#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
export HOME="$HOST_HOME"
export PREFIX="$HOST_PREFIX"
export PATH="$HOST_PREFIX/bin:/system/bin:/system/xbin"
export TMPDIR="$HOST_PREFIX/tmp"
export LD_LIBRARY_PATH="$HOST_PREFIX/lib"

BIN="$HOST_HOME/.local/lib/atri-v150/atri-supervisor"
LAUNCHER="$HOST_HOME/atri-v150-production-watchdog.sh"
STATE_DIR="$HOST_HOME/.cache/atri-v150-persistence"
LOG="$HOST_HOME/.atri-v150-persistence.log"
LOCK_FILE="$STATE_DIR/boot-hook.lock"
DELAY="${ATRI_V150_BOOT_DELAY:-20}"
START_TIMEOUT="${ATRI_V150_BOOT_START_TIMEOUT:-60}"

mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  printf '%s\n' "LOCK_BUSY" >"$STATE_DIR/last_result"
  printf '%s boot_hook_lock_busy\n' "$(date '+%F %T')" >>"$LOG"
  exit 77
fi

positive_int() { [[ "$1" =~ ^[0-9]+$ ]]; }
if ! positive_int "$DELAY" || ! positive_int "$START_TIMEOUT"; then
  printf '%s invalid boot timing delay=%s timeout=%s\n' "$(date '+%F %T')" "$DELAY" "$START_TIMEOUT" >>"$LOG"
  exit 2
fi

boot_id="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"
printf '%s\n' "$boot_id" >"$STATE_DIR/last_hook_boot_id"
printf '%s\n' "$(date +%s)" >"$STATE_DIR/last_hook_epoch"
printf '%s boot_hook_invoked boot_id=%s\n' "$(date '+%F %T')" "$boot_id" >>"$LOG"

legacy_pids() {
  pgrep -af '[a]tri-production-watchdog.sh' 2>/dev/null | awk 'NF{print $1}' | sort -n
}

v150_pids() {
  pgrep -af "$BIN" 2>/dev/null | awk 'NF{print $1}' | sort -n
}

mapfile -t legacy < <(legacy_pids)
if ((${#legacy[@]} > 0)); then
  printf '%s\n' "BLOCKED_LEGACY_OWNER pids=${legacy[*]}" >"$STATE_DIR/last_result"
  printf '%s boot_hook_blocked legacy=%s\n' "$(date '+%F %T')" "${legacy[*]}" >>"$LOG"
  exit 75
fi

mapfile -t current < <(v150_pids)
if ((${#current[@]} == 1)); then
  printf '%s\n' "ALREADY_RUNNING pid=${current[0]}" >"$STATE_DIR/last_result"
  printf '%s boot_hook_already_running pid=%s\n' "$(date '+%F %T')" "${current[0]}" >>"$LOG"
  exit 0
elif ((${#current[@]} > 1)); then
  printf '%s\n' "DUPLICATE_V150 pids=${current[*]}" >"$STATE_DIR/last_result"
  printf '%s boot_hook_duplicate_v150 pids=%s\n' "$(date '+%F %T')" "${current[*]}" >>"$LOG"
  exit 76
fi

if [[ ! -x "$BIN" || ! -x "$LAUNCHER" ]]; then
  printf '%s\n' "MISSING_RUNTIME bin=$BIN launcher=$LAUNCHER" >"$STATE_DIR/last_result"
  printf '%s boot_hook_missing_runtime\n' "$(date '+%F %T')" >>"$LOG"
  exit 127
fi

if ((DELAY > 0)); then
  sleep "$DELAY"
fi

# Re-check ownership after the boot delay so two boot paths cannot create two
# V150 watchdogs. A legacy owner always wins the safety gate: V150 will not start.
mapfile -t legacy < <(legacy_pids)
mapfile -t current < <(v150_pids)
if ((${#legacy[@]} > 0)); then
  printf '%s\n' "BLOCKED_LEGACY_OWNER pids=${legacy[*]}" >"$STATE_DIR/last_result"
  exit 75
fi
if ((${#current[@]} == 1)); then
  printf '%s\n' "ALREADY_RUNNING pid=${current[0]}" >"$STATE_DIR/last_result"
  exit 0
elif ((${#current[@]} > 1)); then
  printf '%s\n' "DUPLICATE_V150 pids=${current[*]}" >"$STATE_DIR/last_result"
  exit 76
fi

# FD 9 owns the boot-hook flock. It must not survive into the long-lived
# watchdog/supervisor process, otherwise future boot/deploy invocations see a
# permanently busy lock even though this hook has already exited.
nohup "$HOST_PREFIX/bin/bash" "$LAUNCHER" \
  9>&- >>"$HOST_HOME/.atri-v150-production-watchdog.log" 2>&1 < /dev/null &
started_pid=$!

for ((i=0; i<START_TIMEOUT; i++)); do
  sleep 1
  mapfile -t legacy < <(legacy_pids)
  mapfile -t current < <(v150_pids)
  if ((${#legacy[@]} > 0)); then
    printf '%s\n' "START_CONFLICT_LEGACY pids=${legacy[*]}" >"$STATE_DIR/last_result"
    printf '%s boot_hook_start_conflict legacy=%s\n' "$(date '+%F %T')" "${legacy[*]}" >>"$LOG"
    exit 75
  fi
  if ((${#current[@]} == 1)); then
    printf '%s\n' "STARTED pid=${current[0]} boot_id=$boot_id" >"$STATE_DIR/last_result"
    printf '%s boot_hook_started pid=%s requested_pid=%s\n' "$(date '+%F %T')" "${current[0]}" "$started_pid" >>"$LOG"
    exit 0
  fi
done

printf '%s\n' "START_TIMEOUT requested_pid=$started_pid" >"$STATE_DIR/last_result"
printf '%s boot_hook_start_timeout pid=%s\n' "$(date '+%F %T')" "$started_pid" >>"$LOG"
exit 1