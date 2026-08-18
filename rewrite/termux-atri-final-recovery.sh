#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Final host-side lifecycle recovery for PrixOk/Atri on Termux + Debian PRoot.
# The normal mode is an all-in-one verified deployment/recovery transaction.
# --orphan-recover is an internal fail-closed hook called by the V150 watchdog.

SCRIPT_VERSION="v168.2-final"
DISTRO="${ATRI_PROOT_DISTRO:-debian}"
BOT_SESSION="${ATRI_BOT_SESSION:-prixok-bot}"
BOT_LOCK="${ATRI_BOT_LOCK_PATH:-/app/.atri-prixok-bot-v133.lock}"
SESSION_FILE="${ATRI_TELEGRAM_SESSION_FILE:-/app/8572909267.session}"
HOST_PREFIX="${PREFIX:-}"
HOST_HOME="${HOME:-}"
HOST_BASH="$HOST_PREFIX/bin/bash"
HOST_PYTHON="$HOST_PREFIX/bin/python3"
V150_DIR="$HOST_HOME/.local/lib/atri-v150"
V150_BIN="$V150_DIR/atri-supervisor"
V150_WRAPPER="$HOST_HOME/atri-v150-production-watchdog.sh"
V150_BOT_WRAPPER="$V150_DIR/prixok-bot-v150.sh"
V150_BOOT_HOOK="$HOST_HOME/.termux/boot/20-atri-v150-production.sh"
FINAL_INSTALL="$V150_DIR/termux-atri-final-recovery.sh"
RUNTIME_PROBE="${ATRI_RUNTIME_PROBE:-$V150_DIR/atri-runtime-probe.py}"
OWNER_LOCK="$HOST_HOME/.local/state/atri-v150-wrapper/owner.lock"
LOCAL_HEALTH="$HOST_HOME/atri-production-local-health.sh"
WATCHDOG_LOG="$HOST_HOME/.atri-v150-production-watchdog.log"
STATE_ROOT="$HOST_HOME/.local/state/atri-final-lifecycle"
ORPHAN_LOG="$STATE_ROOT/orphan-recovery.log"
EXPECTED_MAIN_SHA="${ATRI_EXPECTED_MAIN_SHA:-}"
RUNTIME_MUTABLE_TRACKED_PATH="qBittorrent/config/qBittorrent.conf"
STARTUP_TIMEOUT="${ATRI_FINAL_STARTUP_TIMEOUT:-1200}"
RECOVERY_TIMEOUT="${ATRI_FINAL_RECOVERY_TIMEOUT:-420}"
STABILITY_ROUNDS="${ATRI_FINAL_STABILITY_ROUNDS:-10}"
STABILITY_INTERVAL="${ATRI_FINAL_STABILITY_INTERVAL:-6}"
SUPERVISOR_TIMEOUT="${ATRI_FINAL_SUPERVISOR_TIMEOUT:-120}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"

positive_int() { [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]; }
log_line() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

audit_tracked_tree_local() {
  local repo="${1:-}" current="${2:-}" expected="${3:-}"
  local status line path raw old_mode new_mode old_oid new_oid change reported_path
  local runtime_dirty=0 sha mode

  [[ "$current" =~ ^[0-9a-f]{40}$ && "$expected" =~ ^[0-9a-f]{40}$ ]] || {
    echo "TREE_AUDIT_DENY reason=invalid-commit" >&2
    return 40
  }
  [[ "$(git -C "$repo" rev-parse --is-inside-work-tree 2>/dev/null || true)" == true ]] || {
    echo "TREE_AUDIT_DENY reason=not-a-work-tree" >&2
    return 40
  }
  git -C "$repo" cat-file -e "$current^{commit}" 2>/dev/null || {
    echo "TREE_AUDIT_DENY reason=current-commit-missing" >&2
    return 40
  }
  git -C "$repo" cat-file -e "$expected^{commit}" 2>/dev/null || {
    echo "TREE_AUDIT_DENY reason=expected-commit-missing" >&2
    return 40
  }

  status="$(LC_ALL=C git -C "$repo" status --porcelain=v1 --untracked-files=no)" || {
    echo "TREE_AUDIT_DENY reason=status-failed" >&2
    return 41
  }
  if [[ -n "$status" ]]; then
    while IFS= read -r line; do
      [[ "$line" == " M $RUNTIME_MUTABLE_TRACKED_PATH" ]] || {
        printf 'TREE_AUDIT_DENY reason=tracked-change status=%q\n' "$line" >&2
        return 42
      }
      path="$RUNTIME_MUTABLE_TRACKED_PATH"
      [[ -f "$repo/$path" && ! -L "$repo/$path" ]] || {
        echo "TREE_AUDIT_DENY reason=runtime-path-not-regular path=$path" >&2
        return 43
      }
      raw="$(LC_ALL=C git -C "$repo" diff --raw --no-renames -- "$path")" || {
        echo "TREE_AUDIT_DENY reason=runtime-diff-failed path=$path" >&2
        return 43
      }
      [[ -n "$raw" && "$raw" != *$'\n'* ]] || {
        echo "TREE_AUDIT_DENY reason=runtime-diff-ambiguous path=$path" >&2
        return 43
      }
      IFS=$' \t' read -r old_mode new_mode old_oid new_oid change reported_path <<<"$raw"
      [[ "$old_mode" == :100644 && "$new_mode" == 100644 &&
        "$old_oid" =~ ^[0-9a-f]+$ && "$new_oid" =~ ^[0-9a-f]+$ &&
        "$change" == M && "$reported_path" == "$path" ]] || {
        echo "TREE_AUDIT_DENY reason=runtime-change-not-content-only path=$path" >&2
        return 43
      }
      if ! git -C "$repo" cat-file -e "$current:$path" 2>/dev/null ||
        ! git -C "$repo" cat-file -e "$expected:$path" 2>/dev/null; then
        echo "TREE_AUDIT_DENY reason=runtime-path-not-tracked path=$path" >&2
        return 44
      fi
      git -C "$repo" diff --quiet "$current" "$expected" -- "$path" || {
        echo "TREE_AUDIT_DENY reason=runtime-path-changed-upstream path=$path" >&2
        return 44
      }
      sha="$(sha256sum "$repo/$path" | awk '{print $1}')"
      mode="$(stat -c '%a' "$repo/$path")"
      printf 'TREE_AUDIT_RUNTIME_DIRTY path=%s sha256=%s mode=%s\n' "$path" "$sha" "$mode"
      runtime_dirty=$((runtime_dirty + 1))
    done <<<"$status"
  fi
  printf 'TREE_AUDIT_PASS runtime_dirty=%d\n' "$runtime_dirty"
}

tracked_tree_audit_self_test() (
  set -Eeuo pipefail
  local repo="" base expected before after output

  # shellcheck disable=SC2329  # Invoked by the EXIT trap in this subshell.
  cleanup_tree_audit_test() {
    [[ -n "$repo" && -d "$repo" && "$(basename "$repo")" == atri-tree-audit.* ]] || return 0
    rm -rf -- "$repo"
  }
  trap cleanup_tree_audit_test EXIT

  repo="$(mktemp -d "${TMPDIR:-/tmp}/atri-tree-audit.XXXXXX")"
  mkdir -p "$repo/qBittorrent/config" "$repo/bot"
  printf '[Preferences]\nWebUI\\Port=8090\n' >"$repo/$RUNTIME_MUTABLE_TRACKED_PATH"
  printf 'BASE = True\n' >"$repo/bot/source.py"
  git -C "$repo" init -q
  git -C "$repo" add "$RUNTIME_MUTABLE_TRACKED_PATH" bot/source.py
  git -C "$repo" -c user.name=ATRI -c user.email=atri@example.invalid -c commit.gpgsign=false commit -qm base
  base="$(git -C "$repo" rev-parse HEAD)"

  printf 'EXPECTED = True\n' >"$repo/bot/source.py"
  git -C "$repo" add bot/source.py
  git -C "$repo" -c user.name=ATRI -c user.email=atri@example.invalid -c commit.gpgsign=false commit -qm expected
  expected="$(git -C "$repo" rev-parse HEAD)"
  git -C "$repo" switch --detach -q "$base"
  printf 'Runtime\\SessionPort=48123\n' >>"$repo/$RUNTIME_MUTABLE_TRACKED_PATH"

  output="$(audit_tracked_tree_local "$repo" "$base" "$expected")"
  grep -q "TREE_AUDIT_RUNTIME_DIRTY path=$RUNTIME_MUTABLE_TRACKED_PATH" <<<"$output"
  grep -q 'TREE_AUDIT_PASS runtime_dirty=1' <<<"$output"
  before="$(sha256sum "$repo/$RUNTIME_MUTABLE_TRACKED_PATH" | awk '{print $1}')"
  git -C "$repo" merge --ff-only -q "$expected"
  after="$(sha256sum "$repo/$RUNTIME_MUTABLE_TRACKED_PATH" | awk '{print $1}')"
  [[ "$before" == "$after" ]]
  [[ "$(LC_ALL=C git -C "$repo" status --porcelain=v1 --untracked-files=no)" == " M $RUNTIME_MUTABLE_TRACKED_PATH" ]]
  audit_tracked_tree_local "$repo" "$expected" "$expected" >/dev/null

  printf 'DIRTY = True\n' >>"$repo/bot/source.py"
  if audit_tracked_tree_local "$repo" "$expected" "$expected" >/dev/null 2>&1; then
    echo "tracked tree audit self-test: FAIL (source dirt was accepted)" >&2
    return 1
  fi
  echo "tracked tree audit self-test: PASS"
)

self_test() {
  local script_dir probe
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  probe="$script_dir/v156_bot_pid_probe.py"
  bash -n "${BASH_SOURCE[0]}"
  if grep -Eq 'tmux[[:space:]]+kill-session' "${BASH_SOURCE[0]}"; then
    echo "final recovery self-test: FAIL (unsafe tmux teardown found)" >&2
    return 1
  fi
  if grep -Eq 'rm[[:space:]][^[:cntrl:]]*\.atri-prixok-bot[^[:cntrl:]]*lock' "${BASH_SOURCE[0]}"; then
    echo "final recovery self-test: FAIL (lock deletion found)" >&2
    return 1
  fi
  if grep -Eq 'git[[:space:]]+(reset|clean|checkout)[[:space:]]' "${BASH_SOURCE[0]}"; then
    echo "final recovery self-test: FAIL (destructive source command found)" >&2
    return 1
  fi
  if grep -Eq 'local[^#]*timeout="\$[12]"[^#]*deadline=.*timeout' "${BASH_SOURCE[0]}"; then
    echo "final recovery self-test: FAIL (nounset-unsafe local declaration found)" >&2
    return 1
  fi
  for marker in \
    'audit_tracked_tree_local' \
    'RUNTIME_MUTABLE_TRACKED_PATH' \
    'requirements-lifecycle.txt' \
    'LIFECYCLE_TEST_OVERLAY=PASS' \
    'RUNTIME_PYTHON_ENV_UNCHANGED=PASS' \
    'discover_rootfs' \
    'resolve_lock_owner' \
    'orphan_recover_main' \
    'CONTROLLED BOT RECOVERY' \
    'CONTROLLED SUPERVISOR RECOVERY' \
    'FINAL_PRODUCTION_AUDIT=PASS'; do
    grep -q "$marker" "${BASH_SOURCE[0]}"
  done
  if [[ -f "$probe" ]]; then
    python3 -m py_compile "$probe"
  fi
  tracked_tree_audit_self_test
  echo "termux atri final recovery self-test: PASS"
}

guest() {
  proot-distro login "$DISTRO" -- "$@"
}

guest_bash() {
  proot-distro login "$DISTRO" -- bash -lc "$1"
}

run_guest_tree_audit() {
  local repo="$1" current="$2" expected="$3"
  {
    declare -p RUNTIME_MUTABLE_TRACKED_PATH
    declare -f audit_tracked_tree_local
    # shellcheck disable=SC2016  # Positional parameters expand in guest bash.
    printf '%s\n' 'audit_tracked_tree_local "$1" "$2" "$3"'
  } | timeout 60 proot-distro login "$DISTRO" -- bash -s -- "$repo" "$current" "$expected"
}

tmux_has() {
  tmux has-session -t "$1" 2>/dev/null
}

capture_bot() {
  tmux capture-pane -p -S -5000 -t "$BOT_SESSION" 2>/dev/null || true
}

bot_pane_identity() {
  tmux list-panes -t "$BOT_SESSION" -F '#{pane_id}|#{pane_pid}|#{pane_dead}' 2>/dev/null | head -n1 | tr -d '\r'
}

guest_lock_state() {
  local output rc
  set +e
  # shellcheck disable=SC2016  # Expanded by the guest bash, not the host.
  output="$(timeout 30 proot-distro login "$DISTRO" -- bash -lc '
set -u
p=$1
if [[ ! -e "$p" ]]; then echo MISSING; exit 0; fi
command -v flock >/dev/null 2>&1 || exit 20
exec 9<>"$p" || exit 21
if flock -n -E 11 9; then
  flock -u 9 || exit 22
  echo FREE
  exit 0
fi
rc=$?
[[ "$rc" -eq 11 ]] || exit 23
echo HELD
' lock-state "$BOT_LOCK" 2>/dev/null)"
  rc=$?
  set -e
  if ((rc != 0)); then
    echo UNKNOWN
    return 1
  fi
  case "$output" in
    HELD|FREE|MISSING) printf '%s\n' "$output" ;;
    *) echo UNKNOWN; return 1 ;;
  esac
}

guest_identity() {
  timeout 30 proot-distro login "$DISTRO" -- python3 -c '
import os, sys
s = os.stat(sys.argv[1], follow_symlinks=True)
print(f"{s.st_dev}|{s.st_ino}")
' "$1" 2>/dev/null | tail -n1 | tr -d '\r'
}

host_lock_state() {
  local path="$1"
  local rc
  [[ -e "$path" ]] || { echo MISSING; return 0; }
  exec 6<>"$path" || { echo UNKNOWN; return 1; }
  if flock -n -E 11 6; then
    flock -u 6 || true
    exec 6>&-
    echo FREE
    return 0
  fi
  rc=$?
  exec 6>&-
  [[ "$rc" -eq 11 ]] && { echo HELD; return 0; }
  echo UNKNOWN
  return 1
}

probe_command_safe() {
  local arg
  for arg in "$@"; do
    [[ "$arg" =~ ^[A-Za-z0-9_./:=+@,-]+$ ]] || return 1
  done
}

run_probe() {
  local output rc command arg
  [[ -f "$RUNTIME_PROBE" && -x "$HOST_PYTHON" ]] || return 1
  set +e
  output="$(timeout 20 "$HOST_PYTHON" "$RUNTIME_PROBE" "$@" 2>>"$ORPHAN_LOG")"
  rc=$?
  set -e
  if ((rc == 0)); then
    printf '%s\n' "$output"
    return 0
  fi
  command -v su >/dev/null 2>&1 || return 1
  probe_command_safe "$HOST_PYTHON" "$RUNTIME_PROBE" "$@" || return 1
  command="PATH=$HOST_PREFIX/bin:/system/bin:/system/xbin LD_LIBRARY_PATH=$HOST_PREFIX/lib $HOST_PYTHON $RUNTIME_PROBE"
  for arg in "$@"; do command+=" $arg"; done
  set +e
  output="$(timeout 20 su -c "$command" 2>>"$ORPHAN_LOG")"
  rc=$?
  set -e
  ((rc == 0)) || return 1
  printf '%s\n' "$output"
}

resolve_lock_owner() {
  local device="$1"
  local inode="$2"
  run_probe \
    --strategy lock-owner \
    --proc-root /proc \
    --lock-device "$device" \
    --lock-inode "$inode" \
    --expected-uid "$(id -u)" \
    --require-proc-locks \
    --details | tail -n1
}

resolve_wrapper_owner() {
  [[ "$(host_lock_state "$OWNER_LOCK" 2>/dev/null || true)" == HELD ]] || return 1
  run_probe \
    --strategy lock-owner \
    --proc-root /proc \
    --lock-file "$OWNER_LOCK" \
    --expected-uid "$(id -u)" \
    --require-proc-locks \
    --details | tail -n1
}

resolve_supervisor_child() {
  local wrapper_pid="$1"
  run_probe \
    --strategy child-exe \
    --proc-root /proc \
    --parent-pid "$wrapper_pid" \
    --executable "$V150_BIN" \
    --expected-uid "$(id -u)" \
    --details | tail -n1
}

exact_argument_processes() {
  run_probe \
    --strategy argv-exact \
    --proc-root /proc \
    --argument "$1" \
    --expected-uid "$(id -u)" \
    --details
}

same_process_identity() {
  local expected="$1"
  local actual="$2"
  [[ -n "$expected" && "$expected" == "$actual" ]]
}

signal_exact_pid() {
  local signal="$1"
  local details="$2"
  local pid start uid source
  IFS='|' read -r pid start uid source <<<"$details"
  [[ "$pid" =~ ^[1-9][0-9]*$ && "$start" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ "$uid" == "$(id -u)" ]] || return 1
  [[ "$pid" != "$$" && "$pid" != "$PPID" ]] || return 1
  if kill "-$signal" "$pid" 2>/dev/null; then return 0; fi
  command -v su >/dev/null 2>&1 || return 1
  timeout 10 su -c "kill -$signal $pid" >/dev/null 2>&1
}

wait_guest_lock_not_held() {
  local timeout="$1"
  local deadline
  local state
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    state="$(guest_lock_state 2>/dev/null || true)"
    [[ "$state" == FREE || "$state" == MISSING ]] && return 0
    [[ "$state" == UNKNOWN ]] && return 1
    sleep 1
  done
  return 1
}

trim_orphan_log() {
  local bytes
  [[ -f "$ORPHAN_LOG" ]] || return 0
  bytes="$(wc -c <"$ORPHAN_LOG" 2>/dev/null || echo 0)"
  if [[ "$bytes" =~ ^[0-9]+$ && "$bytes" -gt 1048576 ]]; then
    tail -n 2000 "$ORPHAN_LOG" >"$ORPHAN_LOG.tmp.$$" || true
    mv -f "$ORPHAN_LOG.tmp.$$" "$ORPHAN_LOG"
  fi
}

orphan_recover_main() {
  local helper_lock state identity device inode owner before_signal after_term
  local owner_after_term
  mkdir -p "$STATE_ROOT"
  touch "$ORPHAN_LOG"
  trim_orphan_log
  helper_lock="$STATE_ROOT/orphan-helper.lock"
  exec 7>"$helper_lock"
  flock -n 7 || exit 76
  exec >>"$ORPHAN_LOG" 2>&1
  log_line "ORPHAN_RECOVERY_BEGIN session=$BOT_SESSION lock=$BOT_LOCK"

  command -v proot-distro >/dev/null 2>&1 || { log_line "ORPHAN_RECOVERY_FAIL proot-distro-missing"; exit 70; }
  command -v tmux >/dev/null 2>&1 || { log_line "ORPHAN_RECOVERY_FAIL tmux-missing"; exit 70; }
  command -v timeout >/dev/null 2>&1 || { log_line "ORPHAN_RECOVERY_FAIL timeout-missing"; exit 70; }
  [[ -f "$RUNTIME_PROBE" ]] || { log_line "ORPHAN_RECOVERY_FAIL probe-missing"; exit 70; }
  tmux_has "$BOT_SESSION" && { log_line "ORPHAN_RECOVERY_ABORT tmux-present"; exit 75; }
  state="$(guest_lock_state 2>/dev/null || true)"
  if [[ "$state" == FREE || "$state" == MISSING ]]; then
    log_line "ORPHAN_RECOVERY_RACE lock=$state"
    exit 0
  fi
  [[ "$state" == HELD ]] || { log_line "ORPHAN_RECOVERY_FAIL lock=$state"; exit 71; }

  identity="$(guest_identity "$BOT_LOCK" || true)"
  IFS='|' read -r device inode <<<"$identity"
  [[ "$device" =~ ^[0-9]+$ && "$inode" =~ ^[1-9][0-9]*$ ]] || { log_line "ORPHAN_RECOVERY_FAIL identity"; exit 71; }
  owner="$(resolve_lock_owner "$device" "$inode" || true)"
  [[ "$owner" =~ ^[0-9]+\|[0-9]+\|[0-9]+\|proc_locks$ ]] || { log_line "ORPHAN_RECOVERY_FAIL owner-unproven"; exit 72; }

  tmux_has "$BOT_SESSION" && { log_line "ORPHAN_RECOVERY_ABORT tmux-returned"; exit 75; }
  [[ "$(guest_lock_state 2>/dev/null || true)" == HELD ]] || { log_line "ORPHAN_RECOVERY_RACE lock-released"; exit 0; }
  [[ "$(guest_identity "$BOT_LOCK" 2>/dev/null || true)" == "$identity" ]] || { log_line "ORPHAN_RECOVERY_FAIL inode-changed"; exit 72; }
  before_signal="$(resolve_lock_owner "$device" "$inode" || true)"
  same_process_identity "$owner" "$before_signal" || { log_line "ORPHAN_RECOVERY_FAIL owner-changed-before-term"; exit 72; }

  log_line "ORPHAN_RECOVERY_TERM owner=${owner%%|*} evidence=${owner##*|}"
  signal_exact_pid TERM "$owner" || { log_line "ORPHAN_RECOVERY_FAIL term"; exit 73; }
  if wait_guest_lock_not_held 20; then
    log_line "ORPHAN_RECOVERY_PASS signal=TERM"
    exit 0
  fi

  tmux_has "$BOT_SESSION" && { log_line "ORPHAN_RECOVERY_ABORT tmux-returned-after-term"; exit 75; }
  [[ "$(guest_lock_state 2>/dev/null || true)" == HELD ]] || { log_line "ORPHAN_RECOVERY_PASS delayed-release"; exit 0; }
  [[ "$(guest_identity "$BOT_LOCK" 2>/dev/null || true)" == "$identity" ]] || { log_line "ORPHAN_RECOVERY_FAIL inode-changed-after-term"; exit 74; }
  owner_after_term="$(resolve_lock_owner "$device" "$inode" || true)"
  same_process_identity "$owner" "$owner_after_term" || { log_line "ORPHAN_RECOVERY_FAIL owner-changed-after-term"; exit 74; }
  after_term="$owner_after_term"
  log_line "ORPHAN_RECOVERY_KILL owner=${after_term%%|*} evidence=${after_term##*|}"
  signal_exact_pid KILL "$after_term" || { log_line "ORPHAN_RECOVERY_FAIL kill"; exit 74; }
  wait_guest_lock_not_held 10 || { log_line "ORPHAN_RECOVERY_FAIL lock-still-held"; exit 74; }
  log_line "ORPHAN_RECOVERY_PASS signal=KILL"
}

if [[ "${1:-}" == "--self-test" ]]; then
  (($# == 1)) || exit 2
  self_test
  exit 0
fi
if [[ "${1:-}" == "--orphan-recover" ]]; then
  (($# == 1)) || exit 2
  orphan_recover_main
  exit 0
fi
(($# == 0)) || { echo "Usage: bash $0 [--self-test]" >&2; exit 2; }

choose_report_dir() {
  local directory
  for directory in "$HOST_HOME/storage/downloads" /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$directory" && -w "$directory" ]]; then
      printf '%s\n' "$directory"
      return 0
    fi
  done
  printf '%s\n' "$STATE_ROOT"
}

REPORT_DIR="$(choose_report_dir)"
RUN_DIR="$STATE_ROOT/runs/$RUN_ID"
REPORT="$REPORT_DIR/atri-final-recovery-$RUN_ID.txt"
BUNDLE="$REPORT_DIR/atri-final-recovery-$RUN_ID.tar.gz"
BACKUP_DIR="$STATE_ROOT/backups/$RUN_ID"
mkdir -p "$RUN_DIR" "$REPORT_DIR" "$BACKUP_DIR" "$V150_DIR"
: >"$REPORT"
touch "$ORPHAN_LOG"
exec > >(tee -a "$REPORT") 2>&1

PHASE="init"
FINAL_PASS=0
ROOTFS_PATH="UNRESOLVED"
STAGE_DIR=""
CURRENT_HEAD=""
RUNTIME_MUTATED=0
ORPHAN_SUPERVISOR_STOPPED=0
LEGACY_BOOT_MANIFEST="$BACKUP_DIR/legacy-boot-hooks.tsv"

section() { printf '\n===== %s =====\n' "$1"; }
info() { log_line "[INFO] $*"; }
pass() { log_line "[PASS] $*"; }
fatal() { log_line "[FAIL] phase=$PHASE $*"; exit 1; }

audit_production_tree() {
  local label="$1" current="$2" expected="$3"
  local output rc evidence
  [[ "$label" =~ ^[a-z0-9-]+$ ]] || fatal "invalid tree-audit label"
  evidence="$RUN_DIR/tracked-tree-audit-$label.txt"
  set +e
  output="$(run_guest_tree_audit /app "$current" "$expected" 2>&1)"
  rc=$?
  set -e
  printf '%s\n' "$output" | tee "$evidence"
  ((rc == 0)) || fatal "tracked production tree audit failed label=$label rc=$rc"
}

collect_diagnostics() {
  local destination="$RUN_DIR/failure"
  mkdir -p "$destination"
  {
    echo "DATE=$(date)"
    echo "SCRIPT_VERSION=$SCRIPT_VERSION"
    echo "PHASE=$PHASE"
    echo "EXPECTED_MAIN_SHA=$EXPECTED_MAIN_SHA"
    echo "CURRENT_HEAD=$CURRENT_HEAD"
    echo "ROOTFS_PATH=$ROOTFS_PATH"
    echo "BOT_SESSION=$(tmux_has "$BOT_SESSION" && echo PRESENT || echo MISSING)"
    echo "BOT_PANE=$(bot_pane_identity 2>/dev/null || true)"
    echo "BOT_LOCK=$(guest_lock_state 2>/dev/null || true)"
    echo "WRAPPER_OWNER=$(resolve_wrapper_owner 2>/dev/null || true)"
    echo "===== TMUX ====="
    tmux list-panes -a -F 'session=#{session_name} pane=#{pane_id} pid=#{pane_pid} dead=#{pane_dead} cmd=#{pane_current_command}' 2>&1 || true
    echo "===== MEMORY ====="
    free -h 2>&1 || true
    grep -E 'MemAvailable|SwapTotal|SwapFree' /proc/meminfo 2>/dev/null || true
  } >"$destination/host.txt" 2>&1
  capture_bot >"$destination/bot-pane.txt" 2>&1 || true
  tail -n 3000 "$WATCHDOG_LOG" >"$destination/watchdog.log" 2>&1 || true
  tail -n 1000 "$ORPHAN_LOG" >"$destination/orphan-recovery.log" 2>&1 || true
  timeout 30 proot-distro login "$DISTRO" -- bash -lc '
cd /app 2>/dev/null || exit 0
echo "===== GIT ====="
git status --short --branch 2>&1 || true
git rev-parse HEAD 2>&1 || true
echo "===== SESSION METADATA ====="
find -L /app -maxdepth 1 -type f \( -name "*.session" -o -name "*.session-journal" \) -printf "%p|%s|%TY-%Tm-%TdT%TH:%TM:%TS\n" 2>/dev/null || true
echo "===== LOCK METADATA ====="
stat -Lc "%n|dev=%d|ino=%i|size=%s|mode=%a" /app/.atri-prixok-bot-v133.lock 2>&1 || true
' >"$destination/debian.txt" 2>&1 || true
  cp -f "$REPORT" "$RUN_DIR/report.txt" 2>/dev/null || true
}

on_exit() {
  local rc="$1"
  trap - EXIT
  if ((rc != 0 || FINAL_PASS == 0)); then
    collect_diagnostics || true
    if ((RUNTIME_MUTATED == 1)); then
      if rollback_runtime_and_restart; then
        log_line "[ROLLBACK] previous runtime and boot-hook topology restored"
      else
        log_line "[ROLLBACK] FAILED; evidence retained in bundle"
      fi
    fi
    cp -f "$REPORT" "$RUN_DIR/report.txt" 2>/dev/null || true
    tar -C "$RUN_DIR" -czf "$BUNDLE" . 2>/dev/null || true
    echo "ATRI_FINAL_RECOVERY=FAIL"
    echo "REPORT=$REPORT"
    echo "BUNDLE=$BUNDLE"
  fi
  if [[ -n "$STAGE_DIR" ]]; then
    timeout 60 proot-distro login "$DISTRO" -- rm -rf -- "$STAGE_DIR" >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap 'on_exit $?' EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || fatal "missing command: $1"
}

session_metadata() {
  timeout 30 proot-distro login "$DISTRO" -- python3 -c '
import os, sys
p = sys.argv[1]
s = os.stat(p, follow_symlinks=True)
assert s.st_size > 0
print(f"{p}|dev={s.st_dev}|ino={s.st_ino}|size={s.st_size}")
' "$SESSION_FILE" 2>/dev/null | tail -n1 | tr -d '\r'
}

health_ok() {
  [[ -x "$LOCAL_HEALTH" ]] && timeout 60 "$LOCAL_HEALTH" --quiet >/dev/null 2>&1
}

bot_ready() {
  local pane
  tmux_has "$BOT_SESSION" || return 1
  pane="$(capture_bot)"
  grep -q 'Bot Started!' <<<"$pane" && grep -q 'ATRI_PRODUCTION_WORKER_V133_ONLINE' <<<"$pane"
}

wait_bot_ready() {
  local timeout="$1"
  local deadline
  local pane_id=""
  local current pane flood last_notice=0
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    if tmux_has "$BOT_SESSION"; then
      current="$(bot_pane_identity || true)"
      if [[ -z "$pane_id" ]]; then
        pane_id="$current"
      elif [[ "$current" != "$pane_id" ]]; then
        fatal "bot pane restart storm while waiting READY: $pane_id -> $current"
      fi
      if bot_ready; then return 0; fi
      pane="$(capture_bot)"
      flood="$(grep -E 'TELEGRAM_BOT_START_FLOOD_WAIT|FloodWait|FLOOD_WAIT' <<<"$pane" | tail -n1 || true)"
      if ((SECONDS - last_notice >= 15)); then
        if [[ -n "$flood" ]]; then
          info "Telegram FloodWait retained in-process; pane=$pane_id"
        else
          info "waiting bot READY pane=$pane_id"
        fi
        last_notice=$SECONDS
      fi
    fi
    sleep 2
  done
  return 1
}

wait_recovered_bot() {
  local old_pane="$1"
  local timeout="$2"
  local deadline
  local candidate="" current pane flood last_notice=0
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    if tmux_has "$BOT_SESSION"; then
      current="$(bot_pane_identity || true)"
      if [[ -n "$current" && "$current" != "$old_pane" ]]; then
        if [[ -z "$candidate" ]]; then candidate="$current"; fi
        [[ "$current" == "$candidate" ]] || fatal "recovered bot restarted again: $candidate -> $current"
        if bot_ready; then
          printf '%s\n' "$candidate"
          return 0
        fi
        pane="$(capture_bot)"
        flood="$(grep -E 'TELEGRAM_BOT_START_FLOOD_WAIT|FloodWait|FLOOD_WAIT' <<<"$pane" | tail -n1 || true)"
        if ((SECONDS - last_notice >= 15)); then
          if [[ -n "$flood" ]]; then
            info "recovery FloodWait stays in pane=$candidate"
          else
            info "waiting recovered bot pane=$candidate"
          fi
          last_notice=$SECONDS
        fi
      fi
    fi
    sleep 2
  done
  return 1
}

discover_rootfs() {
  local identity_file="$RUN_DIR/rootfs-probe.txt"
  local launcher device inode candidate host_identity
  : >"$identity_file"
  guest python3 -c '
import os, time
s = os.stat("/", follow_symlinks=True)
print(f"{s.st_dev}|{s.st_ino}", flush=True)
time.sleep(8)
' >"$identity_file" 2>/dev/null &
  launcher=$!
  for _ in $(seq 1 30); do
    [[ -s "$identity_file" ]] && break
    sleep 0.2
  done
  IFS='|' read -r device inode <"$identity_file" || true
  if [[ "$device" =~ ^[0-9]+$ && "$inode" =~ ^[1-9][0-9]*$ ]]; then
    candidate="$(run_probe --strategy rootfs --proc-root /proc --root-device "$device" --root-inode "$inode" --expected-uid "$(id -u)" 2>/dev/null | tail -n1 || true)"
  fi
  wait "$launcher" 2>/dev/null || true
  [[ -n "$candidate" && -d "$candidate" ]] || return 1
  host_identity="$("$HOST_PYTHON" -c 'import os,sys; s=os.stat(sys.argv[1]); print(f"{s.st_dev}|{s.st_ino}")' "$candidate" 2>/dev/null || true)"
  [[ -n "${device:-}" && "$host_identity" == "$device|$inode" ]] || return 1
  printf '%s\n' "$candidate"
}

wait_wrapper_owner() {
  local timeout="$1"
  local deadline
  local details
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    details="$(resolve_wrapper_owner 2>/dev/null || true)"
    [[ "$details" =~ ^[0-9]+\|[0-9]+\|[0-9]+\|proc_locks$ ]] && { printf '%s\n' "$details"; return 0; }
    sleep 1
  done
  return 1
}

wait_supervisor_child() {
  local wrapper_pid="$1"
  local old_details="${2:-}"
  local timeout="$3"
  local deadline details
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    details="$(resolve_supervisor_child "$wrapper_pid" 2>/dev/null || true)"
    if [[ "$details" =~ ^[0-9]+\|[0-9]+\|[0-9]+\|parent_exe$ && "$details" != "$old_details" ]]; then
      printf '%s\n' "$details"
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_exact_wrapper() {
  local details="$1"
  local current pid deadline
  current="$(resolve_wrapper_owner 2>/dev/null || true)"
  same_process_identity "$details" "$current" || return 1
  signal_exact_pid TERM "$details" || return 1
  IFS='|' read -r pid _ <<<"$details"
  deadline=$((SECONDS + 30))
  while ((SECONDS < deadline)); do
    [[ "$(host_lock_state "$OWNER_LOCK" 2>/dev/null || true)" != HELD ]] && return 0
    current="$(resolve_wrapper_owner 2>/dev/null || true)"
    [[ -z "$current" ]] && return 0
    same_process_identity "$details" "$current" || return 1
    sleep 1
  done
  return 1
}

atomic_install() {
  local source="$1"
  local destination="$2"
  local mode="$3"
  local temporary="$destination.tmp.$$"
  mkdir -p "$(dirname "$destination")" || return 1
  install -m "$mode" "$source" "$temporary" || return 1
  mv -f "$temporary" "$destination" || return 1
}

backup_runtime() {
  local key path
  : >"$BACKUP_DIR/manifest.tsv" || return 1
  while IFS='|' read -r key path; do
    if [[ -e "$path" ]]; then
      cp -p "$path" "$BACKUP_DIR/$key" || return 1
      printf '%s\t1\t%s\n' "$key" "$path" >>"$BACKUP_DIR/manifest.tsv" || return 1
    else
      printf '%s\t0\t%s\n' "$key" "$path" >>"$BACKUP_DIR/manifest.tsv" || return 1
    fi
  done <<EOF
supervisor|$V150_BIN
watchdog|$V150_WRAPPER
bot-wrapper|$V150_BOT_WRAPPER
boot-hook|$V150_BOOT_HOOK
final-script|$FINAL_INSTALL
runtime-probe|$V150_DIR/atri-runtime-probe.py
EOF
}

restore_runtime() {
  local key existed path
  [[ -f "$BACKUP_DIR/manifest.tsv" ]] || return 1
  while IFS=$'\t' read -r key existed path; do
    if [[ "$existed" == 1 ]]; then
      atomic_install "$BACKUP_DIR/$key" "$path" "$(stat -c '%a' "$BACKUP_DIR/$key")" || return 1
    else
      rm -f -- "$path" || return 1
    fi
  done <"$BACKUP_DIR/manifest.tsv"
}

inventory_legacy_boot_hooks() {
  local file
  LEGACY_BOOT_HOOKS=()
  [[ -d "$HOST_HOME/.termux/boot" ]] || return 0
  while IFS= read -r -d '' file; do
    [[ "$file" == "$V150_BOOT_HOOK" ]] && continue
    if grep -Fq "$HOST_HOME/atri-production-watchdog.sh" "$file" 2>/dev/null ||
       grep -Fq "$HOST_HOME/atri-production-ensure.sh" "$file" 2>/dev/null; then
      [[ "$file" != *$'\t'* && "$file" != *$'\n'* ]] || return 1
      LEGACY_BOOT_HOOKS+=("$file")
    fi
  done < <(find "$HOST_HOME/.termux/boot" -maxdepth 1 -type f -print0 2>/dev/null)
}

retire_legacy_boot_hooks() {
  local index=0 original retired retired_dir
  retired_dir="$STATE_ROOT/retired-boot-hooks/$RUN_ID"
  : >"$LEGACY_BOOT_MANIFEST" || return 1
  ((${#LEGACY_BOOT_HOOKS[@]} == 0)) && return 0
  mkdir -p "$retired_dir" || return 1
  for original in "${LEGACY_BOOT_HOOKS[@]}"; do
    [[ -f "$original" && "$(dirname "$original")" == "$HOST_HOME/.termux/boot" ]] || return 1
    index=$((index + 1))
    retired="$retired_dir/$(printf '%03d' "$index")-$(basename "$original")"
    [[ ! -e "$retired" ]] || return 1
    printf '%s\t%s\n' "$original" "$retired" >>"$LEGACY_BOOT_MANIFEST" || return 1
    mv "$original" "$retired" || return 1
  done
}

restore_legacy_boot_hooks() {
  local original retired
  [[ -f "$LEGACY_BOOT_MANIFEST" ]] || return 0
  while IFS=$'\t' read -r original retired; do
    [[ -n "$original" && -n "$retired" ]] || continue
    [[ -e "$retired" ]] || continue
    [[ ! -e "$original" ]] || return 1
    mkdir -p "$(dirname "$original")" || return 1
    mv "$retired" "$original" || return 1
  done <"$LEGACY_BOOT_MANIFEST"
}

rollback_runtime_and_restart() {
  local current lock_state restored_wrapper restored_pid restored_supervisor
  lock_state="$(host_lock_state "$OWNER_LOCK" 2>/dev/null || true)"
  case "$lock_state" in
    HELD)
      current="$(resolve_wrapper_owner 2>/dev/null || true)"
      [[ -n "$current" ]] || return 1
      stop_exact_wrapper "$current" || return 1
      ;;
    FREE|MISSING) ;;
    *) return 1 ;;
  esac
  restore_runtime || return 1
  restore_legacy_boot_hooks || return 1
  RUNTIME_PROBE="$RUN_DIR/candidate-probe.py"
  if [[ -n "${wrapper_before:-}" || "$ORPHAN_SUPERVISOR_STOPPED" == 1 ]]; then
    start_wrapper >/dev/null
    restored_wrapper="$(wait_wrapper_owner 60 || true)"
    [[ -n "$restored_wrapper" ]] || return 1
    IFS='|' read -r restored_pid _ <<<"$restored_wrapper"
    restored_supervisor="$(wait_supervisor_child "$restored_pid" "" 90 || true)"
    [[ -n "$restored_supervisor" ]] || return 1
  fi
  RUNTIME_MUTATED=0
}

install_candidates() {
  atomic_install "$RUN_DIR/candidate-supervisor" "$V150_BIN" 700 || return 1
  atomic_install "$RUN_DIR/candidate-watchdog.sh" "$V150_WRAPPER" 700 || return 1
  atomic_install "$RUN_DIR/candidate-bot-wrapper.sh" "$V150_BOT_WRAPPER" 700 || return 1
  atomic_install "$RUN_DIR/candidate-boot-hook.sh" "$V150_BOOT_HOOK" 700 || return 1
  atomic_install "$RUN_DIR/candidate-final.sh" "$FINAL_INSTALL" 700 || return 1
  atomic_install "$RUN_DIR/candidate-probe.py" "$V150_DIR/atri-runtime-probe.py" 600 || return 1
  RUNTIME_PROBE="$V150_DIR/atri-runtime-probe.py"
}

start_wrapper() {
  nohup "$HOST_BASH" "$V150_WRAPPER" >>"$WATCHDOG_LOG" 2>&1 < /dev/null &
  printf '%s\n' "$!"
}

verify_no_legacy_owner() {
  local legacy="$HOST_HOME/atri-production-watchdog.sh"
  local pids
  if ! pids="$(exact_argument_processes "$legacy" 2>/dev/null)"; then
    fatal "legacy watchdog ownership probe failed closed"
  fi
  [[ -z "$pids" ]] || fatal "legacy watchdog active with exact argv evidence: $(cut -d'|' -f1 <<<"$pids" | tr '\n' ',')"
}

verify_no_legacy_boot_hook() {
  local file
  [[ -d "$HOST_HOME/.termux/boot" ]] || return 0
  while IFS= read -r -d '' file; do
    [[ "$file" == "$V150_BOOT_HOOK" ]] && continue
    if grep -Fq "$HOST_HOME/atri-production-watchdog.sh" "$file" 2>/dev/null ||
       grep -Fq "$HOST_HOME/atri-production-ensure.sh" "$file" 2>/dev/null; then
      fatal "legacy boot hook remains: $file"
    fi
  done < <(find "$HOST_HOME/.termux/boot" -maxdepth 1 -type f -print0 2>/dev/null)
}

stability_check() {
  local label="$1"
  local expected_bot="$2"
  local expected_wrapper="$3"
  local expected_supervisor="$4"
  local round wrapper_pid current_supervisor
  IFS='|' read -r wrapper_pid _ <<<"$expected_wrapper"
  for ((round=1; round<=STABILITY_ROUNDS; round++)); do
    sleep "$STABILITY_INTERVAL"
    [[ "$(bot_pane_identity 2>/dev/null || true)" == "$expected_bot" ]] || fatal "$label bot changed round=$round"
    [[ "$(resolve_wrapper_owner 2>/dev/null || true)" == "$expected_wrapper" ]] || fatal "$label wrapper changed round=$round"
    current_supervisor="$(resolve_supervisor_child "$wrapper_pid" 2>/dev/null || true)"
    [[ "$current_supervisor" == "$expected_supervisor" ]] || fatal "$label supervisor changed round=$round"
    [[ "$(guest_lock_state 2>/dev/null || true)" == HELD ]] || fatal "$label lock not HELD round=$round"
    bot_ready || fatal "$label bot not ONLINE round=$round"
    [[ -n "$(session_metadata 2>/dev/null || true)" ]] || fatal "$label persistent session missing round=$round"
    health_ok || fatal "$label health failed round=$round"
    printf '[CHECK %02d/%02d] %s bot=%s supervisor=%s\n' "$round" "$STABILITY_ROUNDS" "$label" "${expected_bot%%|*}" "${expected_supervisor%%|*}"
  done
}

section "ATRI FINAL PRODUCTION LIFECYCLE"
echo "START=$(date)"
echo "RUN_ID=$RUN_ID"
echo "REPORT=$REPORT"
echo "POLICY=fail-closed; no PID/path/rootfs guesses; no lock deletion"

PHASE="host-preflight"
[[ -n "$HOST_PREFIX" && -n "$HOST_HOME" ]] || fatal "Termux PREFIX/HOME are required"
for command in proot-distro tmux flock git tar timeout sha256sum seq install nohup find; do require_command "$command"; done
[[ -x "$HOST_BASH" && -x "$HOST_PYTHON" ]] || fatal "Termux bash/python missing"
[[ ! -f /etc/debian_version ]] || fatal "must run from Termux host, not Debian guest"
[[ "$EXPECTED_MAIN_SHA" =~ ^[0-9a-f]{40}$ ]] || fatal "ATRI_EXPECTED_MAIN_SHA must be exact merged main SHA"
for value in "$STARTUP_TIMEOUT" "$RECOVERY_TIMEOUT" "$STABILITY_ROUNDS" "$STABILITY_INTERVAL" "$SUPERVISOR_TIMEOUT"; do
  positive_int "$value" || fatal "invalid positive integer: $value"
done
exec 8>"$STATE_ROOT/final-run.lock"
flock -n 8 || fatal "another final recovery run is active"
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock >/dev/null 2>&1 || true
pass "HOST_PREFLIGHT"

PHASE="exact-main-fetch"
guest git -C /app fetch --quiet origin main || fatal "git fetch origin main failed"
origin_main="$(guest git -C /app rev-parse origin/main | tail -n1 | tr -d '\r')"
[[ "$origin_main" == "$EXPECTED_MAIN_SHA" ]] || fatal "origin/main=$origin_main expected=$EXPECTED_MAIN_SHA"
branch="$(guest git -C /app branch --show-current | tail -n1 | tr -d '\r')"
CURRENT_HEAD="$(guest git -C /app rev-parse HEAD | tail -n1 | tr -d '\r')"
app_real="$(guest readlink -f /app | tail -n1 | tr -d '\r')"
remote_url="$(guest git -C /app remote get-url origin | tail -n1 | tr -d '\r')"
[[ "$branch" == main ]] || fatal "live branch=$branch, expected main"
[[ "$app_real" == /home/prix/PrixOk ]] || fatal "/app resolves to $app_real"
case "$remote_url" in
  https://github.com/trangkyanh17/PrixOk|https://github.com/trangkyanh17/PrixOk.git|git@github.com:trangkyanh17/PrixOk.git|ssh://git@github.com/trangkyanh17/PrixOk.git) ;;
  *) fatal "unexpected origin remote" ;;
esac
guest git -C /app merge-base --is-ancestor "$CURRENT_HEAD" "$EXPECTED_MAIN_SHA" || fatal "live main is not an ancestor of expected main"
audit_production_tree exact-main "$CURRENT_HEAD" "$EXPECTED_MAIN_SHA"
guest git -C /app show "$EXPECTED_MAIN_SHA:rewrite/v156_bot_pid_probe.py" >"$RUN_DIR/candidate-probe.py" || fatal "cannot extract runtime probe"
"$HOST_PYTHON" -m py_compile "$RUN_DIR/candidate-probe.py" || fatal "runtime probe compile failed"
RUNTIME_PROBE="$RUN_DIR/candidate-probe.py"
pass "EXACT_MAIN_FETCH current=$CURRENT_HEAD expected=$EXPECTED_MAIN_SHA"

PHASE="rootfs-discovery"
ROOTFS_PATH="$(discover_rootfs || true)"
[[ -n "$ROOTFS_PATH" && -d "$ROOTFS_PATH" ]] || fatal "physical Debian rootfs could not be proven"
pass "ROOTFS_DISCOVERED=$ROOTFS_PATH"

PHASE="isolated-regression"
STAGE_DIR="$(guest mktemp -d /tmp/atri-final-stage.XXXXXX | tail -n1 | tr -d '\r')"
[[ "$STAGE_DIR" =~ ^/tmp/atri-final-stage\.[A-Za-z0-9]+$ ]] || fatal "invalid staging directory"
guest git clone --quiet --shared /app "$STAGE_DIR" || fatal "isolated clone failed"
guest git -C "$STAGE_DIR" switch --quiet --detach "$EXPECTED_MAIN_SHA" || fatal "isolated checkout failed"
# shellcheck disable=SC2016  # Expanded by the guest bash, not the Termux host.
test_python="$(guest bash -lc 'p=/app/mltbenv/bin/python; if [[ -x "$p" ]] && "$p" -m pip --version >/dev/null 2>&1 && "$p" -c "import pyrogram, uvloop" >/dev/null 2>&1; then echo "$p"; fi' | tail -n1 | tr -d '\r')"
[[ "$test_python" == /app/mltbenv/bin/python ]] || fatal "production Python/pip/runtime imports unavailable"
guest_bash "
set -Eeuo pipefail
cd '$STAGE_DIR'
mkdir -p '$STAGE_DIR/.test-state'
runtime_env_before=\"\$(env -u PYTHONPATH '$test_python' -m pip freeze --all | LC_ALL=C sort | sha256sum | awk '{print \$1}')\"
PIP_CACHE_DIR='$STAGE_DIR/.lifecycle-pip-cache' \\
  '$test_python' -m pip install \\
    --disable-pip-version-check \\
    --no-input \\
    --no-compile \\
    --only-binary=:all: \\
    --retries 3 \\
    --timeout 30 \\
    --target '$STAGE_DIR/.lifecycle-deps' \\
    -r '$STAGE_DIR/requirements-lifecycle.txt'
export PYTHONPATH='$STAGE_DIR/.lifecycle-deps'
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export ATRI_PROVIDER_CONTROL_STATE_PATH='$STAGE_DIR/.test-state/atri_provider_control.json'
'$test_python' - <<'PY'
import os
from importlib.metadata import version

import httpx
import pytest
import pytest_asyncio
import socksio

expected = {
    'pytest': '9.1.1',
    'pytest-asyncio': '1.4.0',
    'httpx': '0.28.1',
    'socksio': '1.0.0',
}
actual = {name: version(name) for name in expected}
if actual != expected:
    raise SystemExit(f'lifecycle dependency mismatch: {actual}')
deps = os.path.realpath('$STAGE_DIR/.lifecycle-deps') + os.sep
for module in (pytest, pytest_asyncio, httpx, socksio):
    if not os.path.realpath(module.__file__).startswith(deps):
        raise SystemExit(f'non-isolated lifecycle dependency: {module.__name__}')
print('LIFECYCLE_TEST_OVERLAY=PASS')
PY
git diff --check
while IFS= read -r -d '' file; do bash -n \"\$file\"; done < <(find . -type f -name '*.sh' -not -path './.git/*' -print0)
bash rewrite/termux-atri-final-recovery.sh --self-test
bash rewrite/termux-v150-production-watchdog.sh --self-test
bash rewrite/termux-v150-bot-launcher.sh --self-test
bash rewrite/termux-v150-boot-hook.sh --self-test
python3 -m py_compile rewrite/v156_bot_pid_probe.py
$test_python -m pytest -q -m 'not slow'
for run in \$(seq 1 10); do
  echo \"PYTHON_LIFECYCLE_REGRESSION=\$run/10\"
  $test_python -m pytest -q \
    tests/test_atri_final_lifecycle.py \
    tests/test_atri_telegram_lifecycle_v1674.py \
    tests/test_v1563_kernel_lock_owner.py
  bash rewrite/termux-atri-final-recovery.sh --self-test
done
runtime_env_after=\"\$(env -u PYTHONPATH '$test_python' -m pip freeze --all | LC_ALL=C sort | sha256sum | awk '{print \$1}')\"
[[ \"\$runtime_env_before\" == \"\$runtime_env_after\" ]]
echo RUNTIME_PYTHON_ENV_UNCHANGED=PASS
cd rewrite/supervisor
[[ -z \"\$(gofmt -l .)\" ]]
go vet ./...
go test ./...
go test -count=10 -run 'TestWatchdog|TestSupervisor' ./...
mkdir -p ../target/release
CGO_ENABLED=0 GOOS=android GOARCH=arm64 go build -trimpath -ldflags='-s -w' -o ../target/release/atri-supervisor-android-arm64 .
" || fatal "isolated regression/build failed"
pass "ISOLATED_REGRESSION full=1 lifecycle=10/10"

PHASE="candidate-extraction"
guest cat "$STAGE_DIR/rewrite/target/release/atri-supervisor-android-arm64" >"$RUN_DIR/candidate-supervisor"
guest cat "$STAGE_DIR/rewrite/termux-v150-production-watchdog.sh" >"$RUN_DIR/candidate-watchdog.sh"
guest cat "$STAGE_DIR/rewrite/termux-v150-bot-launcher.sh" >"$RUN_DIR/candidate-bot-wrapper.sh"
guest cat "$STAGE_DIR/rewrite/termux-v150-boot-hook.sh" >"$RUN_DIR/candidate-boot-hook.sh"
guest cat "$STAGE_DIR/rewrite/termux-atri-final-recovery.sh" >"$RUN_DIR/candidate-final.sh"
chmod 700 "$RUN_DIR/candidate-supervisor" "$RUN_DIR/candidate-watchdog.sh" "$RUN_DIR/candidate-bot-wrapper.sh" "$RUN_DIR/candidate-boot-hook.sh" "$RUN_DIR/candidate-final.sh"
chmod 600 "$RUN_DIR/candidate-probe.py"
cmp -s "${BASH_SOURCE[0]}" "$RUN_DIR/candidate-final.sh" || fatal "downloaded script does not match exact main"
bash -n "$RUN_DIR/candidate-watchdog.sh" "$RUN_DIR/candidate-bot-wrapper.sh" "$RUN_DIR/candidate-boot-hook.sh" "$RUN_DIR/candidate-final.sh"
"$RUN_DIR/candidate-watchdog.sh" --self-test
"$RUN_DIR/candidate-bot-wrapper.sh" --self-test
"$RUN_DIR/candidate-boot-hook.sh" --self-test
pass "CANDIDATES_EXTRACTED supervisor_sha256=$(sha256sum "$RUN_DIR/candidate-supervisor" | awk '{print $1}')"

PHASE="production-topology"
verify_no_legacy_owner
inventory_legacy_boot_hooks || fatal "legacy boot-hook inventory failed closed"
info "legacy boot hooks scheduled for reversible retirement: ${#LEGACY_BOOT_HOOKS[@]}"
[[ -x "$HOST_HOME/prixok-bot.sh" ]] || fatal "canonical bot launcher missing"
bash -n "$HOST_HOME/prixok-bot.sh" || fatal "canonical bot launcher syntax failed"
[[ -x "$LOCAL_HEALTH" ]] || fatal "local health helper missing"
bash -n "$LOCAL_HEALTH" || fatal "local health helper syntax failed"
session_before="$(session_metadata || true)"
[[ -n "$session_before" ]] || fatal "persistent Telegram session missing or empty: $SESSION_FILE"
lock_state="$(guest_lock_state 2>/dev/null || true)"
[[ "$lock_state" != UNKNOWN ]] || fatal "bot singleton lock state is UNKNOWN"
if ! tmux_has "$BOT_SESSION" && [[ "$lock_state" == HELD ]]; then
  info "current topology=tmux-missing+lock-held; invoking exact kernel-owner rescue"
  ATRI_RUNTIME_PROBE="$RUNTIME_PROBE" "$RUN_DIR/candidate-final.sh" --orphan-recover || fatal "verified orphan recovery failed"
  wait_guest_lock_not_held 15 || fatal "orphan lock remained held"
  lock_state="$(guest_lock_state 2>/dev/null || true)"
fi
if tmux_has "$BOT_SESSION" && [[ "$lock_state" != HELD ]]; then
  info "bot tmux present without proven lock; waiting for startup convergence"
  wait_bot_ready 90 || fatal "tmux present but worker lock/READY did not converge"
  [[ "$(guest_lock_state 2>/dev/null || true)" == HELD ]] || fatal "bot tmux has no singleton owner"
fi
if tmux_has "$BOT_SESSION"; then
  wait_bot_ready "$STARTUP_TIMEOUT" || fatal "existing bot did not reach READY"
fi
wrapper_lock_state="$(host_lock_state "$OWNER_LOCK" 2>/dev/null || true)"
case "$wrapper_lock_state" in
  HELD)
    wrapper_before="$(resolve_wrapper_owner 2>/dev/null || true)"
    [[ -n "$wrapper_before" ]] || fatal "wrapper lock is HELD but kernel owner is unproven"
    ;;
  FREE|MISSING) wrapper_before="" ;;
  *) fatal "wrapper owner lock state is UNKNOWN" ;;
esac
if ! orphan_supervisors="$(exact_argument_processes "$V150_BIN" 2>/dev/null)"; then
  fatal "supervisor topology probe failed closed"
fi
if [[ -z "$wrapper_before" && -n "$orphan_supervisors" ]]; then
  count="$(awk 'NF{n++} END{print n+0}' <<<"$orphan_supervisors")"
  [[ "$count" == 1 ]] || fatal "multiple unowned supervisors: $count"
  info "one exact unowned supervisor will be retired during runtime transaction"
fi
pass "TOPOLOGY session=$(tmux_has "$BOT_SESSION" && echo PRESENT || echo MISSING) lock=$lock_state wrapper=${wrapper_before%%|*}"

PHASE="source-fast-forward"
audit_production_tree pre-fast-forward "$CURRENT_HEAD" "$EXPECTED_MAIN_SHA"
guest git -C /app merge --ff-only "$EXPECTED_MAIN_SHA" || fatal "production main fast-forward failed"
CURRENT_HEAD="$(guest git -C /app rev-parse HEAD | tail -n1 | tr -d '\r')"
[[ "$CURRENT_HEAD" == "$EXPECTED_MAIN_SHA" ]] || fatal "production source did not reach expected main"
[[ -n "$(session_metadata 2>/dev/null || true)" ]] || fatal "persistent session lost during source fast-forward"
pass "SOURCE_FAST_FORWARD=$CURRENT_HEAD"

PHASE="runtime-transaction"
backup_runtime
RUNTIME_MUTATED=1
retire_legacy_boot_hooks || fatal "legacy boot-hook retirement failed"
if [[ -n "$wrapper_before" ]]; then
  stop_exact_wrapper "$wrapper_before" || fatal "existing wrapper did not stop gracefully"
fi
if [[ -z "$wrapper_before" && -n "$orphan_supervisors" ]]; then
  current_orphan="$(exact_argument_processes "$V150_BIN" 2>/dev/null | tail -n1)" || fatal "orphan supervisor revalidation failed"
  same_process_identity "$current_orphan" "$orphan_supervisors" || fatal "orphan supervisor identity changed"
  signal_exact_pid TERM "$current_orphan" || fatal "cannot stop exact orphan supervisor"
  for _ in $(seq 1 30); do
    remaining_orphans="$(exact_argument_processes "$V150_BIN" 2>/dev/null)" || fatal "orphan supervisor post-TERM probe failed"
    [[ -z "$remaining_orphans" ]] && break
    same_process_identity "$current_orphan" "$remaining_orphans" || fatal "orphan supervisor identity changed after TERM"
    sleep 1
  done
  if [[ -n "$remaining_orphans" ]]; then
    revalidated_orphan="$(exact_argument_processes "$V150_BIN" 2>/dev/null | tail -n1)" || fatal "orphan supervisor KILL revalidation failed"
    same_process_identity "$current_orphan" "$revalidated_orphan" || fatal "orphan supervisor identity changed before KILL"
    signal_exact_pid KILL "$revalidated_orphan" || fatal "cannot KILL exact orphan supervisor"
    sleep 2
    remaining_orphans="$(exact_argument_processes "$V150_BIN" 2>/dev/null)" || fatal "orphan supervisor final probe failed"
    [[ -z "$remaining_orphans" ]] || fatal "orphan supervisor remained alive"
  fi
  ORPHAN_SUPERVISOR_STOPPED=1
fi
install_candidates || fatal "candidate installation failed; automatic rollback will run"
requested_wrapper="$(start_wrapper)"
wrapper_after="$(wait_wrapper_owner 60 || true)"
if [[ -z "$wrapper_after" ]]; then
  fatal "new wrapper did not acquire exact owner lock; automatic rollback will run"
fi
IFS='|' read -r wrapper_pid _ <<<"$wrapper_after"
supervisor_after="$(wait_supervisor_child "$wrapper_pid" "" 90 || true)"
if [[ -z "$supervisor_after" ]]; then
  fatal "new supervisor child not proven; automatic rollback will run"
fi
pass "RUNTIME_TRANSACTION wrapper=${wrapper_after%%|*} supervisor=${supervisor_after%%|*} requested=$requested_wrapper"

PHASE="bot-online"
if ! tmux_has "$BOT_SESSION"; then
  wait_bot_ready "$STARTUP_TIMEOUT" || fatal "watchdog did not restore bot"
else
  wait_bot_ready "$STARTUP_TIMEOUT" || fatal "bot failed READY after runtime transaction"
fi
bot_before="$(bot_pane_identity || true)"
[[ -n "$bot_before" ]] || fatal "bot pane identity missing"
[[ "$(guest_lock_state 2>/dev/null || true)" == HELD ]] || fatal "bot lock not HELD"
health_ok || fatal "production health failed"
[[ -n "$(session_metadata 2>/dev/null || true)" ]] || fatal "persistent session missing"
pass "BOT_ONLINE pane=${bot_before%%|*}"

PHASE="pre-stability"
stability_check PRE "$bot_before" "$wrapper_after" "$supervisor_after"
pass "PRE_STABILITY=$STABILITY_ROUNDS/$STABILITY_ROUNDS"

section "CONTROLLED BOT RECOVERY"
PHASE="controlled-bot-recovery"
lock_identity_before="$(guest_identity "$BOT_LOCK" || true)"
IFS='|' read -r lock_device lock_inode <<<"$lock_identity_before"
old_lock_owner="$(resolve_lock_owner "$lock_device" "$lock_inode" || true)"
[[ -n "$old_lock_owner" ]] || fatal "current bot lock owner not proven"
tmux send-keys -t "$BOT_SESSION" C-c || fatal "cannot deliver SIGINT through bot PTY"
bot_after="$(wait_recovered_bot "$bot_before" "$RECOVERY_TIMEOUT" || true)"
[[ -n "$bot_after" ]] || fatal "bot did not recover to a single new pane"
[[ "$(guest_identity "$BOT_LOCK" || true)" == "$lock_identity_before" ]] || fatal "singleton lock inode changed"
new_lock_owner="$(resolve_lock_owner "$lock_device" "$lock_inode" || true)"
[[ -n "$new_lock_owner" && "$new_lock_owner" != "$old_lock_owner" ]] || fatal "kernel lock owner did not rotate"
recovered_log="$(capture_bot)"
! grep -q 'ImportBotAuthorization' <<<"$recovered_log" || fatal "Telegram bot authorization was repeated"
! grep -q 'ATRI_PRODUCTION_WORKER_V133_DUPLICATE_BLOCKED' <<<"$recovered_log" || fatal "duplicate bot worker was blocked during recovery"
session_after="$(session_metadata || true)"
[[ -n "$session_after" ]] || fatal "persistent session missing after bot recovery"
health_ok || fatal "health failed after bot recovery"
pass "BOT_RECOVERY ${bot_before%%|*}->${bot_after%%|*} owner=${old_lock_owner%%|*}->${new_lock_owner%%|*} persistent=PASS"

section "CONTROLLED SUPERVISOR RECOVERY"
PHASE="controlled-supervisor-recovery"
wrapper_current="$(resolve_wrapper_owner || true)"
[[ "$wrapper_current" == "$wrapper_after" ]] || fatal "wrapper identity changed before supervisor test"
IFS='|' read -r wrapper_pid _ <<<"$wrapper_current"
supervisor_before_test="$(resolve_supervisor_child "$wrapper_pid" || true)"
[[ "$supervisor_before_test" == "$supervisor_after" ]] || fatal "supervisor identity changed before controlled test"
bot_before_supervisor="$(bot_pane_identity || true)"
revalidated_supervisor="$(resolve_supervisor_child "$wrapper_pid" || true)"
same_process_identity "$supervisor_before_test" "$revalidated_supervisor" || fatal "supervisor PID reused before signal"
signal_exact_pid TERM "$revalidated_supervisor" || fatal "cannot TERM exact supervisor child"
supervisor_final="$(wait_supervisor_child "$wrapper_pid" "$supervisor_before_test" "$SUPERVISOR_TIMEOUT" || true)"
[[ -n "$supervisor_final" ]] || fatal "wrapper did not respawn supervisor"
[[ "$(resolve_wrapper_owner || true)" == "$wrapper_after" ]] || fatal "wrapper changed during supervisor recovery"
[[ "$(bot_pane_identity || true)" == "$bot_before_supervisor" ]] || fatal "healthy bot restarted during supervisor-only recovery"
bot_ready || fatal "bot not ONLINE after supervisor recovery"
health_ok || fatal "health failed after supervisor recovery"
pass "SUPERVISOR_RECOVERY ${supervisor_before_test%%|*}->${supervisor_final%%|*} bot_unchanged=${bot_before_supervisor%%|*}"

PHASE="post-stability"
stability_check POST "$bot_after" "$wrapper_after" "$supervisor_final"
pass "POST_STABILITY=$STABILITY_ROUNDS/$STABILITY_ROUNDS"

PHASE="final-audit"
verify_no_legacy_owner
verify_no_legacy_boot_hook
[[ "$(guest git -C /app rev-parse HEAD | tail -n1 | tr -d '\r')" == "$EXPECTED_MAIN_SHA" ]] || fatal "final source SHA drift"
audit_production_tree final "$EXPECTED_MAIN_SHA" "$EXPECTED_MAIN_SHA"
[[ "$(resolve_wrapper_owner || true)" == "$wrapper_after" ]] || fatal "final wrapper drift"
[[ "$(resolve_supervisor_child "$wrapper_pid" || true)" == "$supervisor_final" ]] || fatal "final supervisor drift"
[[ "$(bot_pane_identity || true)" == "$bot_after" ]] || fatal "final bot pane drift"
[[ "$(guest_lock_state 2>/dev/null || true)" == HELD ]] || fatal "final bot lock not HELD"
bot_ready || fatal "final bot not ONLINE"
health_ok || fatal "final health failed"
[[ -n "$(session_metadata 2>/dev/null || true)" ]] || fatal "final persistent session missing"
RUNTIME_MUTATED=0
FINAL_PASS=1
pass "FINAL_PRODUCTION_AUDIT=PASS"
echo "MAIN_SHA=$EXPECTED_MAIN_SHA"
echo "ATRI_FINAL_RECOVERY=PASS"
echo "REPORT=$REPORT"
