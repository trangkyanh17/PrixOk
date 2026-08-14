#!/usr/bin/env bash
# Sourced by termux-production-recovery-host.sh; do not execute directly.

positive_int() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

classify_runtime() {
  local session="$1" lock="$2" health="$3"
  if [[ "$session" == "PRESENT" && "$lock" == "HELD" && "$health" == "HEALTHY" ]]; then
    echo HEALTHY
  elif [[ "$session" == "MISSING" && ( "$lock" == "FREE" || "$lock" == "MISSING" ) ]]; then
    echo STOPPED
  elif [[ "$session" == "MISSING" && "$lock" == "HELD" ]]; then
    echo WORKER_OUTSIDE_TMUX
  elif [[ "$session" == "PRESENT" && ( "$lock" == "FREE" || "$lock" == "MISSING" ) ]]; then
    echo SESSION_WITHOUT_WORKER
  else
    echo MIXED
  fi
}

choose_report_dir() {
  local candidate
  for candidate in /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$candidate" && -w "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  printf '%s\n' "$STATE_DIR"
}

pass() {
  local key="$1"; shift
  RESULTS["$key"]="PASS"
  DETAILS["$key"]="$*"
  printf '[PASS] %-20s %s\n' "$key" "$*"
}

fail() {
  local key="$1"; shift
  RESULTS["$key"]="FAIL"
  DETAILS["$key"]="$*"
  OVERALL_FAIL=1
  printf '[FAIL] %-20s %s\n' "$key" "$*"
}

info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }

section() { printf '\n===== %s =====\n' "$1"; }

debian_run() {
  proot-distro login debian -- bash -lc "$1"
}

find_rootfs() {
  local candidate
  for candidate in \
    "$HOST_PREFIX/var/lib/proot-distro/containers/debian/rootfs" \
    "$HOST_PREFIX/var/lib/proot-distro/installed-rootfs/debian"; do
    if [[ -d "$candidate$DEBIAN_CLONE/rewrite" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

bot_session_state() {
  if tmux has-session -t prixok-bot 2>/dev/null; then echo PRESENT; else echo MISSING; fi
}

bot_pane_pid() {
  tmux list-panes -t prixok-bot -F '#{pane_pid}' 2>/dev/null | head -n1
}

bot_lock_state() {
  debian_run '
set -u
p=/app/.atri-prixok-bot-v133.lock
if [ ! -e "$p" ]; then
  echo MISSING
  exit 0
fi
exec 9<>"$p"
if flock -n 9; then
  flock -u 9
  echo FREE
else
  echo HELD
fi
' 2>/dev/null | tail -n1 | tr -d '\r'
}

local_health_state() {
  if "$HOST_HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1; then
    echo HEALTHY
  else
    echo UNHEALTHY
  fi
}

network_state() {
  if ATRI_NETWORK_PROBE_TIMEOUT=8 "$HOST_HOME/atri-production-network-state.sh" --via-socks >/dev/null 2>&1; then
    echo ONLINE
  else
    echo PENDING_NONBLOCKING
  fi
}

legacy_watchdog_pids() {
  pgrep -af '[a]tri-production-watchdog.sh' 2>/dev/null | awk 'NF{print $1}' | sort -n
}

v150_watchdog_pids() {
  pgrep -af "$V150_HOST_BIN" 2>/dev/null | awk 'NF{print $1}' | sort -n
}

wait_pid_gone() {
  local pid="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))
  while kill -0 "$pid" 2>/dev/null; do
    ((SECONDS < deadline)) || return 1
    sleep 1
  done
}

stop_pid_gracefully() {
  local pid="$1" timeout_seconds="${2:-15}"
  kill -TERM "$pid" 2>/dev/null || return 0
  if wait_pid_gone "$pid" "$timeout_seconds"; then
    return 0
  fi
  return 1
}

descendant_pids() {
  local root="$1"
  ps -eo pid=,ppid= | awk -v root="$root" '
    { parent[$1] = $2 }
    END {
      seen[root] = 1
      changed = 1
      while (changed) {
        changed = 0
        for (pid in parent) {
          parent_pid = parent[pid]
          if ((parent_pid in seen) && !(pid in seen)) {
            seen[pid] = 1
            changed = 1
          }
        }
      }
      for (pid in seen) if (pid != root) print pid
    }
  ' | sort -n
}

sum_tree_rss_kb() {
  local root="$1" pid rss total=0
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    rss="$(ps -o rss= -p "$pid" 2>/dev/null | awk 'NF{print $1; exit}')"
    [[ "$rss" =~ ^[0-9]+$ ]] || continue
    total=$((total + rss))
  done < <(printf '%s\n' "$root"; descendant_pids "$root")
  printf '%s\n' "$total"
}

wait_for_worker_ready() {
  local timeout_seconds="$1"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    session="$(bot_session_state)"
    lock="$(bot_lock_state || echo UNKNOWN)"
    if [[ "$session" == PRESENT && "$lock" == HELD ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

wait_for_runtime_healthy() {
  local timeout_seconds="$1"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    session="$(bot_session_state)"
    lock="$(bot_lock_state || echo UNKNOWN)"
    health="$(local_health_state)"
    if [[ "$session" == PRESENT && "$lock" == HELD && "$health" == HEALTHY ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

ready_line_ok() {
  local line="$1" plugin
  for plugin in context7 semgrep serena; do
    grep -Eq "${plugin}=ready:[1-9][0-9]*" <<<"$line" || return 1
  done
}

wait_for_log_pattern() {
  local logfile="$1"
  local pattern="$2"
  local timeout_seconds="$3"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    grep -Eq "$pattern" "$logfile" 2>/dev/null && return 0
    [[ -z "$MCP_PID" || -e "/proc/$MCP_PID" ]] || return 1
    sleep 2
  done
  return 1
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

start_mcp() {
  : >"$MCP_LOG"
  debian_run "rm -f '$MCP_PIDFILE'"
  proot-distro login debian -- bash -lc "
set -Eeuo pipefail
cd '$DEBIAN_CLONE/rewrite'
echo \$\$ > '$MCP_PIDFILE'
exec env \
  ATRI_LOG_TIMEZONE='$LOG_TIMEZONE' \
  ATRI_REWRITE_WATCHDOG=false \
  ATRI_REWRITE_MCP_LIFECYCLE=true \
  ATRI_MCP_PREWARM_PLUGINS=serena,context7,semgrep \
  ATRI_MCP_PREWARM_CONCURRENCY=2 \
  ATRI_MCP_PREWARM_TIMEOUT=240 \
  ATRI_MCP_REQUEST_TIMEOUT=240 \
  ATRI_MCP_HEALTH_INTERVAL='$MCP_HEALTH_INTERVAL' \
  ATRI_MCP_PRUNE_INTERVAL=30 \
  ATRI_MCP_IDLE_TTL=900 \
  ATRI_REWRITE_SHUTDOWN_TIMEOUT=15 \
  ./target/release/atri-supervisor
" >"$MCP_LOG" 2>&1 &
  MCP_OUTER_PID=$!

  local deadline=$((SECONDS + 20)) pid=""
  while ((SECONDS < deadline)); do
    pid="$(debian_run "cat '$MCP_PIDFILE' 2>/dev/null || true" | tail -n1 | tr -d '\r')"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      MCP_PID="$pid"
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_mcp() {
  if [[ -n "$MCP_PID" ]] && kill -0 "$MCP_PID" 2>/dev/null; then
    kill -TERM "$MCP_PID" 2>/dev/null || true
    wait_pid_gone "$MCP_PID" 20 || kill -KILL "$MCP_PID" 2>/dev/null || true
  fi
  if [[ -n "$MCP_OUTER_PID" ]] && kill -0 "$MCP_OUTER_PID" 2>/dev/null; then
    wait_pid_gone "$MCP_OUTER_PID" 5 || kill -TERM "$MCP_OUTER_PID" 2>/dev/null || true
  fi
  MCP_PID=""
  MCP_OUTER_PID=""
}

start_legacy_watchdog() {
  nohup "$HOST_PREFIX/bin/bash" "$HOST_HOME/atri-production-watchdog.sh" \
    >>"$HOST_HOME/.atri-production-watchdog-launch.log" 2>&1 < /dev/null &
  local pid=$!
  sleep 2
  kill -0 "$pid" 2>/dev/null
}

start_v150_watchdog() {
  : >>"$V150_LOG"
  nohup "$HOST_PREFIX/bin/bash" "$V150_LAUNCHER" >>"$V150_LOG" 2>&1 < /dev/null &
  V150_PID=$!
  sleep 2
  kill -0 "$V150_PID" 2>/dev/null
}

rollback_watchdog_owner() {
  local ok=1 pid
  if [[ -n "$V150_PID" ]] && kill -0 "$V150_PID" 2>/dev/null; then
    stop_pid_gracefully "$V150_PID" 15 || ok=0
  fi
  V150_PID=""
  mapfile -t current_v150 < <(v150_watchdog_pids)
  for pid in "${current_v150[@]}"; do
    stop_pid_gracefully "$pid" 15 || ok=0
  done

  mapfile -t current_legacy < <(legacy_watchdog_pids)
  if ((${#current_legacy[@]} == 0)); then
    if start_legacy_watchdog; then
      LEGACY_STOPPED=0
    else
      ok=0
    fi
  elif ((${#current_legacy[@]} > 1)); then
    ok=0
  fi
  ((ok == 1))
}

print_debug_tail() {
  section "PRODUCTION TMUX TAIL"
  tmux capture-pane -t prixok-bot -p -S -120 2>/dev/null || true
  section "WATCHDOG LOG TAIL"
  tail -120 "$HOST_HOME/.atri-production-watchdog.log" 2>/dev/null || true
  section "LAUNCHER LOG TAIL"
  tail -120 "$HOST_HOME/.atri-production-launcher.log" 2>/dev/null || true
  section "V150 WATCHDOG LOG TAIL"
  tail -120 "$V150_LOG" 2>/dev/null || true
  section "MCP LOG"
  cat "$MCP_LOG" 2>/dev/null || true
}

cleanup() {
  stop_mcp >/dev/null 2>&1 || true

  cleanup_lock="$(bot_lock_state 2>/dev/null || echo UNKNOWN)"
  if ((HANDOFF_COMMITTED == 0)) && ((BOT_CREATED == 1 || HANDOFF_IN_PROGRESS == 1)) && [[ "$cleanup_lock" == HELD ]]; then
    # Any run that recovered the worker or began a watchdog cutover must leave
    # the healthy worker under exactly one known fallback owner on failure.
    rollback_watchdog_owner >/dev/null 2>&1 || true
  fi

  if ((BOT_CREATED == 1)) && [[ "$(bot_session_state 2>/dev/null || echo MISSING)" == PRESENT ]] && [[ "$cleanup_lock" != HELD ]]; then
    # Remove only the tmux session created by this harness when no singleton
    # worker ever acquired the production lock. Never kill an active worker.
    tmux kill-session -t prixok-bot 2>/dev/null || true
  fi
}
