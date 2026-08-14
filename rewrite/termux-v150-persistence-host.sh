#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

EXPECTED_BRANCH="main"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
STATE_DIR="$HOST_HOME/.cache/atri-v150-persistence"
V150_INSTALL_DIR="$HOST_HOME/.local/lib/atri-v150"
V150_BIN="$V150_INSTALL_DIR/atri-supervisor"
V150_LAUNCHER="$HOST_HOME/atri-v150-production-watchdog.sh"
V150_BOT_WRAPPER="$V150_INSTALL_DIR/prixok-bot-v150.sh"
BOOT_DIR="$HOST_HOME/.termux/boot"
BOOT_HOOK="$BOOT_DIR/20-atri-v150-production.sh"
SOURCE_BASELINE="$STATE_DIR/source-baseline.txt"
BOOT_BASELINE="$STATE_DIR/baseline-boot-id"
REPORT_MODE="install-soft-test"
POST_REBOOT=0
SELF_TEST=0

case "${1:-}" in
  "") ;;
  --self-test) SELF_TEST=1 ;;
  --post-reboot-verify) POST_REBOOT=1; REPORT_MODE="post-reboot-verify" ;;
  *) echo "Usage: $0 [--self-test|--post-reboot-verify]" >&2; exit 2 ;;
esac

positive_int() { [[ "$1" =~ ^[0-9]+$ ]]; }

if ((SELF_TEST == 1)); then
  [[ "$EXPECTED_BRANCH" == "main" ]]
  [[ "$BOOT_HOOK" == */.termux/boot/20-atri-v150-production.sh ]]
  for f in "$0" "$(dirname "$0")/termux-v150-boot-hook.sh" "$(dirname "$0")/termux-v150-bot-launcher.sh"; do
    bash -n "$f"
  done
  grep -q 'ATRI_PRODUCTION_LAUNCHER_GUARD=1' "$(dirname "$0")/termux-v150-bot-launcher.sh"
  if grep -Eq 'atri-production-watchdog\.sh[^[:cntrl:]]*(nohup|exec|bash)' "$(dirname "$0")/termux-v150-boot-hook.sh"; then
    echo "persistence self-test: FAIL (legacy watchdog launch found)" >&2
    exit 1
  fi
  echo "persistence self-test: PASS"
  exit 0
fi

mkdir -p "$STATE_DIR"
choose_report_dir() {
  local d
  for d in /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$d" && -w "$d" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  printf '%s\n' "$STATE_DIR"
}
REPORT_DIR="$(choose_report_dir)"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/atri-v150-production-persistence-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

RESULT_ORDER=(HOST_CONTEXT REPO BOOT_PROVIDER LEGACY_BOOT_HOOKS HOOK_INSTALL PRE_STATE SOFT_FAILOVER SINGLETON_REPLAY SOURCE_UNCHANGED REBOOT_PROOF FINAL_STATE)
declare -A RESULTS=()
declare -A DETAILS=()
OVERALL_FAIL=0

pass() { local k="$1"; shift; RESULTS["$k"]="PASS"; DETAILS["$k"]="$*"; printf '[PASS] %-19s %s\n' "$k" "$*"; }
fail() { local k="$1"; shift; RESULTS["$k"]="FAIL"; DETAILS["$k"]="$*"; OVERALL_FAIL=1; printf '[FAIL] %-19s %s\n' "$k" "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
section() { printf '\n===== %s =====\n' "$1"; }

find_rootfs() {
  local d
  for d in \
    "$HOST_PREFIX/var/lib/proot-distro/containers/debian/rootfs" \
    "$HOST_PREFIX/var/lib/proot-distro/installed-rootfs/debian"; do
    if [[ -d "$d$DEBIAN_CLONE/rewrite" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  return 1
}

debian_run() { proot-distro login debian -- bash -lc "$1"; }

boot_provider_installed() {
  if [[ -x /system/bin/cmd ]] && /system/bin/cmd package list packages 2>/dev/null | grep -qx 'package:com.termux.boot'; then return 0; fi
  if [[ -x /system/bin/pm ]] && /system/bin/pm path com.termux.boot 2>/dev/null | grep -q '^package:'; then return 0; fi
  return 1
}

legacy_watchdog_pids() { pgrep -af '[a]tri-production-watchdog.sh' 2>/dev/null | awk 'NF{print $1}' | sort -n; }
v150_watchdog_pids() { pgrep -af "$V150_BIN" 2>/dev/null | awk 'NF{print $1}' | sort -n; }
bot_session_state() { if tmux has-session -t prixok-bot 2>/dev/null; then echo PRESENT; else echo MISSING; fi; }
bot_pane_pid() { tmux list-panes -t prixok-bot -F '#{pane_pid}' 2>/dev/null | head -n1; }
bot_lock_state() {
  debian_run '
set -u
p=/app/.atri-prixok-bot-v133.lock
if [ ! -e "$p" ]; then echo MISSING; exit 0; fi
exec 9<>"$p"
if flock -n 9; then flock -u 9; echo FREE; else echo HELD; fi
' 2>/dev/null | tail -n1 | tr -d '\r'
}
local_health_state() { if "$HOST_HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1; then echo HEALTHY; else echo UNHEALTHY; fi; }

source_fingerprint() {
  debian_run '
set -Eeuo pipefail
cd /app
printf "branch=%s\n" "$(git branch --show-current 2>/dev/null || true)"
printf "head=%s\n" "$(git rev-parse HEAD 2>/dev/null || true)"
for f in start.sh bot/__main__.py bot/modules/atri_ai.py; do
  if [ -f "$f" ]; then sha256sum "$f"; else echo "MISSING $f"; fi
done
' 2>/dev/null
}

wait_pid_gone() {
  local pid="$1"
  local timeout_seconds="$2"
  local deadline
  deadline=$((SECONDS + timeout_seconds))
  while kill -0 "$pid" 2>/dev/null; do
    ((SECONDS < deadline)) || return 1
    sleep 1
  done
}

wait_v150_singleton() {
  local timeout_seconds="$1"
  local deadline
  deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    mapfile -t legacy < <(legacy_watchdog_pids)
    mapfile -t v150 < <(v150_watchdog_pids)
    if ((${#legacy[@]} == 0 && ${#v150[@]} == 1)); then return 0; fi
    sleep 1
  done
  return 1
}

scan_legacy_boot_hooks() {
  local f found=0
  [[ -d "$BOOT_DIR" ]] || return 1
  while IFS= read -r -d '' f; do
    [[ "$f" == "$BOOT_HOOK" ]] && continue
    if grep -Eq 'atri-production-watchdog\.sh|\.atri-production-watchdog' "$f" 2>/dev/null; then
      printf '%s\n' "$f"
      found=1
    fi
  done < <(find "$BOOT_DIR" -maxdepth 1 -type f -print0 2>/dev/null)
  ((found == 1))
}

print_summary() {
  section "FINAL SUMMARY"
  local k
  for k in "${RESULT_ORDER[@]}"; do
    printf '%-21s %-7s %s\n' "$k" "${RESULTS[$k]:-SKIP}" "${DETAILS[$k]:-not executed}"
  done
  if ((OVERALL_FAIL != 0)); then
    echo "OVERALL               FAIL"
  elif ((POST_REBOOT == 0)) && [[ "${RESULTS[REBOOT_PROOF]:-}" == PENDING ]]; then
    echo "OVERALL               SOFT_PASS_REBOOT_PENDING"
  else
    echo "OVERALL               PASS"
  fi
  echo "END: $(date)"
  echo "REPORT: $REPORT"
  section "PERSISTENCE LOG TAIL"
  tail -100 "$HOST_HOME/.atri-v150-persistence.log" 2>/dev/null || true
  section "V150 WATCHDOG LOG TAIL"
  tail -100 "$HOST_HOME/.atri-v150-production-watchdog.log" 2>/dev/null || true
}
trap print_summary EXIT

echo "===== ATRI V150 PRODUCTION PERSISTENCE / FAILOVER ====="
echo "START: $(date)"
echo "MODE: $REPORT_MODE"
echo "REPORT: $REPORT"

ROOTFS_DIR=""
if [[ "$HOST_PREFIX" != "/data/data/com.termux/files/usr" ]] || [[ ! -x "$HOST_PREFIX/bin/proot-distro" ]] || [[ ! -x "$HOST_PREFIX/bin/tmux" ]] || [[ -f /etc/debian_version ]]; then
  fail HOST_CONTEXT "must run from Termux host"
else
  ROOTFS_DIR="$(find_rootfs || true)"
  if [[ -n "$ROOTFS_DIR" ]]; then pass HOST_CONTEXT "Termux host confirmed rootfs=$ROOTFS_DIR"; else fail HOST_CONTEXT "Debian rootfs/clone not found"; fi
fi

if [[ "${RESULTS[HOST_CONTEXT]:-FAIL}" == PASS ]]; then
  section "REPO"
  meta="$(debian_run "cd '$DEBIAN_CLONE' && printf 'branch=%s\\n' \"\$(git branch --show-current)\" && printf 'head=%s\\n' \"\$(git rev-parse HEAD)\" && if git diff --quiet && git diff --cached --quiet; then echo clean=1; else echo clean=0; fi" 2>/dev/null || true)"
  printf '%s\n' "$meta"
  branch="$(awk -F= '$1=="branch"{print $2}' <<<"$meta")"
  head="$(awk -F= '$1=="head"{print $2}' <<<"$meta")"
  clean="$(awk -F= '$1=="clean"{print $2}' <<<"$meta")"
  if [[ "$branch" == "$EXPECTED_BRANCH" && "$head" =~ ^[0-9a-f]{40}$ && "$clean" == 1 ]]; then pass REPO "branch=$branch head=$head"; else fail REPO "branch=${branch:-unknown} head=${head:-unknown} clean=${clean:-unknown}"; fi
fi

if boot_provider_installed; then
  pass BOOT_PROVIDER "Termux:Boot package com.termux.boot detected"
else
  fail BOOT_PROVIDER "Termux:Boot package com.termux.boot not detected; reboot autostart cannot be proven"
fi

mkdir -p "$BOOT_DIR" "$V150_INSTALL_DIR"
legacy_hooks="$(scan_legacy_boot_hooks || true)"
if [[ -z "$legacy_hooks" ]]; then
  pass LEGACY_BOOT_HOOKS "no legacy watchdog boot hook detected"
else
  fail LEGACY_BOOT_HOOKS "legacy watchdog references found: $(tr '\n' ' ' <<<"$legacy_hooks")"
fi

if [[ "${RESULTS[REPO]:-FAIL}" == PASS ]]; then
  section "STAGE PERSISTENCE FILES"
  src="$ROOTFS_DIR$DEBIAN_CLONE/rewrite"
  if [[ -x "$V150_BIN" && -f "$src/termux-v150-production-watchdog.sh" && -f "$src/termux-v150-bot-launcher.sh" && -f "$src/termux-v150-boot-hook.sh" ]]; then
    install -m 700 "$src/termux-v150-production-watchdog.sh" "$V150_LAUNCHER.tmp"
    mv -f "$V150_LAUNCHER.tmp" "$V150_LAUNCHER"
    install -m 700 "$src/termux-v150-bot-launcher.sh" "$V150_BOT_WRAPPER.tmp"
    mv -f "$V150_BOT_WRAPPER.tmp" "$V150_BOT_WRAPPER"
    if [[ -e "$BOOT_HOOK" ]]; then cp -f "$BOOT_HOOK" "$STATE_DIR/boot-hook-before-$(date +%Y%m%d-%H%M%S).bak"; fi
    install -m 700 "$src/termux-v150-boot-hook.sh" "$BOOT_HOOK.tmp"
    mv -f "$BOOT_HOOK.tmp" "$BOOT_HOOK"
    pass HOOK_INSTALL "V150 launcher wrapper + Termux:Boot hook staged"
  else
    fail HOOK_INSTALL "required V150 runtime/source file missing"
  fi
fi

CURRENT_BOOT_ID="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"

if ((POST_REBOOT == 1)); then
  section "POST-REBOOT PROOF"
  baseline_boot="$(cat "$BOOT_BASELINE" 2>/dev/null || true)"
  hook_boot="$(cat "$STATE_DIR/last_hook_boot_id" 2>/dev/null || true)"
  if [[ -n "$baseline_boot" && "$CURRENT_BOOT_ID" != unknown && "$CURRENT_BOOT_ID" != "$baseline_boot" && "$hook_boot" == "$CURRENT_BOOT_ID" ]]; then
    pass REBOOT_PROOF "boot id changed and Termux:Boot hook ran on current boot id=$CURRENT_BOOT_ID"
  else
    fail REBOOT_PROOF "baseline=${baseline_boot:-missing} current=$CURRENT_BOOT_ID hook=${hook_boot:-missing}"
  fi

  mapfile -t legacy < <(legacy_watchdog_pids)
  mapfile -t v150 < <(v150_watchdog_pids)
  session="$(bot_session_state)"; lock="$(bot_lock_state || echo UNKNOWN)"; health="$(local_health_state)"
  if ((${#legacy[@]} == 0 && ${#v150[@]} == 1)) && [[ "$session" == PRESENT && "$lock" == HELD && "$health" == HEALTHY ]]; then
    pass FINAL_STATE "v150=${v150[0]} legacy=0 session=$session lock=$lock health=$health"
  else
    fail FINAL_STATE "v150=${v150[*]:-none} legacy=${legacy[*]:-none} session=$session lock=$lock health=$health"
  fi
  baseline_source="$(cat "$SOURCE_BASELINE" 2>/dev/null || true)"
  current_source="$(source_fingerprint || true)"
  if [[ -n "$baseline_source" && "$current_source" == "$baseline_source" ]]; then pass SOURCE_UNCHANGED "production source fingerprint preserved across reboot"; else fail SOURCE_UNCHANGED "production source fingerprint changed or baseline missing"; fi
  exit "$OVERALL_FAIL"
fi

section "PRE-STATE"
mapfile -t legacy < <(legacy_watchdog_pids)
mapfile -t v150 < <(v150_watchdog_pids)
session="$(bot_session_state)"; pane_before="$(bot_pane_pid || true)"; lock="$(bot_lock_state || echo UNKNOWN)"; health="$(local_health_state)"
printf 'v150=%s legacy=%s session=%s pane=%s lock=%s health=%s\n' "${v150[*]:-none}" "${legacy[*]:-none}" "$session" "${pane_before:-unknown}" "$lock" "$health"
if ((${#legacy[@]} == 0 && ${#v150[@]} == 1)) && [[ "$session" == PRESENT && "$pane_before" =~ ^[0-9]+$ && "$lock" == HELD && "$health" == HEALTHY ]]; then
  pass PRE_STATE "V150 owns watchdog; production bot healthy"
else
  fail PRE_STATE "expected exactly one V150 owner and healthy production"
fi

SOURCE_BEFORE="$(source_fingerprint || true)"
printf '%s\n' "$SOURCE_BEFORE" >"$SOURCE_BASELINE"
printf '%s\n' "$CURRENT_BOOT_ID" >"$BOOT_BASELINE"

if [[ "${RESULTS[PRE_STATE]:-FAIL}" == PASS && "${RESULTS[HOOK_INSTALL]:-FAIL}" == PASS && "${RESULTS[LEGACY_BOOT_HOOKS]:-FAIL}" == PASS ]]; then
  section "SOFT WATCHDOG FAILOVER"
  old_pid="${v150[0]}"
  kill -TERM "$old_pid" 2>/dev/null || true
  if wait_pid_gone "$old_pid" 20; then
    if ATRI_V150_BOOT_DELAY=1 ATRI_V150_BOOT_START_TIMEOUT=30 "$BOOT_HOOK" && wait_v150_singleton 35; then
      mapfile -t v150_after < <(v150_watchdog_pids)
      pane_after="$(bot_pane_pid || true)"; lock_after="$(bot_lock_state || echo UNKNOWN)"; health_after="$(local_health_state)"
      if [[ "$pane_after" == "$pane_before" && "$lock_after" == HELD && "$health_after" == HEALTHY ]]; then
        pass SOFT_FAILOVER "watchdog restarted via boot hook old=$old_pid new=${v150_after[0]}; bot pane unchanged"
      else
        fail SOFT_FAILOVER "watchdog restarted but bot invariant changed pane=$pane_after lock=$lock_after health=$health_after"
      fi
    else
      fail SOFT_FAILOVER "boot hook did not restore one V150 watchdog"
    fi
  else
    fail SOFT_FAILOVER "old V150 watchdog pid=$old_pid did not stop"
  fi
fi

if [[ "${RESULTS[SOFT_FAILOVER]:-FAIL}" == PASS ]]; then
  mapfile -t before_replay < <(v150_watchdog_pids)
  if ATRI_V150_BOOT_DELAY=0 "$BOOT_HOOK"; then
    sleep 2
    mapfile -t after_replay < <(v150_watchdog_pids)
    if ((${#after_replay[@]} == 1)) && [[ "${after_replay[0]}" == "${before_replay[0]}" ]]; then pass SINGLETON_REPLAY "second boot-hook invocation kept same watchdog pid=${after_replay[0]}"; else fail SINGLETON_REPLAY "watchdog ownership changed/duplicated after replay"; fi
  else
    fail SINGLETON_REPLAY "second boot-hook invocation returned non-zero"
  fi
fi

SOURCE_AFTER="$(source_fingerprint || true)"
if [[ -n "$SOURCE_BEFORE" && "$SOURCE_AFTER" == "$SOURCE_BEFORE" ]]; then pass SOURCE_UNCHANGED "production branch/head/core source hashes unchanged"; else fail SOURCE_UNCHANGED "production source fingerprint changed"; fi

if [[ "${RESULTS[BOOT_PROVIDER]:-FAIL}" == PASS && "${RESULTS[HOOK_INSTALL]:-FAIL}" == PASS && "${RESULTS[SOFT_FAILOVER]:-FAIL}" == PASS && "${RESULTS[SINGLETON_REPLAY]:-FAIL}" == PASS ]]; then
  RESULTS[REBOOT_PROOF]="PENDING"
  DETAILS[REBOOT_PROOF]="armed; actual Android reboot required, then run --post-reboot-verify"
  info "REBOOT_PROOF=PENDING actual reboot is required to prove Android boot delivery"
else
  RESULTS[REBOOT_PROOF]="SKIP"
  DETAILS[REBOOT_PROOF]="prerequisites not all green"
fi

mapfile -t final_legacy < <(legacy_watchdog_pids)
mapfile -t final_v150 < <(v150_watchdog_pids)
final_session="$(bot_session_state)"; final_lock="$(bot_lock_state || echo UNKNOWN)"; final_health="$(local_health_state)"
if ((${#final_legacy[@]} == 0 && ${#final_v150[@]} == 1)) && [[ "$final_session" == PRESENT && "$final_lock" == HELD && "$final_health" == HEALTHY ]]; then
  pass FINAL_STATE "v150=${final_v150[0]} legacy=0 session=$final_session lock=$final_lock health=$final_health"
else
  fail FINAL_STATE "v150=${final_v150[*]:-none} legacy=${final_legacy[*]:-none} session=$final_session lock=$final_lock health=$final_health"
fi

exit "$OVERALL_FAIL"
