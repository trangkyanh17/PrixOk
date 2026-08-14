#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_BRANCH="rewrite/rust-go-ts-v150"
BUILD_JOBS="${ATRI_BUILD_JOBS:-2}"
STARTUP_TIMEOUT="${ATRI_ALL_IN_ONE_STARTUP_TIMEOUT:-300}"
HEALTH_INTERVAL="${ATRI_ALL_IN_ONE_HEALTH_INTERVAL:-15}"
SOAK_SECONDS="${ATRI_ALL_IN_ONE_SOAK_SECONDS:-35}"
LOG_TIMEZONE="${ATRI_LOG_TIMEZONE:-Asia/Ho_Chi_Minh}"
TEST_ID="$(date +%Y%m%d-%H%M%S)-$$"
STATE_DIR="${TMPDIR:-/tmp}/atri-v150-all-in-one-${TEST_ID}"
SUPERVISOR="$ROOT_DIR/target/release/atri-supervisor"
NATIVE="$ROOT_DIR/target/release/atri-native"
CANARY_SESSION="atri-v150-canary-${TEST_ID}"
TEST_SUPERVISOR_PID=""
CANARY_ACTIVE=0
OVERALL_FAIL=0

declare -A RESULTS=()
declare -A DETAILS=()
RESULT_ORDER=(
  REPO
  BUILD
  GO_TESTS
  NATIVE_HASH
  MCP_STARTUP
  MCP_HEALTH
  MCP_RECONNECT
  MEMORY
  TIMEZONE
  RESTART
  SHUTDOWN
  ORPHAN_CLEANUP
  WATCHDOG_CANARY
)

positive_int() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

for pair in \
  "ATRI_BUILD_JOBS=$BUILD_JOBS" \
  "ATRI_ALL_IN_ONE_STARTUP_TIMEOUT=$STARTUP_TIMEOUT" \
  "ATRI_ALL_IN_ONE_HEALTH_INTERVAL=$HEALTH_INTERVAL" \
  "ATRI_ALL_IN_ONE_SOAK_SECONDS=$SOAK_SECONDS"; do
  name="${pair%%=*}"
  value="${pair#*=}"
  if ! positive_int "$value"; then
    echo "$name must be a positive integer; got: $value" >&2
    exit 2
  fi
done

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
REPORT="$REPORT_DIR/atri-v150-all-in-one-$(date +%Y%m%d-%H%M%S).txt"
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

warn() {
  printf '[WARN] %s\n' "$*"
}

info() {
  printf '[INFO] %s\n' "$*"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

proc_cwd() {
  readlink -f "/proc/$1/cwd" 2>/dev/null || true
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

stop_supervisor_pid() {
  local pid="$1"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
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

cleanup_clone_supervisors() {
  local pid cwd
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    cwd="$(proc_cwd "$pid")"
    if [[ "$cwd" == "$ROOT_DIR" ]]; then
      info "Stopping previous rewrite supervisor pid=$pid from $cwd"
      stop_supervisor_pid "$pid" || warn "Previous rewrite supervisor pid=$pid required SIGKILL"
    else
      info "Leaving unrelated atri-supervisor pid=$pid cwd=${cwd:-unknown} untouched"
    fi
  done < <(pgrep -x atri-supervisor 2>/dev/null || true)
}

cleanup() {
  local rc=$?
  if [[ -n "${TEST_SUPERVISOR_PID:-}" ]]; then
    stop_supervisor_pid "$TEST_SUPERVISOR_PID" >/dev/null 2>&1 || true
    TEST_SUPERVISOR_PID=""
  fi
  if ((CANARY_ACTIVE == 1)) && command_exists tmux; then
    tmux kill-session -t "$CANARY_SESSION" >/dev/null 2>&1 || true
    CANARY_ACTIVE=0
  fi
  if ((rc != 0)) && ((OVERALL_FAIL == 0)); then
    OVERALL_FAIL=1
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

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
          if (seen[parent[pid]] && !seen[pid]) {
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

print_tree_snapshot() {
  local root="$1"
  local list
  list="$(all_tree_pids "$root" | paste -sd, -)"
  [[ -n "$list" ]] || return 0
  ps -o pid,ppid,%cpu,%mem,rss,etime,comm,args -p "$list" --sort=-rss 2>/dev/null || true
}

wait_for_log() {
  local pid="$1"
  local logfile="$2"
  local pattern="$3"
  local timeout_seconds="$4"
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if grep -Eq "$pattern" "$logfile" 2>/dev/null; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    sleep 2
  done
  return 1
}

ready_line_ok() {
  local line="$1"
  local plugin
  for plugin in context7 semgrep serena; do
    if ! grep -Eq "${plugin}=ready:[1-9][0-9]*" <<<"$line"; then
      return 1
    fi
  done
  return 0
}

launch_mcp() {
  local label="$1"
  local logfile="$2"
  : >"$logfile"
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
    ATRI_MCP_PRUNE_INTERVAL=10 \
    ATRI_MCP_IDLE_TTL=600 \
    ATRI_REWRITE_SHUTDOWN_TIMEOUT=15 \
    "$SUPERVISOR"
  ) >"$logfile" 2>&1 &
  TEST_SUPERVISOR_PID=$!
  info "$label supervisor pid=$TEST_SUPERVISOR_PID"
}

check_local_timestamp() {
  local line="$1"
  local stamp now_epoch log_epoch delta
  stamp="${line:0:19}"
  now_epoch="$(date +%s)"
  log_epoch="$(TZ="$LOG_TIMEZONE" date -d "$stamp" +%s 2>/dev/null || true)"
  [[ "$log_epoch" =~ ^[0-9]+$ ]] || return 1
  delta=$((now_epoch - log_epoch))
  ((delta < 0)) && delta=$((-delta))
  ((delta <= 300))
}

wait_for_health_after_line_count() {
  local pid="$1"
  local logfile="$2"
  local old_count="$3"
  local timeout_seconds="$4"
  local deadline=$((SECONDS + timeout_seconds))
  local count line
  while ((SECONDS < deadline)); do
    count="$(grep -c 'MCP lifecycle health:' "$logfile" 2>/dev/null || true)"
    if [[ "$count" =~ ^[0-9]+$ ]] && ((count > old_count)); then
      line="$(grep 'MCP lifecycle health:' "$logfile" | tail -1)"
      if ready_line_ok "$line"; then
        return 0
      fi
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      return 1
    fi
    sleep 2
  done
  return 1
}

echo "===== ATRI V150 ALL-IN-ONE TERMUX VALIDATION ====="
echo "START: $(date)"
echo "REPORT: $REPORT"
echo "STATE: $STATE_DIR"
echo

cd "$ROOT_DIR"

if [[ "$(git branch --show-current 2>/dev/null || true)" != "$EXPECTED_BRANCH" ]]; then
  fail REPO "expected branch $EXPECTED_BRANCH"
else
  head_sha="$(git rev-parse HEAD 2>/dev/null || true)"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    fail REPO "tracked working tree is dirty at ${head_sha:-unknown}"
  else
    pass REPO "branch=$EXPECTED_BRANCH head=$head_sha"
  fi
fi

if [[ "${RESULTS[REPO]:-FAIL}" != "PASS" ]]; then
  echo
  echo "===== FINAL SUMMARY ====="
  printf '%-20s %-5s %s\n' "REPO" "${RESULTS[REPO]:-FAIL}" "${DETAILS[REPO]:-repository validation failed}"
  echo "OVERALL              FAIL"
  echo "REPORT: $REPORT"
  exit 1
fi

cleanup_clone_supervisors

if [[ ! -x "$SUPERVISOR" || ! -x "$NATIVE" ]]; then
  info "Native binaries missing; running full optimized build"
  if ATRI_BUILD_JOBS="$BUILD_JOBS" ./termux-build.sh; then
    pass BUILD "full optimized build completed"
  else
    fail BUILD "full build failed"
  fi
else
  info "Native binaries already present; rebuilding supervisor only"
  if ATRI_BUILD_JOBS="$BUILD_JOBS" ./termux-build.sh --supervisor-only; then
    pass BUILD "supervisor-only rebuild completed"
  else
    fail BUILD "supervisor build failed"
  fi
fi

if [[ "${RESULTS[BUILD]:-FAIL}" != "PASS" ]]; then
  warn "Skipping runtime tests because build failed"
else
  if (cd "$ROOT_DIR/supervisor" && go test ./...); then
    pass GO_TESTS "go test ./... completed"
  else
    fail GO_TESTS "Go tests failed"
  fi

  hash_file="$STATE_DIR/hash.txt"
  printf 'abc' >"$hash_file"
  expected_hash="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
  native_hash="$("$NATIVE" hash "$hash_file" 2>/dev/null | tr -d '\r\n' || true)"
  if [[ "$native_hash" == "$expected_hash" ]]; then
    pass NATIVE_HASH "$native_hash"
  else
    fail NATIVE_HASH "unexpected hash: ${native_hash:-empty}"
  fi

  mcp_log="$STATE_DIR/mcp-primary.log"
  launch_mcp "primary" "$mcp_log"

  if wait_for_log "$TEST_SUPERVISOR_PID" "$mcp_log" 'MCP lifecycle startup:' "$STARTUP_TIMEOUT"; then
    startup_line="$(grep 'MCP lifecycle startup:' "$mcp_log" | tail -1)"
    if ready_line_ok "$startup_line"; then
      pass MCP_STARTUP "$startup_line"
    else
      fail MCP_STARTUP "$startup_line"
    fi
    if check_local_timestamp "$startup_line"; then
      pass TIMEZONE "supervisor timestamp matches $LOG_TIMEZONE"
    else
      fail TIMEZONE "timestamp mismatch: $startup_line"
    fi
  else
    fail MCP_STARTUP "startup did not become ready within ${STARTUP_TIMEOUT}s"
    fail TIMEZONE "no startup timestamp available"
    tail -100 "$mcp_log" || true
  fi

  if [[ "${RESULTS[MCP_STARTUP]:-FAIL}" == "PASS" ]]; then
    rss_before="$(sum_rss_kb "$TEST_SUPERVISOR_PID")"
    info "MCP RSS after startup: $((rss_before / 1024)) MiB"
    sleep "$SOAK_SECONDS"
    health_line="$(grep 'MCP lifecycle health:' "$mcp_log" | tail -1 || true)"
    if [[ -n "$health_line" ]] && ready_line_ok "$health_line"; then
      pass MCP_HEALTH "$health_line"
    else
      fail MCP_HEALTH "no healthy combined health cycle after ${SOAK_SECONDS}s"
    fi
    rss_after="$(sum_rss_kb "$TEST_SUPERVISOR_PID")"
    rss_delta=$((rss_after - rss_before))
    info "MCP RSS after soak: $((rss_after / 1024)) MiB (delta=$((rss_delta / 1024)) MiB)"
    if ((rss_after <= 1048576 && rss_delta <= 262144)); then
      pass MEMORY "rss=$((rss_after / 1024))MiB delta=$((rss_delta / 1024))MiB"
    else
      fail MEMORY "rss=$((rss_after / 1024))MiB delta=$((rss_delta / 1024))MiB"
    fi

    echo "----- MCP process snapshot -----"
    print_tree_snapshot "$TEST_SUPERVISOR_PID"

    semgrep_pid=""
    while read -r candidate; do
      args="$(ps -o args= -p "$candidate" 2>/dev/null || true)"
      if [[ "$args" == *"pysemgrep mcp"* ]]; then
        semgrep_pid="$candidate"
        break
      fi
    done < <(descendant_pids "$TEST_SUPERVISOR_PID")

    if [[ -n "$semgrep_pid" ]]; then
      old_health_count="$(grep -c 'MCP lifecycle health:' "$mcp_log" 2>/dev/null || true)"
      info "Injecting isolated Semgrep child failure pid=$semgrep_pid"
      kill -KILL "$semgrep_pid" 2>/dev/null || true
      if wait_for_health_after_line_count "$TEST_SUPERVISOR_PID" "$mcp_log" "$old_health_count" 90; then
        new_semgrep_pid=""
        while read -r candidate; do
          args="$(ps -o args= -p "$candidate" 2>/dev/null || true)"
          if [[ "$args" == *"pysemgrep mcp"* ]]; then
            new_semgrep_pid="$candidate"
            break
          fi
        done < <(descendant_pids "$TEST_SUPERVISOR_PID")
        if [[ -n "$new_semgrep_pid" && "$new_semgrep_pid" != "$semgrep_pid" ]]; then
          pass MCP_RECONNECT "semgrep recovered old_pid=$semgrep_pid new_pid=$new_semgrep_pid"
        else
          fail MCP_RECONNECT "health recovered but Semgrep child PID did not change"
        fi
      else
        fail MCP_RECONNECT "Semgrep did not recover within 90s"
      fi
    else
      fail MCP_RECONNECT "could not identify Semgrep child for fault injection"
    fi
  else
    fail MCP_HEALTH "skipped because startup failed"
    fail MEMORY "skipped because startup failed"
    fail MCP_RECONNECT "skipped because startup failed"
  fi

  mapfile -t primary_tree < <(all_tree_pids "$TEST_SUPERVISOR_PID")
  shutdown_started="$(date +%s)"
  primary_pid="$TEST_SUPERVISOR_PID"
  if stop_supervisor_pid "$primary_pid"; then
    shutdown_elapsed=$(( $(date +%s) - shutdown_started ))
    if ((shutdown_elapsed <= 20)); then
      pass SHUTDOWN "SIGTERM exit in ${shutdown_elapsed}s"
    else
      fail SHUTDOWN "SIGTERM exit took ${shutdown_elapsed}s"
    fi
  else
    fail SHUTDOWN "supervisor required SIGKILL"
  fi
  TEST_SUPERVISOR_PID=""

  orphaned=()
  for pid in "${primary_tree[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      orphaned+=("$pid")
    fi
  done
  if ((${#orphaned[@]} == 0)); then
    pass ORPHAN_CLEANUP "all captured MCP processes exited"
  else
    fail ORPHAN_CLEANUP "still alive: ${orphaned[*]}"
  fi

  restart_log="$STATE_DIR/mcp-restart.log"
  launch_mcp "restart" "$restart_log"
  if wait_for_log "$TEST_SUPERVISOR_PID" "$restart_log" 'MCP lifecycle startup:' "$STARTUP_TIMEOUT"; then
    restart_line="$(grep 'MCP lifecycle startup:' "$restart_log" | tail -1)"
    if ready_line_ok "$restart_line"; then
      sleep $((HEALTH_INTERVAL + 3))
      restart_health="$(grep 'MCP lifecycle health:' "$restart_log" | tail -1 || true)"
      if [[ -n "$restart_health" ]] && ready_line_ok "$restart_health"; then
        pass RESTART "combined MCP restarted and passed health"
      else
        fail RESTART "restart startup ready but health failed"
      fi
    else
      fail RESTART "$restart_line"
    fi
  else
    fail RESTART "restart did not become ready within ${STARTUP_TIMEOUT}s"
  fi
  restart_pid="$TEST_SUPERVISOR_PID"
  mapfile -t restart_tree < <(all_tree_pids "$restart_pid")
  stop_supervisor_pid "$restart_pid" || true
  TEST_SUPERVISOR_PID=""
  restart_orphans=()
  for pid in "${restart_tree[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      restart_orphans+=("$pid")
    fi
  done
  if ((${#restart_orphans[@]} > 0)); then
    fail ORPHAN_CLEANUP "restart left processes alive: ${restart_orphans[*]}"
  fi

  if command_exists tmux; then
    health_ok="$STATE_DIR/health-ok.sh"
    health_bad="$STATE_DIR/health-bad.sh"
    network_ok="$STATE_DIR/network-ok.sh"
    repair_ok="$STATE_DIR/repair-ok.sh"
    launcher="$STATE_DIR/launcher.sh"
    network_marker="$STATE_DIR/network.marker"
    repair_marker="$STATE_DIR/repair.marker"
    launcher_marker="$STATE_DIR/launcher.marker"

    cat >"$health_ok" <<'EOF'
#!/bin/sh
exit 0
EOF
    cat >"$health_bad" <<'EOF'
#!/bin/sh
exit 1
EOF
    cat >"$network_ok" <<EOF
#!/bin/sh
printf 'network\n' >>'$network_marker'
exit 0
EOF
    cat >"$repair_ok" <<EOF
#!/bin/sh
printf 'repair\n' >>'$repair_marker'
exit 0
EOF
    cat >"$launcher" <<EOF
#!/bin/sh
printf 'launcher\n' >>'$launcher_marker'
exec sleep 300
EOF
    chmod 700 "$health_ok" "$health_bad" "$network_ok" "$repair_ok" "$launcher"

    tmux kill-session -t "$CANARY_SESSION" >/dev/null 2>&1 || true
    if tmux new-session -d -s "$CANARY_SESSION" "sleep 300"; then
      CANARY_ACTIVE=1

      watchdog_log="$STATE_DIR/watchdog-healthy.log"
      (
        cd "$ROOT_DIR"
        ATRI_LOG_TIMEZONE="$LOG_TIMEZONE" \
        ATRI_REWRITE_WATCHDOG=true \
        ATRI_REWRITE_MCP_LIFECYCLE=false \
        ATRI_BOT_SESSION="$CANARY_SESSION" \
        ATRI_BOT_LAUNCHER="$launcher" \
        ATRI_LOCAL_HEALTH="$health_ok" \
        ATRI_BROWSER_ENSURE="$repair_ok" \
        ATRI_NETWORK_STATE="$network_ok" \
        ATRI_WATCHDOG_INTERVAL=2 \
        ATRI_WATCHDOG_COMMAND_TIMEOUT=3 \
        ATRI_WATCHDOG_REPAIR_TIMEOUT=3 \
        ATRI_NETWORK_INTERVAL=2 \
        ATRI_NETWORK_TIMEOUT=2 \
        ATRI_REWRITE_SHUTDOWN_TIMEOUT=5 \
        "$SUPERVISOR"
      ) >"$watchdog_log" 2>&1 &
      TEST_SUPERVISOR_PID=$!
      sleep 6
      healthy_pid="$TEST_SUPERVISOR_PID"
      stop_supervisor_pid "$healthy_pid" || true
      TEST_SUPERVISOR_PID=""

      healthy_ok=1
      grep -q 'NETWORK_STATE=ONLINE' "$watchdog_log" || healthy_ok=0
      [[ -s "$network_marker" ]] || healthy_ok=0
      [[ ! -e "$repair_marker" ]] || healthy_ok=0
      [[ ! -e "$launcher_marker" ]] || healthy_ok=0

      rm -f "$repair_marker" "$launcher_marker"
      watchdog_repair_log="$STATE_DIR/watchdog-repair.log"
      (
        cd "$ROOT_DIR"
        ATRI_LOG_TIMEZONE="$LOG_TIMEZONE" \
        ATRI_REWRITE_WATCHDOG=true \
        ATRI_REWRITE_MCP_LIFECYCLE=false \
        ATRI_BOT_SESSION="$CANARY_SESSION" \
        ATRI_BOT_LAUNCHER="$launcher" \
        ATRI_LOCAL_HEALTH="$health_bad" \
        ATRI_BROWSER_ENSURE="$repair_ok" \
        ATRI_NETWORK_STATE="$network_ok" \
        ATRI_WATCHDOG_INTERVAL=2 \
        ATRI_WATCHDOG_COMMAND_TIMEOUT=3 \
        ATRI_WATCHDOG_REPAIR_TIMEOUT=3 \
        ATRI_NETWORK_INTERVAL=2 \
        ATRI_NETWORK_TIMEOUT=2 \
        ATRI_REWRITE_SHUTDOWN_TIMEOUT=5 \
        "$SUPERVISOR"
      ) >"$watchdog_repair_log" 2>&1 &
      TEST_SUPERVISOR_PID=$!
      sleep 6
      repair_pid="$TEST_SUPERVISOR_PID"
      stop_supervisor_pid "$repair_pid" || true
      TEST_SUPERVISOR_PID=""

      repair_ok_result=1
      grep -q 'LOCAL_SHARED_COMPONENT_HEALTH=UNHEALTHY' "$watchdog_repair_log" || repair_ok_result=0
      grep -q 'LOCAL_SHARED_COMPONENT_REPAIR=PASS' "$watchdog_repair_log" || repair_ok_result=0
      [[ -s "$repair_marker" ]] || repair_ok_result=0
      [[ ! -e "$launcher_marker" ]] || repair_ok_result=0

      if ((healthy_ok == 1 && repair_ok_result == 1)); then
        pass WATCHDOG_CANARY "healthy/network and isolated repair paths passed; production session untouched"
      else
        fail WATCHDOG_CANARY "canary assertion failed"
        echo "----- watchdog healthy log -----"
        cat "$watchdog_log" || true
        echo "----- watchdog repair log -----"
        cat "$watchdog_repair_log" || true
      fi

      tmux kill-session -t "$CANARY_SESSION" >/dev/null 2>&1 || true
      CANARY_ACTIVE=0
    else
      fail WATCHDOG_CANARY "failed to create isolated tmux session"
    fi
  else
    fail WATCHDOG_CANARY "tmux is not available"
  fi
fi

echo
echo "===== FINAL SUMMARY ====="
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

for log_file in \
  "$STATE_DIR/mcp-primary.log" \
  "$STATE_DIR/mcp-restart.log" \
  "$STATE_DIR/watchdog-healthy.log" \
  "$STATE_DIR/watchdog-repair.log"; do
  if [[ -f "$log_file" ]]; then
    echo
    echo "===== LOG: $(basename "$log_file") ====="
    cat "$log_file"
  fi
done

if ((OVERALL_FAIL == 0)); then
  exit 0
fi
exit 1
