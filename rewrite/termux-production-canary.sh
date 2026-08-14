#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_BRANCH="rewrite/rust-go-ts-v150"
BUILD_JOBS="${ATRI_BUILD_JOBS:-2}"
CANARY_SECONDS="${ATRI_PRODUCTION_CANARY_SECONDS:-45}"
STARTUP_TIMEOUT="${ATRI_PRODUCTION_CANARY_STARTUP_TIMEOUT:-300}"
HEALTH_INTERVAL="${ATRI_PRODUCTION_CANARY_HEALTH_INTERVAL:-15}"
LOG_TIMEZONE="${ATRI_LOG_TIMEZONE:-Asia/Ho_Chi_Minh}"
HOST_PREFIX="${ATRI_TERMUX_PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${ATRI_TERMUX_HOME:-/data/data/com.termux/files/home}"
HOST_BASH="$HOST_PREFIX/bin/bash"
HOST_PATH="$HOST_PREFIX/bin:/system/bin:/system/xbin"
TEST_ID="$(date +%Y%m%d-%H%M%S)-$$"
STATE_DIR="${TMPDIR:-/tmp}/atri-v150-production-canary-${TEST_ID}"
DEBIAN_SUPERVISOR="$ROOT_DIR/target/release/atri-supervisor"
HOST_SUPERVISOR="$ROOT_DIR/target/release/atri-supervisor-android-arm64"
HOST_STAGE_DIR="$HOST_HOME/.cache/atri-rewrite-v150-canary"
STAGED_HOST_SUPERVISOR="$HOST_STAGE_DIR/atri-supervisor-${TEST_ID}"
HOST_WATCHDOG_LOG="$HOST_STAGE_DIR/watchdog-${TEST_ID}.log"
MCP_LOG="$STATE_DIR/mcp.log"
DEBIAN_SUPERVISOR_PID=""
HOST_WATCHDOG_PID=""
OVERALL_FAIL=0

RESULT_ORDER=(
  REPO
  BUILD
  HOST_BRIDGE
  HOST_HELPERS
  LEGACY_WATCHDOG
  BOT_SESSION
  BOT_LOCK
  PROD_HEALTH
  NETWORK
  HOST_BINARY
  WATCHDOG_OBSERVE
  MCP_COEXIST
  BOT_STABILITY
  MEMORY
  MUTATION_GUARD
  CLEANUP
)
declare -A RESULTS=()
declare -A DETAILS=()

positive_int() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

for pair in \
  "ATRI_BUILD_JOBS=$BUILD_JOBS" \
  "ATRI_PRODUCTION_CANARY_SECONDS=$CANARY_SECONDS" \
  "ATRI_PRODUCTION_CANARY_STARTUP_TIMEOUT=$STARTUP_TIMEOUT" \
  "ATRI_PRODUCTION_CANARY_HEALTH_INTERVAL=$HEALTH_INTERVAL"; do
  name="${pair%%=*}"
  value="${pair#*=}"
  if ! positive_int "$value"; then
    echo "$name must be a positive integer; got: $value" >&2
    exit 2
  fi
done

if [[ "${1:-}" == "--self-test" ]]; then
  [[ "$EXPECTED_BRANCH" == "rewrite/rust-go-ts-v150" ]]
  positive_int "$CANARY_SECONDS"
  positive_int "$STARTUP_TIMEOUT"
  positive_int "$HEALTH_INTERVAL"
  echo "production canary self-test: PASS"
  exit 0
fi
if (($#)); then
  echo "Usage: ./termux-production-canary.sh [--self-test]" >&2
  exit 2
fi

mkdir -p "$STATE_DIR"

choose_report_dir() {
  local candidate
  for candidate in /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$candidate" && -w "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  printf '%s\n' "$ROOT_DIR/target"
}

REPORT_DIR="$(choose_report_dir)"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/atri-v150-production-canary-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

pass() {
  local key="$1"
  shift
  RESULTS["$key"]="PASS"
  DETAILS["$key"]="$*"
  printf '[PASS] %-18s %s\n' "$key" "$*"
}

fail() {
  local key="$1"
  shift
  RESULTS["$key"]="FAIL"
  DETAILS["$key"]="$*"
  OVERALL_FAIL=1
  printf '[FAIL] %-18s %s\n' "$key" "$*"
}

info() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*"
}

host_run() {
  HOME="$HOST_HOME" \
  PREFIX="$HOST_PREFIX" \
  TMPDIR="$HOST_PREFIX/tmp" \
  PATH="$HOST_PATH" \
  LD_LIBRARY_PATH="$HOST_PREFIX/lib" \
    "$HOST_BASH" --noprofile --norc -c "$1"
}

host_pid_alive() {
  local pid="$1"
  host_run "kill -0 $pid 2>/dev/null"
}

stop_host_watchdog() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  if ! host_pid_alive "$pid"; then
    return 0
  fi
  host_run "kill -TERM $pid 2>/dev/null || true; for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 $pid 2>/dev/null || exit 0; sleep 1; done; kill -KILL $pid 2>/dev/null || true; exit 1" || true
}

wait_pid_gone() {
  local pid="$1"
  local timeout_seconds="$2"
  local deadline=$((SECONDS + timeout_seconds))
  while kill -0 "$pid" 2>/dev/null; do
    if ((SECONDS >= deadline)); then
      return 1
    fi
    sleep 1
  done
  return 0
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
      for (pid in seen) {
        if (pid != root) {
          print pid
        }
      }
    }
  ' | sort -n
}

all_tree_pids() {
  local root="$1"
  printf '%s\n' "$root"
  descendant_pids "$root"
}

sum_rss_kb() {
  local root="$1"
  local pid rss total=0
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    rss="$(ps -o rss= -p "$pid" 2>/dev/null | awk 'NF {print $1; exit}')"
    [[ "$rss" =~ ^[0-9]+$ ]] || continue
    total=$((total + rss))
  done < <(all_tree_pids "$root")
  printf '%s\n' "$total"
}

stop_debian_supervisor() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill -TERM "$pid" 2>/dev/null || true
  if wait_pid_gone "$pid" 20; then
    wait "$pid" 2>/dev/null || true
    return 0
  fi
  kill -KILL "$pid" 2>/dev/null || true
  wait_pid_gone "$pid" 5 || true
  wait "$pid" 2>/dev/null || true
  return 1
}

cleanup() {
  if [[ -n "${DEBIAN_SUPERVISOR_PID:-}" ]]; then
    stop_debian_supervisor "$DEBIAN_SUPERVISOR_PID" >/dev/null 2>&1 || true
    DEBIAN_SUPERVISOR_PID=""
  fi
  if [[ -n "${HOST_WATCHDOG_PID:-}" ]] && [[ -x "$HOST_BASH" ]]; then
    stop_host_watchdog "$HOST_WATCHDOG_PID" >/dev/null 2>&1 || true
    HOST_WATCHDOG_PID=""
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

ready_line_ok() {
  local line="$1"
  local plugin
  for plugin in context7 semgrep serena; do
    grep -Eq "${plugin}=ready:[1-9][0-9]*" <<<"$line" || return 1
  done
}

wait_for_log() {
  local pid="$1"
  local logfile="$2"
  local pattern="$3"
  local timeout_seconds="$4"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    grep -Eq "$pattern" "$logfile" 2>/dev/null && return 0
    kill -0 "$pid" 2>/dev/null || return 1
    sleep 2
  done
  return 1
}

worker_lock_held() {
  host_run "proot-distro login debian -- bash -lc 'exec 9>>/app/.atri-prixok-bot-v133.lock; if flock -n 9; then flock -u 9; exit 1; fi; exit 0' >/dev/null 2>&1"
}

legacy_watchdog_pids() {
  host_run "pgrep -af '[a]tri-production-watchdog.sh' 2>/dev/null || true" | awk 'NF {print $1}' | sort -n
}

legacy_watchdog_topology() {
  local pids list
  pids="$(legacy_watchdog_pids | paste -sd, -)"
  [[ -n "$pids" ]] || return 0
  host_run "ps -o pid,ppid,lstart,args -p $pids 2>/dev/null || true"
}

bot_pane_pid() {
  host_run "tmux list-panes -t prixok-bot -F '#{pane_pid}' 2>/dev/null | head -n1"
}

launch_mcp() {
  : >"$MCP_LOG"
  (
    cd "$ROOT_DIR"
    ATRI_LOG_TIMEZONE="$LOG_TIMEZONE" \
    ATRI_REWRITE_WATCHDOG=false \
    ATRI_REWRITE_MCP_LIFECYCLE=true \
    ATRI_MCP_PREWARM_PLUGINS=serena,context7,semgrep \
    ATRI_MCP_PREWARM_CONCURRENCY=2 \
    ATRI_MCP_PREWARM_TIMEOUT=240 \
    ATRI_MCP_REQUEST_TIMEOUT=240 \
    ATRI_MCP_HEALTH_INTERVAL="$HEALTH_INTERVAL" \
    ATRI_MCP_PRUNE_INTERVAL=30 \
    ATRI_MCP_IDLE_TTL=900 \
    ATRI_REWRITE_SHUTDOWN_TIMEOUT=15 \
    "$DEBIAN_SUPERVISOR"
  ) >"$MCP_LOG" 2>&1 &
  DEBIAN_SUPERVISOR_PID=$!
  info "Debian MCP supervisor pid=$DEBIAN_SUPERVISOR_PID"
}

start_host_observer() {
  mkdir -p "$HOST_STAGE_DIR"
  cp -f "$HOST_SUPERVISOR" "$STAGED_HOST_SUPERVISOR"
  chmod 700 "$STAGED_HOST_SUPERVISOR"
  : >"$HOST_WATCHDOG_LOG"

  HOST_WATCHDOG_PID="$(host_run "nohup env \
ATRI_LOG_TIMEZONE='$LOG_TIMEZONE' \
ATRI_REWRITE_WATCHDOG=true \
ATRI_REWRITE_WATCHDOG_OBSERVE_ONLY=true \
ATRI_REWRITE_MCP_LIFECYCLE=false \
ATRI_BOT_SESSION=prixok-bot \
ATRI_BOT_LAUNCHER='$HOST_HOME/prixok-bot.sh' \
ATRI_LOCAL_HEALTH='$HOST_HOME/atri-production-local-health.sh' \
ATRI_BROWSER_ENSURE='$HOST_HOME/atri-production-browser-ensure.sh' \
ATRI_NETWORK_STATE='$HOST_HOME/atri-production-network-state.sh' \
ATRI_PROOT_DISTRO=debian \
ATRI_BOT_LOCK_PATH=/app/.atri-prixok-bot-v133.lock \
ATRI_WATCHDOG_INTERVAL=5 \
ATRI_WATCHDOG_COMMAND_TIMEOUT=10 \
ATRI_WATCHDOG_REPAIR_TIMEOUT=30 \
ATRI_NETWORK_INTERVAL=10 \
ATRI_NETWORK_TIMEOUT=8 \
ATRI_REWRITE_SHUTDOWN_TIMEOUT=10 \
'$STAGED_HOST_SUPERVISOR' >'$HOST_WATCHDOG_LOG' 2>&1 < /dev/null & echo \$!")"
  [[ "$HOST_WATCHDOG_PID" =~ ^[0-9]+$ ]]
}

print_summary() {
  echo
  echo "===== FINAL SUMMARY ====="
  local key status detail
  for key in "${RESULT_ORDER[@]}"; do
    status="${RESULTS[$key]:-SKIP}"
    detail="${DETAILS[$key]:-not executed}"
    printf '%-20s %-5s %s\n' "$key" "$status" "$detail"
  done
  if ((OVERALL_FAIL == 0)); then
    echo "OVERALL              PASS"
  else
    echo "OVERALL              FAIL"
  fi
  echo
  echo "END: $(date)"
  echo "REPORT: $REPORT"

  echo
  echo "===== MEMORY ====="
  free -h || true

  echo
  echo "===== LEGACY WATCHDOG TOPOLOGY ====="
  legacy_watchdog_topology || true

  if [[ -f "$HOST_WATCHDOG_LOG" ]]; then
    echo
    echo "===== LOG: host-watchdog-observe.log ====="
    cat "$HOST_WATCHDOG_LOG" || true
  fi
  if [[ -f "$MCP_LOG" ]]; then
    echo
    echo "===== LOG: mcp-coexist.log ====="
    cat "$MCP_LOG" || true
  fi
}

echo "===== ATRI V150 PRODUCTION INTEGRATION CANARY ====="
echo "START: $(date)"
echo "REPORT: $REPORT"
echo "STATE: $STATE_DIR"
echo "MODE: observe-only; production restart/repair disabled"
echo

cd "$ROOT_DIR"

branch="$(git branch --show-current 2>/dev/null || true)"
head_sha="$(git rev-parse HEAD 2>/dev/null || true)"
if [[ "$branch" != "$EXPECTED_BRANCH" ]]; then
  fail REPO "expected branch=$EXPECTED_BRANCH got=${branch:-unknown}"
elif ! git diff --quiet || ! git diff --cached --quiet; then
  fail REPO "tracked working tree is dirty at ${head_sha:-unknown}"
else
  pass REPO "branch=$branch head=$head_sha"
fi

if [[ ! -x "$HOST_BASH" ]] || [[ ! -d "$HOST_HOME" ]]; then
  fail HOST_BRIDGE "Termux host bridge unavailable prefix=$HOST_PREFIX home=$HOST_HOME"
elif host_run "command -v tmux >/dev/null && command -v proot-distro >/dev/null && test -d \"\$HOME\"" >/dev/null 2>&1; then
  pass HOST_BRIDGE "Termux bash/tmux/proot-distro reachable from Debian"
else
  fail HOST_BRIDGE "host commands are not executable through $HOST_BASH"
fi

if [[ "${RESULTS[HOST_BRIDGE]:-FAIL}" == "PASS" ]]; then
  if host_run 'for f in prixok-bot.sh atri-production-local-health.sh atri-production-browser-ensure.sh atri-production-network-state.sh atri-production-watchdog.sh; do test -x "$HOME/$f" || exit 20; bash -n "$HOME/$f" || exit 21; done' >/dev/null 2>&1; then
    pass HOST_HELPERS "live production helpers are executable and syntax-valid"
  else
    fail HOST_HELPERS "one or more live production helpers missing/not executable/invalid"
  fi

  mapfile -t legacy_before < <(legacy_watchdog_pids)
  if ((${#legacy_before[@]} == 1)); then
    pass LEGACY_WATCHDOG "single legacy watchdog pid=${legacy_before[0]}"
  else
    fail LEGACY_WATCHDOG "expected 1 legacy watchdog, found ${#legacy_before[@]} pids=${legacy_before[*]:-none}"
  fi

  if host_run "tmux has-session -t prixok-bot 2>/dev/null"; then
    pane_before="$(bot_pane_pid)"
    if [[ "$pane_before" =~ ^[0-9]+$ ]]; then
      pass BOT_SESSION "prixok-bot active pane_pid=$pane_before"
    else
      fail BOT_SESSION "tmux session exists but pane pid unavailable"
    fi
  else
    fail BOT_SESSION "prixok-bot tmux session missing"
    pane_before=""
  fi

  if worker_lock_held; then
    pass BOT_LOCK "/app/.atri-prixok-bot-v133.lock held by active worker"
  else
    fail BOT_LOCK "bot worker lock is not held"
  fi

  if host_run '"$HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1'; then
    pass PROD_HEALTH "live local health is healthy before canary"
  else
    fail PROD_HEALTH "live local health failed before canary"
  fi

  if host_run 'ATRI_NETWORK_PROBE_TIMEOUT=8 "$HOME/atri-production-network-state.sh" --via-socks >/dev/null 2>&1'; then
    pass NETWORK "state=ONLINE"
  else
    pass NETWORK "state=PENDING_NONBLOCKING"
  fi
fi

critical_preflight=0
for key in REPO HOST_BRIDGE HOST_HELPERS LEGACY_WATCHDOG BOT_SESSION BOT_LOCK PROD_HEALTH; do
  [[ "${RESULTS[$key]:-FAIL}" == "PASS" ]] || critical_preflight=1
done

if ((critical_preflight == 0)); then
  if ATRI_BUILD_JOBS="$BUILD_JOBS" ./termux-build.sh --supervisor-only && \
     ATRI_BUILD_JOBS="$BUILD_JOBS" ./termux-build.sh --host-watchdog-only; then
    pass BUILD "Debian MCP + Android/arm64 host watchdog supervisors built"
  else
    fail BUILD "supervisor build failed"
  fi
else
  fail BUILD "skipped because production preflight failed"
fi

if [[ "${RESULTS[BUILD]:-FAIL}" == "PASS" ]]; then
  mkdir -p "$HOST_STAGE_DIR"
  cp -f "$HOST_SUPERVISOR" "$STAGED_HOST_SUPERVISOR"
  chmod 700 "$STAGED_HOST_SUPERVISOR"
  if host_run "'$STAGED_HOST_SUPERVISOR' >/dev/null 2>&1"; then
    pass HOST_BINARY "Android/arm64 supervisor executes natively on Termux host"
  else
    fail HOST_BINARY "cross-built supervisor did not execute on Termux host"
  fi
fi

if [[ "${RESULTS[HOST_BINARY]:-FAIL}" == "PASS" ]]; then
  if start_host_observer && sleep 2 && host_pid_alive "$HOST_WATCHDOG_PID"; then
    deadline=$((SECONDS + 20))
    while ((SECONDS < deadline)) && ! grep -q 'WATCHDOG_OBSERVE_ONLY=ACTIVE' "$HOST_WATCHDOG_LOG" 2>/dev/null; do
      sleep 1
    done
    if grep -q 'WATCHDOG_OBSERVE_ONLY=ACTIVE' "$HOST_WATCHDOG_LOG" 2>/dev/null; then
      pass WATCHDOG_OBSERVE "host pid=$HOST_WATCHDOG_PID observe-only active beside legacy watchdog"
    else
      fail WATCHDOG_OBSERVE "host watchdog started but activation log missing"
    fi
  else
    fail WATCHDOG_OBSERVE "failed to start host observe-only watchdog"
  fi
else
  fail WATCHDOG_OBSERVE "skipped because host binary failed"
fi

if [[ "${RESULTS[WATCHDOG_OBSERVE]:-FAIL}" == "PASS" ]]; then
  launch_mcp
  if wait_for_log "$DEBIAN_SUPERVISOR_PID" "$MCP_LOG" 'MCP lifecycle startup:' "$STARTUP_TIMEOUT"; then
    startup_line="$(grep 'MCP lifecycle startup:' "$MCP_LOG" | tail -1)"
    if ready_line_ok "$startup_line"; then
      rss_before="$(sum_rss_kb "$DEBIAN_SUPERVISOR_PID")"
      info "MCP startup RSS=$((rss_before / 1024)) MiB"
      sleep "$CANARY_SECONDS"
      health_line="$(grep 'MCP lifecycle health:' "$MCP_LOG" | tail -1 || true)"
      if [[ -n "$health_line" ]] && ready_line_ok "$health_line" && host_pid_alive "$HOST_WATCHDOG_PID"; then
        pass MCP_COEXIST "$health_line"
      else
        fail MCP_COEXIST "MCP health or host observe watchdog failed during coexist soak"
      fi
    else
      fail MCP_COEXIST "$startup_line"
      rss_before=0
    fi
  else
    fail MCP_COEXIST "combined MCP did not become ready within ${STARTUP_TIMEOUT}s"
    rss_before=0
  fi
else
  fail MCP_COEXIST "skipped because host observe watchdog failed"
  rss_before=0
fi

if [[ "${RESULTS[MCP_COEXIST]:-FAIL}" == "PASS" ]]; then
  pane_after="$(bot_pane_pid)"
  mapfile -t legacy_after < <(legacy_watchdog_pids)
  stability_ok=1
  [[ "$pane_after" == "$pane_before" ]] || stability_ok=0
  [[ "${legacy_after[*]}" == "${legacy_before[*]}" ]] || stability_ok=0
  worker_lock_held || stability_ok=0
  host_run '"$HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1' || stability_ok=0

  if ((stability_ok == 1)); then
    pass BOT_STABILITY "tmux pane/worker lock/legacy watchdog/local health unchanged"
  else
    fail BOT_STABILITY "production bot topology or health changed during canary"
  fi

  rss_after="$(sum_rss_kb "$DEBIAN_SUPERVISOR_PID")"
  rss_delta=$((rss_after - rss_before))
  host_rss="$(host_run "ps -o rss= -p $HOST_WATCHDOG_PID 2>/dev/null | awk 'NF {print \$1; exit}'" || true)"
  [[ "$host_rss" =~ ^[0-9]+$ ]] || host_rss=0
  info "MCP RSS=$((rss_after / 1024)) MiB delta=$((rss_delta / 1024)) MiB; host watchdog RSS=$((host_rss / 1024)) MiB"
  if ((rss_after <= 1048576 && rss_delta <= 262144 && host_rss <= 131072)); then
    pass MEMORY "mcp=$((rss_after / 1024))MiB delta=$((rss_delta / 1024))MiB host_watchdog=$((host_rss / 1024))MiB"
  else
    fail MEMORY "mcp=$((rss_after / 1024))MiB delta=$((rss_delta / 1024))MiB host_watchdog=$((host_rss / 1024))MiB"
  fi

  if grep -Eq 'BOT_SESSION_RESTART|LOCAL_SHARED_COMPONENT_REPAIR=(PASS|FAIL)' "$HOST_WATCHDOG_LOG" 2>/dev/null; then
    fail MUTATION_GUARD "observe-only watchdog emitted a mutating action log"
  else
    pass MUTATION_GUARD "no bot restart or shared-component repair attempted"
  fi
else
  fail BOT_STABILITY "skipped because MCP coexist failed"
  fail MEMORY "skipped because MCP coexist failed"
  fail MUTATION_GUARD "skipped because MCP coexist failed"
fi

cleanup_ok=1
if [[ -n "$DEBIAN_SUPERVISOR_PID" ]]; then
  mapfile -t mcp_tree < <(all_tree_pids "$DEBIAN_SUPERVISOR_PID")
  stop_debian_supervisor "$DEBIAN_SUPERVISOR_PID" || cleanup_ok=0
  DEBIAN_SUPERVISOR_PID=""
  for pid in "${mcp_tree[@]}"; do
    kill -0 "$pid" 2>/dev/null && cleanup_ok=0
  done
fi
if [[ -n "$HOST_WATCHDOG_PID" ]]; then
  observer_pid="$HOST_WATCHDOG_PID"
  stop_host_watchdog "$observer_pid"
  HOST_WATCHDOG_PID=""
  host_pid_alive "$observer_pid" && cleanup_ok=0
fi

if [[ "${RESULTS[HOST_BRIDGE]:-FAIL}" == "PASS" ]]; then
  pane_final="$(bot_pane_pid || true)"
  mapfile -t legacy_final < <(legacy_watchdog_pids)
  [[ "${pane_final:-}" == "${pane_before:-}" ]] || cleanup_ok=0
  [[ "${legacy_final[*]}" == "${legacy_before[*]:-}" ]] || cleanup_ok=0
fi

if ((cleanup_ok == 1)); then
  pass CLEANUP "canary processes exited; production bot/watchdog remained untouched"
else
  fail CLEANUP "canary cleanup or production post-check failed"
fi

print_summary

rm -f "$STAGED_HOST_SUPERVISOR" 2>/dev/null || true

if ((OVERALL_FAIL == 0)); then
  exit 0
fi
exit 1
