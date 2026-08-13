#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

LOG="$HOME/.atri-production-watchdog.log"
BROWSER_ENSURE="$HOME/atri-production-browser-ensure.sh"
LOCAL_HEALTH="$HOME/atri-production-local-health.sh"
NETWORK_STATE="$HOME/atri-production-network-state.sh"
BOT_LAUNCHER="$HOME/prixok-bot.sh"

LAST_NETWORK_CHECK=0
NETWORK_CHECK_INTERVAL=180
LAST_NETWORK_STATE=""
REPAIR_FAILURES=0
NEXT_REPAIR_AT=0
LAST_BACKOFF_LOG=0

trim_log() {
  if [ -f "$LOG" ]; then
    bytes="$(wc -c <"$LOG" 2>/dev/null || echo 0)"
    if [ "${bytes:-0}" -gt 1048576 ]; then
      tail -n 1800 "$LOG" >"$LOG.tmp" || true
      mv -f "$LOG.tmp" "$LOG"
    fi
  fi
}

log() {
  printf '%s %s\n' \
    "$(date '+%F %T')" \
    "$*" >>"$LOG"
}

bot_worker_lock_held() {
  proot-distro login debian -- bash -lc '
    exec 9>>/app/.atri-prixok-bot-v133.lock

    if flock -n 9; then
      flock -u 9
      exit 1
    fi

    exit 0
  ' >/dev/null 2>&1
}

while true; do
  trim_log

  if ! tmux has-session -t prixok-bot 2>/dev/null; then
    if bot_worker_lock_held; then
      log BOT_SESSION_MISSING_WORKER_ACTIVE
    elif [ -x "$BOT_LAUNCHER" ]; then
      log BOT_SESSION_RESTART
      tmux new-session -d \
        -s prixok-bot \
        'exec bash "$HOME/prixok-bot.sh"' \
        || log BOT_SESSION_RESTART_FAIL
    else
      log BOT_LAUNCHER_MISSING
    fi
  fi

  if [ -x "$LOCAL_HEALTH" ] && \
     "$LOCAL_HEALTH" --quiet >/dev/null 2>&1
  then
    REPAIR_FAILURES=0
    NEXT_REPAIR_AT=0
  else
    log LOCAL_SHARED_COMPONENT_HEALTH=UNHEALTHY

    NOW="$(date +%s)"
    if [ "$NOW" -lt "$NEXT_REPAIR_AT" ]; then
      if [ $((NOW - LAST_BACKOFF_LOG)) -ge 60 ]; then
        log "LOCAL_SHARED_COMPONENT_REPAIR=BACKOFF until=$NEXT_REPAIR_AT"
        LAST_BACKOFF_LOG="$NOW"
      fi
      sleep 10
      continue
    fi

    set +e
    timeout 270 \
      "$BROWSER_ENSURE" \
      --from-watchdog \
      >/dev/null 2>&1
    RC=$?
    set -e

    if [ "$RC" -eq 0 ]; then
      log LOCAL_SHARED_COMPONENT_REPAIR=PASS
      REPAIR_FAILURES=0
      NEXT_REPAIR_AT=0
    else
      log "LOCAL_SHARED_COMPONENT_REPAIR=FAIL rc=$RC"
      REPAIR_FAILURES=$((REPAIR_FAILURES + 1))
      case "$REPAIR_FAILURES" in
        1) REPAIR_DELAY=30 ;;
        2) REPAIR_DELAY=60 ;;
        3) REPAIR_DELAY=120 ;;
        4) REPAIR_DELAY=300 ;;
        *) REPAIR_DELAY=600 ;;
      esac
      NOW="$(date +%s)"
      NEXT_REPAIR_AT=$((NOW + REPAIR_DELAY))
      log "LOCAL_SHARED_COMPONENT_REPAIR_BACKOFF=${REPAIR_DELAY}s"
    fi
  fi

  NOW="$(date +%s)"

  if [ $((NOW - LAST_NETWORK_CHECK)) -ge "$NETWORK_CHECK_INTERVAL" ]; then
    LAST_NETWORK_CHECK="$NOW"

    if [ -x "$NETWORK_STATE" ]; then
      set +e
      ATRI_NETWORK_PROBE_TIMEOUT=8 \
        "$NETWORK_STATE" --via-socks \
        >/dev/null 2>&1
      NET_RC=$?
      set -e

      if [ "$NET_RC" -eq 0 ]; then
        NET_STATE="ONLINE"
      else
        NET_STATE="PENDING_NONBLOCKING"
      fi

      if [ "$NET_STATE" != "$LAST_NETWORK_STATE" ]; then
        log "NETWORK_STATE=$NET_STATE"
        LAST_NETWORK_STATE="$NET_STATE"
      fi
    fi
  fi

  sleep 30
done
