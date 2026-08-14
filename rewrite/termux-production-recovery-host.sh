#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

EXPECTED_BRANCH="rewrite/rust-go-ts-v150"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
BUILD_JOBS="${ATRI_BUILD_JOBS:-2}"
BOT_START_TIMEOUT="${ATRI_PRODUCTION_RECOVERY_BOT_TIMEOUT:-240}"
MCP_STARTUP_TIMEOUT="${ATRI_PRODUCTION_RECOVERY_MCP_TIMEOUT:-300}"
MCP_HEALTH_INTERVAL="${ATRI_PRODUCTION_RECOVERY_MCP_HEALTH_INTERVAL:-15}"
MCP_SOAK_SECONDS="${ATRI_PRODUCTION_RECOVERY_MCP_SOAK_SECONDS:-45}"
HANDOFF_VERIFY_SECONDS="${ATRI_PRODUCTION_RECOVERY_HANDOFF_VERIFY_SECONDS:-45}"
LOG_TIMEZONE="${ATRI_LOG_TIMEZONE:-Asia/Ho_Chi_Minh}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
TEST_ID="$(date +%Y%m%d-%H%M%S)-$$"
STATE_DIR="$HOST_HOME/.cache/atri-v150-production-recovery/$TEST_ID"
V150_INSTALL_DIR="$HOST_HOME/.local/lib/atri-v150"
V150_HOST_BIN="$V150_INSTALL_DIR/atri-supervisor"
V150_LAUNCHER="$HOST_HOME/atri-v150-production-watchdog.sh"
V150_LOG="$HOST_HOME/.atri-v150-production-watchdog.log"
MCP_LOG="$STATE_DIR/mcp.log"
MCP_PIDFILE="/tmp/atri-v150-production-recovery-$TEST_ID.pid"
MCP_OUTER_PID=""
MCP_PID=""
V150_PID=""
BOT_CREATED=0
LEGACY_STOPPED=0
ORIGINAL_LEGACY_PIDS=()
OVERALL_FAIL=0
ROLLBACK_NEEDED=0
HANDOFF_IN_PROGRESS=0
HANDOFF_COMMITTED=0

RESULT_ORDER=(
  HOST_CONTEXT
  REPO
  SOURCE_SNAPSHOT
  BUILD
  BOT_RECOVERY
  BOT_LOCK
  PROD_REPAIR
  PROD_HEALTH
  MCP_COEXIST
  MEMORY
  SOURCE_UNCHANGED
  WATCHDOG_HANDOFF
  BOT_STABILITY
  ROLLBACK
  CLEANUP
)
declare -A RESULTS=()
declare -A DETAILS=()

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=termux-production-recovery-common.sh
source "$SCRIPT_DIR/termux-production-recovery-common.sh"

for pair in \
  "ATRI_BUILD_JOBS=$BUILD_JOBS" \
  "ATRI_PRODUCTION_RECOVERY_BOT_TIMEOUT=$BOT_START_TIMEOUT" \
  "ATRI_PRODUCTION_RECOVERY_MCP_TIMEOUT=$MCP_STARTUP_TIMEOUT" \
  "ATRI_PRODUCTION_RECOVERY_MCP_HEALTH_INTERVAL=$MCP_HEALTH_INTERVAL" \
  "ATRI_PRODUCTION_RECOVERY_MCP_SOAK_SECONDS=$MCP_SOAK_SECONDS" \
  "ATRI_PRODUCTION_RECOVERY_HANDOFF_VERIFY_SECONDS=$HANDOFF_VERIFY_SECONDS"; do
  name="${pair%%=*}"
  value="${pair#*=}"
  if ! positive_int "$value"; then
    echo "$name must be a positive integer; got: $value" >&2
    exit 2
  fi
done

if [[ "${1:-}" == "--self-test" ]]; then
  [[ "$(classify_runtime PRESENT HELD HEALTHY)" == "HEALTHY" ]]
  [[ "$(classify_runtime MISSING FREE UNHEALTHY)" == "STOPPED" ]]
  [[ "$(classify_runtime MISSING MISSING UNHEALTHY)" == "STOPPED" ]]
  [[ "$(classify_runtime MISSING HELD UNHEALTHY)" == "WORKER_OUTSIDE_TMUX" ]]
  [[ "$(classify_runtime PRESENT FREE UNHEALTHY)" == "SESSION_WITHOUT_WORKER" ]]
  positive_int "$BUILD_JOBS"
  for self_file in "$0" "$SCRIPT_DIR/termux-production-recovery-common.sh" "$SCRIPT_DIR/termux-production-recovery-run.sh"; do
    if grep -Eq 'git[[:space:]]+(pull|reset|checkout|clean)|update\.py' "$self_file"; then
      echo "production recovery self-test: FAIL (source mutation pattern found in $self_file)" >&2
      exit 1
    fi
  done
  echo "production recovery self-test: PASS"
  exit 0
fi
if (($#)); then
  echo "Usage: ./termux-production-recovery-host.sh [--self-test]" >&2
  exit 2
fi

mkdir -p "$STATE_DIR"

REPORT_DIR="$(choose_report_dir)"
mkdir -p "$REPORT_DIR"
REPORT="$REPORT_DIR/atri-v150-production-recovery-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

trap cleanup EXIT
trap 'exit 130' INT TERM

echo "===== ATRI V150 PRODUCTION RECOVERY + INTEGRATION ====="
echo "START: $(date)"
echo "REPORT: $REPORT"
echo "MODE: source-preserving recovery, MCP coexistence, controlled watchdog handoff"
echo

ROOTFS_DIR=""
if [[ "$HOST_PREFIX" != "/data/data/com.termux/files/usr" ]] || \
   [[ ! -x "$HOST_PREFIX/bin/proot-distro" ]] || \
   [[ ! -x "$HOST_PREFIX/bin/tmux" ]]; then
  fail HOST_CONTEXT "must run from Termux host; prefix=$HOST_PREFIX"
elif [[ -f /etc/debian_version ]]; then
  fail HOST_CONTEXT "detected Debian/PROot context; exit Debian before running"
else
  ROOTFS_DIR="$(find_rootfs || true)"
  if [[ -z "$ROOTFS_DIR" ]]; then
    fail HOST_CONTEXT "cannot locate Debian rootfs containing $DEBIAN_CLONE"
  else
    pass HOST_CONTEXT "Termux host confirmed rootfs=$ROOTFS_DIR"
  fi
fi

if [[ "${RESULTS[HOST_CONTEXT]:-FAIL}" == PASS ]]; then
  section "REPO PREFLIGHT"
  repo_meta="$(debian_run "cd '$DEBIAN_CLONE' && printf 'branch=%s\\n' \"\$(git branch --show-current)\" && printf 'head=%s\\n' \"\$(git rev-parse HEAD)\" && if git diff --quiet && git diff --cached --quiet; then echo clean=1; else echo clean=0; fi" 2>/dev/null || true)"
  printf '%s\n' "$repo_meta"
  repo_branch="$(awk -F= '$1=="branch"{print $2}' <<<"$repo_meta")"
  repo_head="$(awk -F= '$1=="head"{print $2}' <<<"$repo_meta")"
  repo_clean="$(awk -F= '$1=="clean"{print $2}' <<<"$repo_meta")"
  if [[ "$repo_branch" == "$EXPECTED_BRANCH" && "$repo_clean" == 1 && "$repo_head" =~ ^[0-9a-f]{40}$ ]]; then
    pass REPO "branch=$repo_branch head=$repo_head"
  else
    fail REPO "branch=${repo_branch:-unknown} head=${repo_head:-unknown} clean=${repo_clean:-unknown}"
  fi
fi

if [[ "${RESULTS[REPO]:-FAIL}" == PASS ]]; then
  section "PRODUCTION SOURCE SNAPSHOT"
  SOURCE_BEFORE="$(source_fingerprint || true)"
  printf '%s\n' "$SOURCE_BEFORE"
  APP_HEAD_BEFORE="$(awk -F= '$1=="head"{print $2}' <<<"$SOURCE_BEFORE")"
  APP_BRANCH_BEFORE="$(awk -F= '$1=="branch"{print $2}' <<<"$SOURCE_BEFORE")"
  debian_run 'cd /app && git status --short --branch 2>/dev/null || true'
  if [[ "$APP_HEAD_BEFORE" =~ ^[0-9a-f]{40}$ ]]; then
    pass SOURCE_SNAPSHOT "branch=$APP_BRANCH_BEFORE head=$APP_HEAD_BEFORE; no source mutation commands permitted"
  else
    fail SOURCE_SNAPSHOT "unable to fingerprint production /app"
  fi
fi

for helper in prixok-bot.sh atri-production-watchdog.sh atri-production-local-health.sh atri-production-browser-ensure.sh atri-production-network-state.sh; do
  if [[ ! -x "$HOST_HOME/$helper" ]] || ! "$HOST_PREFIX/bin/bash" -n "$HOST_HOME/$helper"; then
    fail SOURCE_SNAPSHOT "production helper invalid: $helper"
    break
  fi
done

if [[ "${RESULTS[SOURCE_SNAPSHOT]:-FAIL}" == PASS ]]; then
  section "BUILD V150 SUPERVISORS"
  if debian_run "cd '$DEBIAN_CLONE/rewrite' && ATRI_BUILD_JOBS='$BUILD_JOBS' ./termux-build.sh --supervisor-only && ATRI_BUILD_JOBS='$BUILD_JOBS' ./termux-build.sh --host-watchdog-only"; then
    mkdir -p "$V150_INSTALL_DIR"
    install -m 700 "$ROOTFS_DIR$DEBIAN_CLONE/rewrite/target/release/atri-supervisor-android-arm64" "$V150_HOST_BIN.tmp"
    mv -f "$V150_HOST_BIN.tmp" "$V150_HOST_BIN"
    install -m 700 "$ROOTFS_DIR$DEBIAN_CLONE/rewrite/termux-v150-production-watchdog.sh" "$V150_LAUNCHER.tmp"
    mv -f "$V150_LAUNCHER.tmp" "$V150_LAUNCHER"
    if "$V150_HOST_BIN" >/dev/null 2>&1; then
      pass BUILD "Debian supervisor + Android/arm64 watchdog built and staged"
    else
      fail BUILD "host binary failed native execution smoke"
    fi
  else
    fail BUILD "V150 build failed"
  fi
fi

if [[ "${RESULTS[BUILD]:-FAIL}" == PASS ]]; then
  section "PRODUCTION PRE-RECOVERY STATE"
  SESSION_BEFORE="$(bot_session_state)"
  LOCK_BEFORE="$(bot_lock_state || echo UNKNOWN)"
  HEALTH_BEFORE="$(local_health_state)"
  NETWORK_BEFORE="$(network_state)"
  mapfile -t ORIGINAL_LEGACY_PIDS < <(legacy_watchdog_pids)
  mapfile -t ORIGINAL_V150_PIDS < <(v150_watchdog_pids)
  printf 'session=%s lock=%s health=%s network=%s legacy=%s v150=%s\n' \
    "$SESSION_BEFORE" "$LOCK_BEFORE" "$HEALTH_BEFORE" "$NETWORK_BEFORE" \
    "${ORIGINAL_LEGACY_PIDS[*]:-none}" "${ORIGINAL_V150_PIDS[*]:-none}"

  if ((${#ORIGINAL_LEGACY_PIDS[@]} > 1 || ${#ORIGINAL_V150_PIDS[@]} > 0)); then
    fail BOT_RECOVERY "unexpected watchdog ownership before recovery legacy=${ORIGINAL_LEGACY_PIDS[*]:-none} v150=${ORIGINAL_V150_PIDS[*]:-none}"
  else
    runtime_class="$(classify_runtime "$SESSION_BEFORE" "$LOCK_BEFORE" "$HEALTH_BEFORE")"
    info "runtime_class=$runtime_class"
    case "$runtime_class" in
      HEALTHY)
        BOT_CREATED=0
        pass BOT_RECOVERY "production bot already healthy"
        ;;
      STOPPED)
        # Bypass atri-production-ensure.sh for this controlled recovery so the
        # launcher cannot perform any opaque source/runtime provisioning. The
        # launcher itself still enforces RUN_SOURCE_UPDATE=0.
        if tmux new-session -d -s prixok-bot 'exec env ATRI_PRODUCTION_LAUNCHER_GUARD=1 bash "$HOME/prixok-bot.sh"'; then
          BOT_CREATED=1
          if wait_for_worker_ready "$BOT_START_TIMEOUT"; then
            pass BOT_RECOVERY "production worker recovered through existing launcher"
          else
            fail BOT_RECOVERY "bot did not reach tmux+singleton-lock within ${BOT_START_TIMEOUT}s"
          fi
        else
          fail BOT_RECOVERY "failed to create prixok-bot tmux session"
        fi
        ;;
      WORKER_OUTSIDE_TMUX)
        fail BOT_RECOVERY "worker lock is held outside tmux; refusing duplicate launch"
        ;;
      SESSION_WITHOUT_WORKER)
        warn "tmux session exists without worker lock; waiting 60s before refusing mutation"
        if wait_for_runtime_healthy 60; then
          pass BOT_RECOVERY "existing session completed startup"
        else
          fail BOT_RECOVERY "existing tmux session has no healthy singleton worker; not killing unknown session"
        fi
        ;;
      *)
        fail BOT_RECOVERY "mixed production state session=$SESSION_BEFORE lock=$LOCK_BEFORE health=$HEALTH_BEFORE"
        ;;
    esac
  fi
fi

if [[ "${RESULTS[BOT_RECOVERY]:-FAIL}" == PASS ]]; then
  LOCK_AFTER="$(bot_lock_state || echo UNKNOWN)"
  PANE_AFTER_RECOVERY="$(bot_pane_pid || true)"
  if [[ "$LOCK_AFTER" == HELD ]]; then pass BOT_LOCK "singleton lock held"; else fail BOT_LOCK "lock=$LOCK_AFTER"; fi

  HEALTH_AFTER="$(local_health_state)"
  if [[ "$HEALTH_AFTER" == HEALTHY ]]; then
    pass PROD_REPAIR "not required"
    pass PROD_HEALTH "local health healthy"
  elif [[ "${RESULTS[BOT_LOCK]:-FAIL}" == PASS ]]; then
    section "ONE-SHOT PRODUCTION SHARED-COMPONENT REPAIR"
    set +e
    timeout 270 "$HOST_HOME/atri-production-browser-ensure.sh" --from-watchdog >/dev/null 2>&1
    repair_rc=$?
    set -e
    if [[ "$repair_rc" -eq 0 ]]; then
      pass PROD_REPAIR "browser/shared-component ensure returned success"
      if wait_for_runtime_healthy 90; then
        pass PROD_HEALTH "local health healthy after one-shot repair"
      else
        fail PROD_HEALTH "local health remained unhealthy after repair"
      fi
    else
      fail PROD_REPAIR "browser/shared-component ensure failed rc=$repair_rc"
      fail PROD_HEALTH "local health=$HEALTH_AFTER"
    fi
  else
    fail PROD_REPAIR "skipped because singleton lock is not held"
    fail PROD_HEALTH "local health=$HEALTH_AFTER"
  fi
fi

# Continue orchestration from the second source file.
source "$SCRIPT_DIR/termux-production-recovery-run.sh"
