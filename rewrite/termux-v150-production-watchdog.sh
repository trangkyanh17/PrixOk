#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

BIN="${ATRI_V150_SUPERVISOR_BIN:-$HOME/.local/lib/atri-v150/atri-supervisor}"
BOT_LAUNCHER="$HOME/.local/lib/atri-v150/prixok-bot-v150.sh"
ORPHAN_RECOVERY="$HOME/.local/lib/atri-v150/termux-atri-final-recovery.sh"
LOG_TIMEZONE="${ATRI_LOG_TIMEZONE:-Asia/Ho_Chi_Minh}"
SHADOW_CONFIG="$HOME/.local/state/atri-v151-shadow/runtime.env"
STATE_DIR="$HOME/.local/state/atri-v150-wrapper"
OWNER_LOCK="$STATE_DIR/owner.lock"
MIN_BACKOFF="${ATRI_V150_SUPERVISOR_MIN_BACKOFF:-3}"
MAX_BACKOFF="${ATRI_V150_SUPERVISOR_MAX_BACKOFF:-30}"
STABLE_SECONDS="${ATRI_V150_SUPERVISOR_STABLE_SECONDS:-120}"
ORPHAN_GRACE="${ATRI_BOT_ORPHAN_GRACE:-90}"
ORPHAN_RETRY="${ATRI_BOT_ORPHAN_RETRY:-300}"
ORPHAN_TIMEOUT="${ATRI_BOT_ORPHAN_RECOVERY_TIMEOUT:-60}"

STOP_REQUESTED=0
CHILD_PID=""

positive_int() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

next_backoff() {
  local current="$1" next
  next=$((current * 2))
  if ((next > MAX_BACKOFF)); then
    next="$MAX_BACKOFF"
  fi
  printf '%s\n' "$next"
}

request_stop() {
  STOP_REQUESTED=1
  if [[ "$CHILD_PID" =~ ^[0-9]+$ ]] && kill -0 "$CHILD_PID" 2>/dev/null; then
    kill -TERM "$CHILD_PID" 2>/dev/null || true
  fi
}

if [[ "${1:-}" == "--self-test" ]]; then
  MIN_BACKOFF=3
  MAX_BACKOFF=30
  [[ "$(next_backoff 3)" == 6 ]]
  [[ "$(next_backoff 15)" == 30 ]]
  [[ "$(next_backoff 30)" == 30 ]]
  positive_int "$STABLE_SECONDS"
  positive_int "$ORPHAN_GRACE"
  positive_int "$ORPHAN_RETRY"
  positive_int "$ORPHAN_TIMEOUT"
  [[ "$ORPHAN_RECOVERY" == */.local/lib/atri-v150/termux-atri-final-recovery.sh ]]
  echo "v150 production watchdog self-test: PASS"
  exit 0
fi

if ! positive_int "$MIN_BACKOFF" || \
   ! positive_int "$MAX_BACKOFF" || \
   ! positive_int "$STABLE_SECONDS" || \
   ! positive_int "$ORPHAN_GRACE" || \
   ! positive_int "$ORPHAN_RETRY" || \
   ! positive_int "$ORPHAN_TIMEOUT" || \
   ((MIN_BACKOFF > MAX_BACKOFF)); then
  echo "invalid V150 supervisor lifecycle configuration" >&2
  exit 2
fi

if [[ -r "$SHADOW_CONFIG" ]]; then
  # Managed by termux-v151-shadow-canary.sh. Keep this file key=value only and
  # private to the Termux app UID; it is intentionally outside the repository.
  set -a
  # shellcheck disable=SC1090
  . "$SHADOW_CONFIG"
  set +a
fi

if [[ ! -x "$BIN" ]]; then
  echo "missing executable: $BIN" >&2
  exit 127
fi
if [[ ! -x "$BOT_LAUNCHER" ]]; then
  echo "missing executable: $BOT_LAUNCHER" >&2
  exit 127
fi

# The boot hook identifies the Go child as the V150 owner. During a short
# crash/restart backoff there is intentionally no Go child, so keep a separate
# wrapper flock to guarantee that a concurrent boot/deploy invocation cannot
# create a second restart loop.
mkdir -p "$STATE_DIR"
exec 8>"$OWNER_LOCK"
if ! flock -n 8; then
  echo "ATRI_V150_WRAPPER_ALREADY_RUNNING" >&2
  exit 76
fi

trap 'request_stop' TERM INT HUP

backoff="$MIN_BACKOFF"

while true; do
  started_at="$(date +%s)"

  env \
    ATRI_LOG_TIMEZONE="$LOG_TIMEZONE" \
    ATRI_REWRITE_WATCHDOG=true \
    ATRI_REWRITE_WATCHDOG_OBSERVE_ONLY=false \
    ATRI_REWRITE_MCP_LIFECYCLE=false \
    ATRI_V150_TELEGRAM_SHADOW="${ATRI_V150_TELEGRAM_SHADOW:-false}" \
    ATRI_TELEGRAM_SHADOW_ADDR="${ATRI_TELEGRAM_SHADOW_ADDR:-127.0.0.1:18750}" \
    ATRI_TELEGRAM_SHADOW_SECRET="${ATRI_TELEGRAM_SHADOW_SECRET:-}" \
    ATRI_TELEGRAM_SHADOW_RETRY="${ATRI_TELEGRAM_SHADOW_RETRY:-15}" \
    ATRI_BOT_SESSION=prixok-bot \
    ATRI_BOT_LAUNCHER="$BOT_LAUNCHER" \
    ATRI_BOT_ORPHAN_RECOVERY="$ORPHAN_RECOVERY" \
    ATRI_BOT_ORPHAN_GRACE="$ORPHAN_GRACE" \
    ATRI_BOT_ORPHAN_RETRY="$ORPHAN_RETRY" \
    ATRI_BOT_ORPHAN_RECOVERY_TIMEOUT="$ORPHAN_TIMEOUT" \
    ATRI_LOCAL_HEALTH="$HOME/atri-production-local-health.sh" \
    ATRI_BROWSER_ENSURE="$HOME/atri-production-browser-ensure.sh" \
    ATRI_NETWORK_STATE="$HOME/atri-production-network-state.sh" \
    ATRI_PROOT_DISTRO=debian \
    ATRI_BOT_LOCK_PATH=/app/.atri-prixok-bot-v133.lock \
    ATRI_WATCHDOG_INTERVAL="${ATRI_WATCHDOG_INTERVAL:-30}" \
    ATRI_WATCHDOG_COMMAND_TIMEOUT="${ATRI_WATCHDOG_COMMAND_TIMEOUT:-30}" \
    ATRI_WATCHDOG_REPAIR_TIMEOUT="${ATRI_WATCHDOG_REPAIR_TIMEOUT:-270}" \
    ATRI_NETWORK_INTERVAL="${ATRI_NETWORK_INTERVAL:-180}" \
    ATRI_NETWORK_TIMEOUT="${ATRI_NETWORK_TIMEOUT:-8}" \
    ATRI_REWRITE_SHUTDOWN_TIMEOUT="${ATRI_REWRITE_SHUTDOWN_TIMEOUT:-15}" \
    "$BIN" 8>&- &
  CHILD_PID=$!

  printf '%s SUPERVISOR_START pid=%s\n' "$(date '+%F %T')" "$CHILD_PID"

  set +e
  wait "$CHILD_PID"
  rc=$?
  set -e
  CHILD_PID=""

  stopped_at="$(date +%s)"
  uptime=$((stopped_at - started_at))

  if ((STOP_REQUESTED)); then
    printf '%s SUPERVISOR_STOP_REQUESTED rc=%s uptime=%ss\n' \
      "$(date '+%F %T')" "$rc" "$uptime"
    exit 0
  fi

  printf '%s SUPERVISOR_EXIT rc=%s uptime=%ss\n' \
    "$(date '+%F %T')" "$rc" "$uptime"

  if ((uptime >= STABLE_SECONDS)); then
    backoff="$MIN_BACKOFF"
  fi

  printf '%s SUPERVISOR_RESTART_BACKOFF seconds=%s\n' \
    "$(date '+%F %T')" "$backoff"

  sleep "$backoff" || true
  if ((STOP_REQUESTED)); then
    exit 0
  fi

  backoff="$(next_backoff "$backoff")"
done
