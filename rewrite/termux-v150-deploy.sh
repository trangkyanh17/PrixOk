#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

EXPECTED_BRANCH="rewrite/rust-go-ts-v150"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
BUILD_JOBS="${ATRI_BUILD_JOBS:-2}"
HEALTH_TIMEOUT="${ATRI_V150_DEPLOY_HEALTH_TIMEOUT:-330}"
STATE_DIR="$HOST_HOME/.local/state/atri-v150-deploy"
BACKUP_ROOT="$STATE_DIR/backups"
LEGACY_ROOT="$STATE_DIR/legacy-archives"
V150_DIR="$HOST_HOME/.local/lib/atri-v150"
V150_BIN="$V150_DIR/atri-supervisor"
V150_LAUNCHER="$HOST_HOME/atri-v150-production-watchdog.sh"
V150_BOT_WRAPPER="$V150_DIR/prixok-bot-v150.sh"
BOOT_DIR="$HOST_HOME/.termux/boot"
BOOT_HOOK="$BOOT_DIR/20-atri-v150-production.sh"
CURRENT_SHA_FILE="$STATE_DIR/current-sha"
LAST_BACKUP_FILE="$STATE_DIR/last-backup"
LAST_LEGACY_ARCHIVE_FILE="$STATE_DIR/last-legacy-archive"
ACTION="${1:-status}"

usage() {
  cat <<'EOF'
Usage: termux-v150-deploy.sh <command>

Commands:
  status          Read-only production/deploy status.
  install         First managed V150 install from the isolated rewrite clone.
  upgrade         Build and atomically replace an existing healthy V150 runtime.
  rollback        Restore the runtime snapshot saved before the last install/upgrade.
  cleanup-legacy  Archive legacy watchdog/ensure host artifacts and legacy boot hooks.
  restore-legacy  Restore the most recent legacy archive without starting it.
  --self-test     Syntax/constant safety checks used by CI.

The script never updates or resets the live /app Git tree.
EOF
}

positive_int() { [[ "${1:-}" =~ ^[0-9]+$ && "$1" -gt 0 ]]; }

if [[ "$ACTION" == "--self-test" ]]; then
  [[ "$EXPECTED_BRANCH" == "rewrite/rust-go-ts-v150" ]]
  [[ "$V150_BIN" == */.local/lib/atri-v150/atri-supervisor ]]
  [[ "$BOOT_HOOK" == */.termux/boot/20-atri-v150-production.sh ]]
  positive_int "$BUILD_JOBS"
  positive_int "$HEALTH_TIMEOUT"
  for cmd in status install upgrade rollback cleanup-legacy restore-legacy; do
    grep -q "^    $cmd)" "$0"
  done
  if grep -Eq 'git[[:space:]]+(pull|reset|checkout|clean)|update\.py' "$0"; then
    echo "deploy self-test: FAIL (source mutation command found)" >&2
    exit 1
  fi
  echo "deploy self-test: PASS"
  exit 0
fi

case "$ACTION" in
  status|install|upgrade|rollback|cleanup-legacy|restore-legacy) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! positive_int "$BUILD_JOBS" || ! positive_int "$HEALTH_TIMEOUT"; then
  echo "invalid ATRI_BUILD_JOBS/ATRI_V150_DEPLOY_HEALTH_TIMEOUT" >&2
  exit 2
fi

mkdir -p "$STATE_DIR" "$BACKUP_ROOT" "$LEGACY_ROOT"
choose_report_dir() {
  local d
  for d in /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$d" && -w "$d" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  printf '%s\n' "$STATE_DIR"
}
REPORT_DIR="$(choose_report_dir)"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/atri-v150-deploy-${ACTION}-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }
info() { printf '[INFO] %s\n' "$*"; }
pass() { printf '[PASS] %-20s %s\n' "$1" "$2"; }
fail() { printf '[FAIL] %-20s %s\n' "$1" "$2" >&2; return 1; }

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

require_host() {
  if [[ "$HOST_PREFIX" != "/data/data/com.termux/files/usr" ]] || [[ -f /etc/debian_version ]]; then
    fail HOST_CONTEXT "must run from the Termux host"
    return 1
  fi
  command -v proot-distro >/dev/null 2>&1 || { fail HOST_CONTEXT "proot-distro missing"; return 1; }
  command -v tmux >/dev/null 2>&1 || { fail HOST_CONTEXT "tmux missing"; return 1; }
  ROOTFS_DIR="$(find_rootfs || true)"
  [[ -n "$ROOTFS_DIR" ]] || { fail HOST_CONTEXT "isolated Debian clone not found"; return 1; }
  pass HOST_CONTEXT "Termux host rootfs=$ROOTFS_DIR"
}

repo_meta() {
  debian_run "cd '$DEBIAN_CLONE' && printf 'branch=%s\\n' \"\$(git branch --show-current)\" && printf 'head=%s\\n' \"\$(git rev-parse HEAD)\" && if git diff --quiet && git diff --cached --quiet; then echo clean=1; else echo clean=0; fi" 2>/dev/null
}

require_repo() {
  local meta branch head clean
  meta="$(repo_meta || true)"
  printf '%s\n' "$meta"
  branch="$(awk -F= '$1=="branch"{print $2}' <<<"$meta")"
  head="$(awk -F= '$1=="head"{print $2}' <<<"$meta")"
  clean="$(awk -F= '$1=="clean"{print $2}' <<<"$meta")"
  if [[ "$branch" != "$EXPECTED_BRANCH" || ! "$head" =~ ^[0-9a-f]{40}$ || "$clean" != 1 ]]; then
    fail REPO "branch=${branch:-unknown} head=${head:-unknown} clean=${clean:-unknown}"
    return 1
  fi
  REPO_SHA="$head"
  pass REPO "branch=$branch head=$head"
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

local_health_state() {
  if [[ -x "$HOST_HOME/atri-production-local-health.sh" ]] && "$HOST_HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1; then
    echo HEALTHY
  else
    echo UNHEALTHY
  fi
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
  local -a legacy=() v150=()
  deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    mapfile -t legacy < <(legacy_watchdog_pids)
    mapfile -t v150 < <(v150_watchdog_pids)
    if ((${#legacy[@]} == 0 && ${#v150[@]} == 1)); then return 0; fi
    sleep 1
  done
  return 1
}

wait_healthy() {
  local timeout_seconds="$1"
  local deadline
  deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    [[ "$(local_health_state)" == HEALTHY ]] && return 0
    sleep 5
  done
  return 1
}

require_healthy_production() {
  local -a legacy=() v150=()
  local session pane lock health
  mapfile -t legacy < <(legacy_watchdog_pids)
  mapfile -t v150 < <(v150_watchdog_pids)
  session="$(bot_session_state)"
  pane="$(bot_pane_pid || true)"
  lock="$(bot_lock_state || echo UNKNOWN)"
  health="$(local_health_state)"
  printf 'v150=%s legacy=%s session=%s pane=%s lock=%s health=%s\n' \
    "${v150[*]:-none}" "${legacy[*]:-none}" "$session" "${pane:-unknown}" "$lock" "$health"
  if ((${#legacy[@]} != 0)) || [[ "$session" != PRESENT || ! "$pane" =~ ^[0-9]+$ || "$lock" != HELD || "$health" != HEALTHY ]]; then
    fail PRODUCTION "requires legacy=0 and healthy singleton bot"
    return 1
  fi
  HEALTHY_V150_COUNT=${#v150[@]}
  HEALTHY_BOT_PANE="$pane"
  pass PRODUCTION "bot healthy pane=$pane v150=${v150[*]:-none} legacy=0"
}

stop_v150() {
  local -a v150=()
  mapfile -t v150 < <(v150_watchdog_pids)
  if ((${#v150[@]} > 1)); then
    echo "refusing to stop duplicate V150 owners: ${v150[*]}" >&2
    return 1
  fi
  if ((${#v150[@]} == 1)); then
    kill -TERM "${v150[0]}"
    wait_pid_gone "${v150[0]}" 25 || return 1
  fi
}

start_v150() {
  [[ -x "$BOOT_HOOK" ]] || return 1
  ATRI_V150_BOOT_DELAY=0 ATRI_V150_BOOT_START_TIMEOUT=60 "$BOOT_HOOK"
  wait_v150_singleton 70
}

atomic_install() {
  local src="$1" dst="$2" tmp
  tmp="$dst.tmp.$$"
  install -m 700 "$src" "$tmp"
  mv -f "$tmp" "$dst"
}

snapshot_runtime() {
  local tag="$1" backup key path
  backup="$BACKUP_ROOT/${tag}-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$backup/files"
  : >"$backup/manifest.tsv"
  : >"$backup/meta.tsv"
  printf 'repo_sha\t%s\n' "$(cat "$CURRENT_SHA_FILE" 2>/dev/null || echo unknown)" >>"$backup/meta.tsv"
  while IFS='|' read -r key path; do
    if [[ -e "$path" ]]; then
      cp -p "$path" "$backup/files/$key"
      printf '%s\t1\t%s\n' "$key" "$path" >>"$backup/manifest.tsv"
    else
      printf '%s\t0\t%s\n' "$key" "$path" >>"$backup/manifest.tsv"
    fi
  done <<EOF
supervisor|$V150_BIN
watchdog|$V150_LAUNCHER
bot_wrapper|$V150_BOT_WRAPPER
boot_hook|$BOOT_HOOK
EOF
  printf '%s\n' "$backup" >"$LAST_BACKUP_FILE"
  printf '%s\n' "$backup"
}

restore_runtime_files() {
  local backup="$1" key existed path
  [[ -f "$backup/manifest.tsv" ]] || return 1
  while IFS=$'\t' read -r key existed path; do
    if [[ "$existed" == 1 ]]; then
      [[ -f "$backup/files/$key" ]] || return 1
      mkdir -p "$(dirname "$path")"
      atomic_install "$backup/files/$key" "$path"
    else
      rm -f "$path"
    fi
  done <"$backup/manifest.tsv"
}

rollback_from_backup() {
  local backup="$1" previous_sha
  info "rollback from $backup"
  stop_v150 || true
  restore_runtime_files "$backup" || { echo "rollback file restore failed" >&2; return 1; }
  previous_sha="$(awk -F$'\t' '$1=="repo_sha"{print $2}' "$backup/meta.tsv" 2>/dev/null || true)"
  if [[ "$previous_sha" =~ ^[0-9a-f]{40}$ ]]; then printf '%s\n' "$previous_sha" >"$CURRENT_SHA_FILE"; fi
  if [[ -x "$V150_BIN" && -x "$BOOT_HOOK" ]]; then
    start_v150 || return 1
  fi
}

build_candidate() {
  section "BUILD HOST WATCHDOG"
  debian_run "cd '$DEBIAN_CLONE/rewrite' && ATRI_BUILD_JOBS='$BUILD_JOBS' ./termux-build.sh --host-watchdog-only"
  CANDIDATE_BIN="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/target/release/atri-supervisor-android-arm64"
  [[ -f "$CANDIDATE_BIN" ]] || { fail BUILD "host watchdog artifact missing"; return 1; }
  ATRI_REWRITE_WATCHDOG=false ATRI_REWRITE_MCP_LIFECYCLE=false "$CANDIDATE_BIN" >/dev/null 2>&1 || {
    fail BUILD "Android arm64 candidate execution smoke failed"
    return 1
  }
  pass BUILD "candidate_sha256=$(sha256sum "$CANDIDATE_BIN" | awk '{print $1}')"
}

install_sources() {
  local src="$ROOTFS_DIR$DEBIAN_CLONE/rewrite"
  [[ -f "$src/termux-v150-production-watchdog.sh" ]] || return 1
  [[ -f "$src/termux-v150-bot-launcher.sh" ]] || return 1
  [[ -f "$src/termux-v150-boot-hook.sh" ]] || return 1
  mkdir -p "$V150_DIR" "$BOOT_DIR"
  atomic_install "$CANDIDATE_BIN" "$V150_BIN"
  atomic_install "$src/termux-v150-production-watchdog.sh" "$V150_LAUNCHER"
  atomic_install "$src/termux-v150-bot-launcher.sh" "$V150_BOT_WRAPPER"
  atomic_install "$src/termux-v150-boot-hook.sh" "$BOOT_HOOK"
}

validate_installed_sources() {
  local src="$ROOTFS_DIR$DEBIAN_CLONE/rewrite"
  cmp -s "$CANDIDATE_BIN" "$V150_BIN" || return 1
  cmp -s "$src/termux-v150-production-watchdog.sh" "$V150_LAUNCHER" || return 1
  cmp -s "$src/termux-v150-bot-launcher.sh" "$V150_BOT_WRAPPER" || return 1
  cmp -s "$src/termux-v150-boot-hook.sh" "$BOOT_HOOK" || return 1
}

run_deploy_transaction() {
  local pane_before="$1" source_before="$2"
  local pane_after lock_after source_after
  stop_v150 || return 1
  install_sources || return 1
  validate_installed_sources || return 1
  start_v150 || return 1
  wait_healthy "$HEALTH_TIMEOUT" || return 1
  pane_after="$(bot_pane_pid || true)"
  lock_after="$(bot_lock_state || echo UNKNOWN)"
  [[ "$pane_after" == "$pane_before" && "$lock_after" == HELD ]] || return 1
  source_after="$(source_fingerprint || true)"
  [[ "$source_after" == "$source_before" ]] || return 1
}

managed_deploy() {
  local mode="$1" backup source_before pane_before pane_after
  local -a v150=()
  require_host
  require_repo
  require_healthy_production
  mapfile -t v150 < <(v150_watchdog_pids)
  if [[ "$mode" == upgrade && ${#v150[@]} -ne 1 ]]; then
    fail MODE "upgrade requires exactly one active V150 owner"
    return 1
  fi
  if [[ "$mode" == install && ${#v150[@]} -gt 1 ]]; then
    fail MODE "install refuses duplicate V150 owners"
    return 1
  fi
  pane_before="$HEALTHY_BOT_PANE"
  source_before="$(source_fingerprint || true)"
  [[ -n "$source_before" ]] || { fail SOURCE_GUARD "unable to fingerprint production"; return 1; }
  build_candidate
  backup="$(snapshot_runtime "$mode")"
  pass SNAPSHOT "$backup"

  if ! run_deploy_transaction "$pane_before" "$source_before"; then
    fail DEPLOY "new runtime failed invariants; restoring previous snapshot" || true
    if rollback_from_backup "$backup" && wait_healthy "$HEALTH_TIMEOUT"; then
      pass ROLLBACK "previous runtime restored"
    else
      echo "CRITICAL: automatic rollback did not recover healthy runtime" >&2
    fi
    return 1
  fi

  printf '%s\n' "$REPO_SHA" >"$CURRENT_SHA_FILE"
  pane_after="$(bot_pane_pid || true)"
  pass DEPLOY "$mode sha=$REPO_SHA watchdog=$(v150_watchdog_pids | tr '\n' ',' | sed 's/,$//') bot_pane=$pane_after source_unchanged=1"
}

archive_legacy_path() {
  local archive="$1" path="$2" label="$3" dest
  [[ -e "$path" ]] || return 0
  dest="$archive/$label"
  mv "$path" "$dest"
  printf '%s\t%s\n' "$path" "$dest" >>"$archive/manifest.tsv"
}

cleanup_legacy() {
  local archive f base
  local -a v150=() legacy=()
  require_host
  require_repo
  require_healthy_production
  mapfile -t v150 < <(v150_watchdog_pids)
  mapfile -t legacy < <(legacy_watchdog_pids)
  [[ ${#v150[@]} -eq 1 && ${#legacy[@]} -eq 0 ]] || { fail LEGACY_CLEANUP "requires one V150 owner and zero legacy owners"; return 1; }
  [[ -x "$BOOT_HOOK" ]] || { fail LEGACY_CLEANUP "V150 boot hook missing"; return 1; }

  archive="$LEGACY_ROOT/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$archive"
  : >"$archive/manifest.tsv"
  archive_legacy_path "$archive" "$HOST_HOME/atri-production-watchdog.sh" "atri-production-watchdog.sh"
  archive_legacy_path "$archive" "$HOST_HOME/atri-production-ensure.sh" "atri-production-ensure.sh"

  if [[ -d "$BOOT_DIR" ]]; then
    while IFS= read -r -d '' f; do
      [[ "$f" == "$BOOT_HOOK" ]] && continue
      if grep -Eq 'atri-production-watchdog\.sh|atri-production-ensure\.sh|\.atri-production-watchdog' "$f" 2>/dev/null; then
        base="$(basename "$f")"
        archive_legacy_path "$archive" "$f" "boot-$base"
      fi
    done < <(find "$BOOT_DIR" -maxdepth 1 -type f -print0 2>/dev/null)
  fi

  printf '%s\n' "$archive" >"$LAST_LEGACY_ARCHIVE_FILE"
  mapfile -t legacy < <(legacy_watchdog_pids)
  mapfile -t v150 < <(v150_watchdog_pids)
  if ((${#legacy[@]} == 0 && ${#v150[@]} == 1)) && [[ "$(bot_lock_state || echo UNKNOWN)" == HELD && "$(local_health_state)" == HEALTHY ]]; then
    pass LEGACY_CLEANUP "archived=$(wc -l <"$archive/manifest.tsv") files path=$archive; V150 remains sole owner"
  else
    fail LEGACY_CLEANUP "post-cleanup production invariant failed"
    return 1
  fi
}

restore_legacy() {
  local archive path stored
  require_host
  archive="$(cat "$LAST_LEGACY_ARCHIVE_FILE" 2>/dev/null || true)"
  [[ -n "$archive" && -f "$archive/manifest.tsv" ]] || { fail LEGACY_RESTORE "no legacy archive available"; return 1; }
  while IFS=$'\t' read -r path stored; do
    [[ -e "$stored" ]] || continue
    if [[ -e "$path" ]]; then
      echo "refusing to overwrite existing $path" >&2
      return 1
    fi
    mkdir -p "$(dirname "$path")"
    mv "$stored" "$path"
  done <"$archive/manifest.tsv"
  pass LEGACY_RESTORE "restored files from $archive; legacy watchdog was not started"
}

show_status() {
  local meta session pane lock health deployed legacy_files
  local -a legacy=() v150=()
  require_host
  meta="$(repo_meta || true)"
  section "REPO"
  printf '%s\n' "$meta"
  mapfile -t legacy < <(legacy_watchdog_pids)
  mapfile -t v150 < <(v150_watchdog_pids)
  session="$(bot_session_state)"
  pane="$(bot_pane_pid || true)"
  lock="$(bot_lock_state || echo UNKNOWN)"
  health="$(local_health_state)"
  deployed="$(cat "$CURRENT_SHA_FILE" 2>/dev/null || echo unmanaged)"
  legacy_files=0
  [[ -e "$HOST_HOME/atri-production-watchdog.sh" ]] && legacy_files=$((legacy_files + 1))
  [[ -e "$HOST_HOME/atri-production-ensure.sh" ]] && legacy_files=$((legacy_files + 1))
  section "PRODUCTION"
  printf 'deployed_sha=%s\n' "$deployed"
  printf 'v150=%s\n' "${v150[*]:-none}"
  printf 'legacy=%s\n' "${legacy[*]:-none}"
  printf 'session=%s pane=%s lock=%s health=%s\n' "$session" "${pane:-unknown}" "$lock" "$health"
  printf 'legacy_host_artifacts=%s\n' "$legacy_files"
  printf 'boot_hook=%s\n' "$([[ -x "$BOOT_HOOK" ]] && echo PRESENT || echo MISSING)"
  printf 'report=%s\n' "$REPORT"
}

rollback_last() {
  local backup source_before source_after
  local -a legacy=()
  require_host
  backup="$(cat "$LAST_BACKUP_FILE" 2>/dev/null || true)"
  [[ -n "$backup" && -f "$backup/manifest.tsv" ]] || { fail ROLLBACK "no deployment backup available"; return 1; }
  mapfile -t legacy < <(legacy_watchdog_pids)
  [[ ${#legacy[@]} -eq 0 ]] || { fail ROLLBACK "legacy owner active; refusing dual-owner rollback"; return 1; }
  source_before="$(source_fingerprint || true)"
  rollback_from_backup "$backup"
  wait_healthy "$HEALTH_TIMEOUT" || { fail ROLLBACK "restored runtime did not become healthy"; return 1; }
  source_after="$(source_fingerprint || true)"
  [[ "$source_before" == "$source_after" ]] || { fail SOURCE_GUARD "production source fingerprint changed during rollback"; return 1; }
  pass ROLLBACK "restored $backup; production source unchanged"
}

section "ATRI V150 DEPLOY MANAGER"
echo "START: $(date)"
echo "ACTION: $ACTION"
echo "REPORT: $REPORT"

case "$ACTION" in
    status) show_status ;;
    install) managed_deploy install ;;
    upgrade) managed_deploy upgrade ;;
    rollback) rollback_last ;;
    cleanup-legacy) cleanup_legacy ;;
    restore-legacy) restore_legacy ;;
esac

echo "END: $(date)"
echo "REPORT: $REPORT"
