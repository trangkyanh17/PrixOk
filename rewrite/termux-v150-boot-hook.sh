#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"

LAUNCHER="$HOST_HOME/atri-v150-production-watchdog.sh"
STATE_DIR="$HOST_HOME/.cache/atri-v150-persistence"
WRAPPER_STATE_DIR="$HOST_HOME/.local/state/atri-v150-wrapper"
WRAPPER_LOCK="$WRAPPER_STATE_DIR/owner.lock"
LOG="$HOST_HOME/.atri-v150-persistence.log"
LOCK_FILE="$STATE_DIR/boot-hook.lock"
DELAY="${ATRI_V150_BOOT_DELAY:-20}"
START_TIMEOUT="${ATRI_V150_BOOT_START_TIMEOUT:-60}"

nonnegative_int() { [[ "${1:-}" =~ ^[0-9]+$ ]]; }
positive_int() { [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]; }

lock_fd_state() {
  local fd="$1"
  local rc
  if flock -n -E 11 "$fd"; then
    flock -u "$fd" || return 22
    echo FREE
    return 0
  else
    # Capture flock itself here; $? after fi would be the compound-if status.
    rc=$?
    [[ "$rc" -eq 11 ]] || return 23
    echo HELD
    return 0
  fi
}

wrapper_lock_state() {
  local state
  mkdir -p "$WRAPPER_STATE_DIR" || return 1
  exec 8>"$WRAPPER_LOCK" || return 1
  if ! state="$(lock_fd_state 8)"; then
    exec 8>&-
    echo UNKNOWN
    return 1
  fi
  exec 8>&-
  case "$state" in
    FREE|HELD) printf '%s\n' "$state" ;;
    *) echo UNKNOWN; return 1 ;;
  esac
}

lock_state_self_test() (
  set -Eeuo pipefail
  local directory state
  directory="$(mktemp -d "${TMPDIR:-/tmp}/atri-boot-lock.XXXXXX")"
  trap 'rm -f -- "$WRAPPER_LOCK"; rmdir -- "$directory" 2>/dev/null || true' EXIT
  WRAPPER_STATE_DIR="$directory"
  WRAPPER_LOCK="$directory/owner.lock"
  : >"$WRAPPER_LOCK"
  exec 7<>"$WRAPPER_LOCK"
  flock -x 7
  state="$(wrapper_lock_state)"
  [[ "$state" == HELD ]]
  flock -u 7
  exec 7>&-
  state="$(wrapper_lock_state)"
  [[ "$state" == FREE ]]
  echo "v150 boot lock state self-test: PASS"
)

if [[ "${1:-}" == "--self-test" ]]; then
  (($# == 1)) || exit 2
  bash -n "${BASH_SOURCE[0]}"
  nonnegative_int 0
  positive_int 1
  grep -q 'WRAPPER_LOCK=.*owner.lock' "${BASH_SOURCE[0]}"
  grep -q 'flock -n -E 11' "${BASH_SOURCE[0]}"
  grep -q '9>&-' "${BASH_SOURCE[0]}"
  lock_state_self_test
  echo "v150 boot hook self-test: PASS"
  exit 0
fi
(($# == 0)) || { echo "Usage: $0 [--self-test]" >&2; exit 2; }

export HOME="$HOST_HOME"
export PREFIX="$HOST_PREFIX"
export PATH="$HOST_PREFIX/bin:/system/bin:/system/xbin"
export TMPDIR="$HOST_PREFIX/tmp"
export LD_LIBRARY_PATH="$HOST_PREFIX/lib"

mkdir -p "$STATE_DIR" "$WRAPPER_STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  printf '%s\n' "LOCK_BUSY" >"$STATE_DIR/last_result"
  printf '%s boot_hook_lock_busy\n' "$(date '+%F %T')" >>"$LOG"
  exit 77
fi

if ! nonnegative_int "$DELAY" || ! positive_int "$START_TIMEOUT"; then
  printf '%s invalid boot timing delay=%s timeout=%s\n' "$(date '+%F %T')" "$DELAY" "$START_TIMEOUT" >>"$LOG"
  exit 2
fi

boot_id="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"
printf '%s\n' "$boot_id" >"$STATE_DIR/last_hook_boot_id"
printf '%s\n' "$(date +%s)" >"$STATE_DIR/last_hook_epoch"
printf '%s boot_hook_invoked boot_id=%s\n' "$(date '+%F %T')" "$boot_id" >>"$LOG"

if [[ ! -x "$LAUNCHER" ]]; then
  printf '%s\n' "MISSING_RUNTIME launcher=$LAUNCHER" >"$STATE_DIR/last_result"
  printf '%s boot_hook_missing_runtime\n' "$(date '+%F %T')" >>"$LOG"
  exit 127
fi

state="$(wrapper_lock_state 2>/dev/null || true)"
case "$state" in
  HELD)
    printf '%s\n' "ALREADY_RUNNING owner_lock=HELD" >"$STATE_DIR/last_result"
    printf '%s boot_hook_already_running owner_lock=HELD\n' "$(date '+%F %T')" >>"$LOG"
    exit 0
    ;;
  FREE) ;;
  *)
    printf '%s\n' "OWNER_LOCK_UNKNOWN" >"$STATE_DIR/last_result"
    printf '%s boot_hook_owner_lock_unknown\n' "$(date '+%F %T')" >>"$LOG"
    exit 78
    ;;
esac

if ((DELAY > 0)); then
  sleep "$DELAY"
fi

# The wrapper's own singleton flock is the authority. Re-check after the boot
# delay; a concurrent boot/deploy attempt is safe because only one wrapper can
# acquire this exact lock and only that wrapper may spawn the supervisor.
state="$(wrapper_lock_state 2>/dev/null || true)"
case "$state" in
  HELD)
    printf '%s\n' "ALREADY_RUNNING owner_lock=HELD" >"$STATE_DIR/last_result"
    exit 0
    ;;
  FREE) ;;
  *)
    printf '%s\n' "OWNER_LOCK_UNKNOWN" >"$STATE_DIR/last_result"
    exit 78
    ;;
esac

# FD 9 owns only the short-lived boot-hook lock and must not be inherited.
nohup "$HOST_PREFIX/bin/bash" "$LAUNCHER" \
  9>&- >>"$HOST_HOME/.atri-v150-production-watchdog.log" 2>&1 < /dev/null &
started_pid=$!

for ((i=0; i<START_TIMEOUT; i++)); do
  sleep 1
  state="$(wrapper_lock_state 2>/dev/null || true)"
  case "$state" in
    HELD)
      printf '%s\n' "STARTED owner_lock=HELD boot_id=$boot_id" >"$STATE_DIR/last_result"
      printf '%s boot_hook_started owner_lock=HELD requested_pid=%s\n' "$(date '+%F %T')" "$started_pid" >>"$LOG"
      exit 0
      ;;
    FREE) ;;
    *)
      printf '%s\n' "OWNER_LOCK_UNKNOWN requested_pid=$started_pid" >"$STATE_DIR/last_result"
      exit 78
      ;;
  esac
done

printf '%s\n' "START_TIMEOUT requested_pid=$started_pid owner_lock=$state" >"$STATE_DIR/last_result"
printf '%s boot_hook_start_timeout pid=%s owner_lock=%s\n' "$(date '+%F %T')" "$started_pid" "$state" >>"$LOG"
exit 1
