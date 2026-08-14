#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

BIN="$HOME/.local/lib/atri-v150/atri-supervisor"
BOT_LAUNCHER="$HOME/.local/lib/atri-v150/prixok-bot-v150.sh"
LOG_TIMEZONE="${ATRI_LOG_TIMEZONE:-Asia/Ho_Chi_Minh}"
SHADOW_CONFIG="$HOME/.local/state/atri-v151-shadow/runtime.env"

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

exec env \
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
  "$BIN"