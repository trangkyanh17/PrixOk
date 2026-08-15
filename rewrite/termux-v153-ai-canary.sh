#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

EXPECTED_BRANCH="main"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
STATE_DIR="$HOST_HOME/.local/state/atri-v153-ai-guard"
BACKUP_ROOT="$STATE_DIR/backups"
LAST_BACKUP_FILE="$STATE_DIR/last-backup"
V150_BIN="$HOST_HOME/.local/lib/atri-v150/atri-supervisor"
V151_HOST_ENABLE="$HOST_HOME/.local/state/atri-v151-shadow/enabled"
V151_READY_FILE="/root/.local/state/atri-v151-shadow/observer-ready.json"
V152_ENABLE_FILE="/root/.local/state/atri-v152-parity/enabled"
ACTION="${1:-status}"
SHADOW_ADDR="${ATRI_TELEGRAM_SHADOW_ADDR:-127.0.0.1:18750}"
SHADOW_URL="http://$SHADOW_ADDR"
RESTART_TIMEOUT="${ATRI_V153_RESTART_TIMEOUT:-150}"
HEALTH_TIMEOUT="${ATRI_V153_HEALTH_TIMEOUT:-180}"
GITHUB_PROBE_OWNER="${ATRI_V153_GITHUB_PROBE_OWNER:-trangkyanh17}"
GITHUB_PROBE_REPO="${ATRI_V153_GITHUB_PROBE_REPO:-PrixOk}"
GITHUB_PROBE_REF="${ATRI_V153_GITHUB_PROBE_REF:-main}"

ROOTFS_DIR=""
REPO_SHA=""
REPORT=""
APPLY_BACKUP=""
SOURCE_APPLIED=0
BOT_RESTART_ATTEMPTED=0
ROLLBACK_RUNNING=0
NEW_PANE=""
HEALTHY_PANE=""
BOT_LOG_LINES_BEFORE=0

usage() {
  cat <<'EOF'
Usage: termux-v153-ai-canary.sh <command>

Commands:
  status       Read-only V151/V152/V153 and production status.
  apply        Guardedly install the V153 AI guard, restart the Python bot once,
               run an isolated real GitHub public-read probe, and auto-rollback
               on any failure.
  rollback     Restore the exact pre-V153 Python source snapshot and restart once.
  --self-test  CI syntax/contract checks.

This manager never mutates /app with Git and never replaces /app. It touches
only bot/__init__.py and bot/modules/atri_ai_runtime_guard.py through the
transactional V153 patcher. It does not start a second Telegram or AI worker.
EOF
}

positive_int() { [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]; }

validate_shadow_addr() {
  local port
  [[ "$SHADOW_ADDR" =~ ^127\.0\.0\.1:([0-9]{1,5})$ ]] || return 1
  port="${BASH_REMATCH[1]}"
  ((port >= 1 && port <= 65535))
}

validate_github_probe() {
  [[ "$GITHUB_PROBE_OWNER" =~ ^[A-Za-z0-9_.-]{1,100}$ ]] || return 1
  [[ "$GITHUB_PROBE_REPO" =~ ^[A-Za-z0-9_.-]{1,100}$ ]] || return 1
  [[ "$GITHUB_PROBE_REF" =~ ^[A-Za-z0-9._/-]{1,200}$ ]] || return 1
  [[ "$GITHUB_PROBE_REF" != *".."* ]]
}

if [[ "$ACTION" == "--self-test" ]]; then
  [[ "$EXPECTED_BRANCH" == main ]]
  positive_int "$RESTART_TIMEOUT"
  positive_int "$HEALTH_TIMEOUT"
  validate_shadow_addr
  validate_github_probe
  for cmd in status apply rollback; do
    grep -q "^    $cmd)" "$0"
  done
  if grep -Eq '^[[:space:]]*git[[:space:]]+(pull|reset|checkout|clean)|update\.py|rm[[:space:]]+-rf[[:space:]]+/app' "$0"; then
    echo "v153 AI canary self-test: FAIL (forbidden source mutation command)" >&2
    exit 1
  fi
  grep -q 'v153_ai_guard_patch.py' "$0"
  grep -q 'v153_ai_probe.py' "$0"
  grep -q 'env -u GITHUB_PERSONAL_ACCESS_TOKEN -u GITHUB_TOKEN' "$0"
  grep -q 'ATRI_AI_RUNTIME_GUARD_V153_INSTALLED' "$0"
  grep -q 'v152_parity_patch.py' "$0"
  grep -q 'AUTO ROLLBACK' "$0"
  grep -q 'tmux send-keys -t prixok-bot C-c' "$0"
  grep -q 'trap finish_report EXIT' "$0"
  echo "v153 AI canary self-test: PASS"
  exit 0
fi

case "$ACTION" in
  status|apply|rollback) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! positive_int "$RESTART_TIMEOUT" || ! positive_int "$HEALTH_TIMEOUT"; then
  echo "invalid ATRI_V153_RESTART_TIMEOUT/ATRI_V153_HEALTH_TIMEOUT" >&2
  exit 2
fi
if ! validate_shadow_addr; then
  echo "ATRI_TELEGRAM_SHADOW_ADDR must be 127.0.0.1:<1-65535>" >&2
  exit 2
fi
if ! validate_github_probe; then
  echo "invalid ATRI_V153_GITHUB_PROBE_OWNER/REPO/REF" >&2
  exit 2
fi

mkdir -p "$STATE_DIR" "$BACKUP_ROOT"
choose_report_dir() {
  local d
  for d in /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$d" && -w "$d" ]]; then
      printf '%s\n' "$d"
      return 0
    fi
  done
  printf '%s\n' "$STATE_DIR"
}
REPORT_DIR="$(choose_report_dir)"
REPORT="$REPORT_DIR/atri-v153-ai-${ACTION}-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }
info() { printf '[INFO] %s\n' "$*"; }
pass() { printf '[PASS] %-22s %s\n' "$1" "$2"; }
fail() { printf '[FAIL] %-22s %s\n' "$1" "$2" >&2; return 1; }

find_rootfs() {
  local d
  for d in \
    "$HOST_PREFIX/var/lib/proot-distro/containers/debian/rootfs" \
    "$HOST_PREFIX/var/lib/proot-distro/installed-rootfs/debian"; do
    if [[ -d "$d$DEBIAN_CLONE/rewrite" ]]; then
      printf '%s\n' "$d"
      return 0
    fi
  done
  return 1
}

debian_run() { proot-distro login debian -- bash -lc "$1"; }

require_host() {
  if [[ "$HOST_PREFIX" != "/data/data/com.termux/files/usr" ]] || [[ -f /etc/debian_version ]]; then
    fail HOST_CONTEXT "must run from Termux host"
    return 1
  fi
  for command in proot-distro tmux curl; do
    command -v "$command" >/dev/null 2>&1 || {
      fail HOST_CONTEXT "$command missing"
      return 1
    }
  done
  ROOTFS_DIR="$(find_rootfs || true)"
  [[ -n "$ROOTFS_DIR" ]] || {
    fail HOST_CONTEXT "isolated Debian clone not found"
    return 1
  }
  debian_run "test -x /app/mltbenv/bin/python" >/dev/null 2>&1 || {
    fail HOST_CONTEXT "production Python missing: /app/mltbenv/bin/python"
    return 1
  }
  pass HOST_CONTEXT "Termux host rootfs=$ROOTFS_DIR"
}

repo_meta() {
  debian_run "cd '$DEBIAN_CLONE' && printf 'branch=%s\\n' \"\$(git branch --show-current)\" && printf 'head=%s\\n' \"\$(git rev-parse HEAD)\" && if git diff --quiet && git diff --cached --quiet; then echo clean=1; else echo clean=0; fi" 2>/dev/null
}

require_repo() {
  local meta branch head clean
  meta="$(repo_meta || true)"
  printf '%s\n' "$meta"
  branch="$(awk -F= '$1=="branch"{print $2}' <<<"$meta")"
  head="$(awk -F= '$1=="head"{print $2}' <<<"$meta")"
  clean="$(awk -F= '$1=="clean"{print $2}' <<<"$meta")"
  if [[ "$branch" != "$EXPECTED_BRANCH" || ! "$head" =~ ^[0-9a-f]{40}$ || "$clean" != 1 ]]; then
    fail REPO "branch=${branch:-unknown} head=${head:-unknown} clean=${clean:-unknown}"
    return 1
  fi
  REPO_SHA="$head"
  for f in \
    rewrite/v153_ai_guard_patch.py \
    rewrite/v153_ai_probe.py \
    rewrite/v152_parity_patch.py \
    bot/modules/atri_ai_runtime_guard.py; do
    [[ -f "$ROOTFS_DIR$DEBIAN_CLONE/$f" ]] || {
      fail REPO "missing $f"
      return 1
    }
  done
  pass REPO "branch=$branch head=$head"
}

legacy_watchdog_pids() { pgrep -af '[a]tri-production-watchdog.sh' 2>/dev/null | awk 'NF{print $1}' | sort -n; }
v150_watchdog_pids() { pgrep -af "$V150_BIN" 2>/dev/null | awk 'NF{print $1}' | sort -n; }
bot_session_state() { if tmux has-session -t prixok-bot 2>/dev/null; then echo PRESENT; else echo MISSING; fi; }
bot_pane_pid() { tmux list-panes -t prixok-bot -F '#{pane_pid}' 2>/dev/null | head -n1; }

bot_lock_state() {
  debian_run '
set -u
p=/app/.atri-prixok-bot-v133.lock
if [ ! -e "$p" ]; then echo MISSING; exit 0; fi
exec 9<>"$p"
if flock -n 9; then flock -u 9; echo FREE; else echo HELD; fi
' 2>/dev/null | tail -n1 | tr -d '\r'
}

local_health_state() {
  if [[ -x "$HOST_HOME/atri-production-local-health.sh" ]] && "$HOST_HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1; then
    echo HEALTHY
  else
    echo UNHEALTHY
  fi
}

require_healthy_production() {
  local -a legacy=() v150=()
  local session pane lock health
  mapfile -t legacy < <(legacy_watchdog_pids)
  mapfile -t v150 < <(v150_watchdog_pids)
  session="$(bot_session_state)"
  pane="$(bot_pane_pid || true)"
  lock="$(bot_lock_state || echo UNKNOWN)"
  health="$(local_health_state)"
  printf 'v150=%s legacy=%s session=%s pane=%s lock=%s health=%s\n' \
    "${v150[*]:-none}" "${legacy[*]:-none}" "$session" "${pane:-unknown}" "$lock" "$health"
  if ((${#legacy[@]} != 0 || ${#v150[@]} != 1)) || \
     [[ "$session" != PRESENT || ! "$pane" =~ ^[0-9]+$ || "$lock" != HELD || "$health" != HEALTHY ]]; then
    fail PRODUCTION "requires one V150 owner, no legacy owner, healthy singleton bot"
    return 1
  fi
  HEALTHY_PANE="$pane"
  pass PRODUCTION "v150=${v150[0]} pane=$pane lock=HELD health=HEALTHY"
}

shadow_health() { curl -fsS --max-time 4 "$SHADOW_URL/healthz"; }

require_v151_gate_a() {
  local response
  [[ -f "$V151_HOST_ENABLE" ]] || {
    fail V151_BASELINE "host enable sentinel missing"
    return 1
  }
  debian_run "test -f '$V151_READY_FILE' && grep -q '\"mode\":\"observe-only\"' '$V151_READY_FILE'" >/dev/null 2>&1 || {
    fail V151_BASELINE "Python observer-ready missing"
    return 1
  }
  response="$(shadow_health 2>/dev/null || true)"
  [[ "$response" == *'"status":"ok"'* ]] || {
    fail V151_BASELINE "shadow ingress unhealthy"
    return 1
  }
  pass V151_BASELINE "Gate A still healthy"
}

v152_source_verify() {
  debian_run "cd '$DEBIAN_CLONE' && python3 rewrite/v152_parity_patch.py verify --source-root '$DEBIAN_CLONE' --live-root /app" >/dev/null 2>&1
}

require_v152_gate_b1() {
  local response
  debian_run "test -f '$V152_ENABLE_FILE'" >/dev/null 2>&1 || {
    fail V152_BASELINE "V152 enable sentinel missing"
    return 1
  }
  v152_source_verify || {
    fail V152_BASELINE "V152 live source verification failed"
    return 1
  }
  response="$(shadow_health 2>/dev/null || true)"
  [[ "$response" == *'"status":"ok"'* && \
     "$response" == *'"route_mismatch":0'* && \
     "$response" == *'"plan_mismatch":0'* && \
     "$response" == *'"tool_mismatch":0'* ]] || {
    fail V152_BASELINE "decision parity health/mismatch invariant failed"
    return 1
  }
  pass V152_BASELINE "Gate B1 source + zero-mismatch invariants healthy"
}

source_patcher() {
  local action="$1" backup="${2:-}"
  local command="cd '$DEBIAN_CLONE' && python3 rewrite/v153_ai_guard_patch.py '$action' --source-root '$DEBIAN_CLONE' --live-root /app"
  if [[ -n "$backup" ]]; then
    command+=" --backup-dir '$backup'"
  fi
  debian_run "$command"
}

clear_v151_ready() { debian_run "rm -f '$V151_READY_FILE'" >/dev/null 2>&1; }

wait_v151_ready() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  while ((SECONDS < deadline)); do
    if debian_run "test -f '$V151_READY_FILE' && grep -q '\"mode\":\"observe-only\"' '$V151_READY_FILE'" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_new_bot_healthy() {
  local old_pane="$1" deadline=$((SECONDS + RESTART_TIMEOUT)) pane lock health
  while ((SECONDS < deadline)); do
    pane="$(bot_pane_pid || true)"
    lock="$(bot_lock_state || echo UNKNOWN)"
    health="$(local_health_state)"
    if [[ "$pane" =~ ^[0-9]+$ && "$pane" != "$old_pane" && "$lock" == HELD && "$health" == HEALTHY ]]; then
      NEW_PANE="$pane"
      return 0
    fi
    sleep 3
  done
  return 1
}

restart_bot_controlled() {
  local old_pane="$1"
  [[ "$(bot_session_state)" == PRESENT ]] || return 1
  tmux send-keys -t prixok-bot C-c || return 1
  wait_new_bot_healthy "$old_pane"
}

bot_log_line_count() {
  debian_run "if [ -f /app/log.txt ]; then wc -l < /app/log.txt; else echo 0; fi" 2>/dev/null | tail -n1 | tr -dc '0-9'
}

runtime_guard_ready_after_restart() {
  local start_line=$((BOT_LOG_LINES_BEFORE + 1))
  debian_run "if [ ! -f /app/log.txt ]; then exit 1; fi; sed -n '${start_line},\$p' /app/log.txt | grep -q 'ATRI_AI_RUNTIME_GUARD_V153_INSTALLED' && ! sed -n '${start_line},\$p' /app/log.txt | grep -q 'ATRI_AI_RUNTIME_GUARD_V153_INSTALL_FAILED'" >/dev/null 2>&1
}

github_rest_probe() {
  debian_run "cd '$DEBIAN_CLONE' && env -u GITHUB_PERSONAL_ACCESS_TOKEN -u GITHUB_TOKEN /app/mltbenv/bin/python rewrite/v153_ai_probe.py --guard /app/bot/modules/atri_ai_runtime_guard.py --owner '$GITHUB_PROBE_OWNER' --repo '$GITHUB_PROBE_REPO' --ref '$GITHUB_PROBE_REF'"
}

boot_lock_fd_clean() {
  local -a pids=()
  mapfile -t pids < <(v150_watchdog_pids)
  ((${#pids[@]} == 1)) || return 1
  ! ls -l "/proc/${pids[0]}/fd" 2>/dev/null | grep -q 'boot-hook\.lock'
}

write_canary_meta() {
  local backup="$1"
  cat >"$backup/canary-meta.env" <<EOF || return 1
REPO_SHA=$REPO_SHA
EOF
  chmod 600 "$backup/canary-meta.env" || return 1
  printf '%s\n' "$backup" >"$LAST_BACKUP_FILE" || return 1
}

rollback_apply_failure() {
  local reason="$1" pane
  ((ROLLBACK_RUNNING == 0)) || return 0
  ROLLBACK_RUNNING=1
  section "AUTO ROLLBACK"
  info "reason=$reason"

  if ((SOURCE_APPLIED == 1)) && [[ -n "$APPLY_BACKUP" ]]; then
    source_patcher rollback "$APPLY_BACKUP/source" || true
  fi
  if ((BOT_RESTART_ATTEMPTED == 1)); then
    pane="$(bot_pane_pid || true)"
    if [[ "$pane" =~ ^[0-9]+$ ]]; then
      clear_v151_ready || true
      restart_bot_controlled "$pane" || true
      wait_v151_ready || true
    fi
  fi

  if [[ "$(local_health_state)" == HEALTHY && "$(bot_lock_state || echo UNKNOWN)" == HELD ]] && \
     require_v151_gate_a >/dev/null 2>&1 && require_v152_gate_b1 >/dev/null 2>&1; then
    pass AUTO_ROLLBACK "pre-V153 Python source restored; V151/V152 healthy"
  else
    fail AUTO_ROLLBACK "rollback attempted; production needs manual inspection" || true
  fi
}

apply_canary() {
  local pane_before probe_result
  require_host
  section "REPO"
  require_repo
  section "PRE-PRODUCTION"
  require_healthy_production
  require_v151_gate_a
  require_v152_gate_b1

  if source_patcher verify >/dev/null 2>&1; then
    fail MODE "V153 AI guard already applied; use status or rollback"
    return 1
  fi

  pane_before="$HEALTHY_PANE"
  BOT_LOG_LINES_BEFORE="$(bot_log_line_count || echo 0)"
  [[ "$BOT_LOG_LINES_BEFORE" =~ ^[0-9]+$ ]] || BOT_LOG_LINES_BEFORE=0
  APPLY_BACKUP="$BACKUP_ROOT/apply-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$APPLY_BACKUP"

  section "PATCH LIVE AI GUARD"
  if ! source_patcher apply "$APPLY_BACKUP/source"; then
    rollback_apply_failure "V153 source patch failed"
    return 1
  fi
  SOURCE_APPLIED=1
  if ! write_canary_meta "$APPLY_BACKUP"; then
    rollback_apply_failure "V153 backup metadata write failed"
    return 1
  fi
  pass SOURCE_PATCH "V153 startup hook + guard module installed; backup=$APPLY_BACKUP/source"

  section "CONTROLLED BOT RESTART"
  if ! clear_v151_ready; then
    rollback_apply_failure "failed to clear V151 ready marker before restart"
    return 1
  fi
  BOT_RESTART_ATTEMPTED=1
  if ! restart_bot_controlled "$pane_before"; then
    rollback_apply_failure "bot did not restart healthy within timeout"
    return 1
  fi
  if ! wait_v151_ready; then
    rollback_apply_failure "V151 observer did not become ready after V153 restart"
    return 1
  fi
  pass BOT_RESTART "old_pane=$pane_before new_pane=$NEW_PANE V151_ready=1"

  if ! source_patcher verify >/dev/null 2>&1; then
    rollback_apply_failure "V153 source verification failed after restart"
    return 1
  fi
  pass SOURCE_VERIFY "V153 startup hook + module compile/sha verified"

  if ! runtime_guard_ready_after_restart; then
    rollback_apply_failure "V153 runtime install marker missing or install failure logged"
    return 1
  fi
  pass RUNTIME_GUARD "startup marker confirms V153 installed in real bot process"

  section "REAL GITHUB PUBLIC-READ PROBE"
  if ! probe_result="$(github_rest_probe 2>&1)"; then
    printf '%s\n' "$probe_result"
    rollback_apply_failure "isolated no-token GitHub REST probe failed"
    return 1
  fi
  printf '%s\n' "$probe_result"
  [[ "$probe_result" == *'"data_ok":true'* && "$probe_result" == *'"source":"github_rest_readonly"'* ]] || {
    rollback_apply_failure "GitHub REST probe returned unexpected payload"
    return 1
  }
  pass GITHUB_REST "real no-token list_commits probe succeeded"

  if ! require_v152_gate_b1; then
    rollback_apply_failure "V152 Gate B1 regressed after V153 restart/probe"
    return 1
  fi
  if ! boot_lock_fd_clean; then
    rollback_apply_failure "boot-hook FD lock leak detected"
    return 1
  fi
  pass BOOT_LOCK_FD "NO_BOOT_LOCK_FD"

  section "FINAL PRODUCTION"
  if ! require_healthy_production; then
    rollback_apply_failure "final production health check failed"
    return 1
  fi
  if ! require_v151_gate_a; then
    rollback_apply_failure "V151 Gate A failed in final production check"
    return 1
  fi
  if ! require_v152_gate_b1; then
    rollback_apply_failure "V152 Gate B1 failed in final production check"
    return 1
  fi
  pass CANARY "V153 AI guard active; Python remains sole Telegram/AI owner"
}

status_canary() {
  local source="NOT_APPLIED" fd="UNKNOWN" runtime="UNKNOWN" meta
  require_host
  section "REPO"
  meta="$(repo_meta || true)"
  printf '%s\n' "$meta"
  section "PRODUCTION"
  require_healthy_production || true
  require_v151_gate_a || true
  require_v152_gate_b1 || true

  if source_patcher verify >/dev/null 2>&1; then source=APPLIED; fi
  if debian_run "grep -q 'ATRI_AI_RUNTIME_GUARD_V153_INSTALLED' /app/log.txt 2>/dev/null" >/dev/null 2>&1; then runtime=SEEN; fi
  if boot_lock_fd_clean; then fd=NO_BOOT_LOCK_FD; else fd=CHECK_FAILED; fi

  section "V153 AI GUARD"
  printf 'source=%s\n' "$source"
  printf 'runtime_marker=%s\n' "$runtime"
  printf 'boot_lock_fd=%s\n' "$fd"
  printf 'last_backup=%s\n' "$(cat "$LAST_BACKUP_FILE" 2>/dev/null || echo none)"
  printf 'report=%s\n' "$REPORT"
}

rollback_canary() {
  local backup candidate pane
  require_host
  section "REPO"
  require_repo
  backup="$(cat "$LAST_BACKUP_FILE" 2>/dev/null || true)"
  [[ -n "$backup" && -f "$backup/canary-meta.env" && -f "$backup/source/source-manifest.json" ]] || {
    fail ROLLBACK "no complete V153 canary backup available"
    return 1
  }
  candidate="$(awk -F= '$1=="REPO_SHA"{print $2}' "$backup/canary-meta.env")"
  [[ "$candidate" =~ ^[0-9a-f]{40}$ ]] || {
    fail ROLLBACK "invalid V153 backup metadata"
    return 1
  }

  section "ROLLBACK V153"
  source_patcher rollback "$backup/source"
  pass SOURCE_ROLLBACK "restored exact pre-V153 bot/__init__.py + guard module state"

  pane="$(bot_pane_pid || true)"
  [[ "$pane" =~ ^[0-9]+$ ]] || {
    fail BOT_ROLLBACK "production pane missing before controlled restart"
    return 1
  }
  clear_v151_ready
  restart_bot_controlled "$pane"
  wait_v151_ready
  pass BOT_ROLLBACK "old_pane=$pane new_pane=$NEW_PANE"
  require_healthy_production
  require_v151_gate_a
  require_v152_gate_b1
  pass ROLLBACK "V153 disabled; V151 Gate A + V152 Gate B1 remain active"
}

finish_report() {
  local rc=$?
  trap - EXIT
  echo "END: $(date)"
  echo "REPORT: $REPORT"
  exit "$rc"
}

trap finish_report EXIT
section "ATRI V153 AI GUARD CANARY"
echo "START: $(date)"
echo "ACTION: $ACTION"
echo "REPORT: $REPORT"

case "$ACTION" in
    status) status_canary ;;
    apply) apply_canary ;;
    rollback) rollback_canary ;;
esac
