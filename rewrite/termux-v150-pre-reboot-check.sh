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

if [[ "${1:-}" == "--self-test" ]]; then
  [[ "$EXPECTED_BRANCH" == "main" ]]
  bash -n "$0"
  if grep -Eq 'kill[[:space:]]+-|tmux[[:space:]]+kill|git[[:space:]]+(pull|reset|checkout|clean)|update\.py|install[[:space:]]+-m|mv[[:space:]]+-f' "$0"; then
    echo "pre-reboot self-test: FAIL (mutation pattern found)" >&2
    exit 1
  fi
  echo "pre-reboot self-test: PASS"
  exit 0
fi
if (($#)); then
  echo "Usage: $0 [--self-test]" >&2
  exit 2
fi

choose_report_dir() {
  local d
  for d in /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$d" && -w "$d" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  printf '%s\n' "$STATE_DIR"
}
mkdir -p "$STATE_DIR"
REPORT_DIR="$(choose_report_dir)"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/atri-v150-pre-reboot-check-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

ORDER=(HOST_CONTEXT REPO BOOT_PROVIDER FILES_MATCH LEGACY_BOOT_HOOKS WATCHDOG_OWNER BOT_SESSION BOT_LOCK PROD_HEALTH SOURCE_BASELINE BOOT_BASELINE READY_TO_REBOOT)
declare -A RESULTS=() DETAILS=()
FAILURES=0
pass() { local k="$1"; shift; RESULTS["$k"]="PASS"; DETAILS["$k"]="$*"; printf '[PASS] %-20s %s\n' "$k" "$*"; }
fail() { local k="$1"; shift; RESULTS["$k"]="FAIL"; DETAILS["$k"]="$*"; FAILURES=1; printf '[FAIL] %-20s %s\n' "$k" "$*"; }

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
bot_lock_state() {
  debian_run '
set -u
p=/app/.atri-prixok-bot-v133.lock
if [ ! -e "$p" ]; then echo MISSING; exit 0; fi
exec 9<>"$p"
if flock -n 9; then flock -u 9; echo FREE; else echo HELD; fi
' 2>/dev/null | tail -n1 | tr -d '\r'
}
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

printf '===== ATRI V150 PRE-REBOOT CHECK =====\n'
printf 'START: %s\nREPORT: %s\n' "$(date)" "$REPORT"

ROOTFS=""
if [[ "$HOST_PREFIX" == "/data/data/com.termux/files/usr" && -x "$HOST_PREFIX/bin/proot-distro" && -x "$HOST_PREFIX/bin/tmux" && ! -f /etc/debian_version ]]; then
  ROOTFS="$(find_rootfs || true)"
fi
if [[ -n "$ROOTFS" ]]; then pass HOST_CONTEXT "Termux host confirmed rootfs=$ROOTFS"; else fail HOST_CONTEXT "must run from Termux host with Debian clone present"; fi

if [[ -n "$ROOTFS" ]]; then
  meta="$(debian_run "cd '$DEBIAN_CLONE' && printf 'branch=%s\\n' \"\$(git branch --show-current)\" && printf 'head=%s\\n' \"\$(git rev-parse HEAD)\" && if git diff --quiet && git diff --cached --quiet; then echo clean=1; else echo clean=0; fi" 2>/dev/null || true)"
  branch="$(awk -F= '$1=="branch"{print $2}' <<<"$meta")"; head="$(awk -F= '$1=="head"{print $2}' <<<"$meta")"; clean="$(awk -F= '$1=="clean"{print $2}' <<<"$meta")"
  if [[ "$branch" == "$EXPECTED_BRANCH" && "$head" =~ ^[0-9a-f]{40}$ && "$clean" == 1 ]]; then pass REPO "branch=$branch head=$head"; else fail REPO "branch=${branch:-unknown} head=${head:-unknown} clean=${clean:-unknown}"; fi
else
  fail REPO "host context unavailable"
fi

if boot_provider_installed; then pass BOOT_PROVIDER "com.termux.boot detected by Android package manager"; else fail BOOT_PROVIDER "com.termux.boot not detected"; fi

if [[ -n "$ROOTFS" ]]; then
  src="$ROOTFS$DEBIAN_CLONE/rewrite"
  if [[ -x "$BOOT_HOOK" && -x "$V150_LAUNCHER" && -x "$V150_BOT_WRAPPER" ]] && \
     cmp -s "$BOOT_HOOK" "$src/termux-v150-boot-hook.sh" && \
     cmp -s "$V150_LAUNCHER" "$src/termux-v150-production-watchdog.sh" && \
     cmp -s "$V150_BOT_WRAPPER" "$src/termux-v150-bot-launcher.sh"; then
    pass FILES_MATCH "installed boot hook/watchdog launcher/bot wrapper match isolated V150 clone"
  else
    fail FILES_MATCH "installed persistence files missing, non-executable, or differ from V150 clone"
  fi
else
  fail FILES_MATCH "host context unavailable"
fi

legacy_hooks="$(scan_legacy_boot_hooks || true)"
if [[ -z "$legacy_hooks" ]]; then pass LEGACY_BOOT_HOOKS "no legacy watchdog boot hook detected"; else fail LEGACY_BOOT_HOOKS "legacy boot references: $(tr '\n' ' ' <<<"$legacy_hooks")"; fi

mapfile -t legacy < <(legacy_watchdog_pids)
mapfile -t v150 < <(v150_watchdog_pids)
if ((${#legacy[@]} == 0 && ${#v150[@]} == 1)); then pass WATCHDOG_OWNER "v150=${v150[0]} legacy=0"; else fail WATCHDOG_OWNER "v150=${v150[*]:-none} legacy=${legacy[*]:-none}"; fi

if tmux has-session -t prixok-bot 2>/dev/null; then pane="$(tmux list-panes -t prixok-bot -F '#{pane_pid}' 2>/dev/null | head -n1)"; pass BOT_SESSION "prixok-bot present pane=${pane:-unknown}"; else fail BOT_SESSION "prixok-bot missing"; fi
lock="$(bot_lock_state || echo UNKNOWN)"
if [[ "$lock" == HELD ]]; then pass BOT_LOCK "singleton lock held"; else fail BOT_LOCK "lock=$lock"; fi
if "$HOST_HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1; then pass PROD_HEALTH "local health healthy"; else fail PROD_HEALTH "local health unhealthy"; fi

saved_source="$(cat "$SOURCE_BASELINE" 2>/dev/null || true)"; current_source="$(source_fingerprint || true)"
if [[ -n "$saved_source" && "$saved_source" == "$current_source" ]]; then pass SOURCE_BASELINE "production source fingerprint unchanged since persistence phase 1"; else fail SOURCE_BASELINE "source baseline missing or changed"; fi
saved_boot="$(cat "$BOOT_BASELINE" 2>/dev/null || true)"; current_boot="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"
if [[ -n "$saved_boot" && "$saved_boot" == "$current_boot" ]]; then pass BOOT_BASELINE "still on armed pre-reboot boot_id=$current_boot"; else fail BOOT_BASELINE "baseline=${saved_boot:-missing} current=$current_boot (do not reboot again before verification)"; fi

if ((FAILURES == 0)); then pass READY_TO_REBOOT "all persistence prerequisites green; safe to perform one real Android reboot"; else fail READY_TO_REBOOT "one or more prerequisites failed; do not reboot for proof yet"; fi

printf '\n===== FINAL SUMMARY =====\n'
for k in "${ORDER[@]}"; do printf '%-22s %-5s %s\n' "$k" "${RESULTS[$k]:-SKIP}" "${DETAILS[$k]:-not executed}"; done
if ((FAILURES == 0)); then echo 'OVERALL                PASS'; exit 0; fi
echo 'OVERALL                FAIL'
exit 1
