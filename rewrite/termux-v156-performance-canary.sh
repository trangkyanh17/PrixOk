#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

EXPECTED_BRANCH="main"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
STATE_DIR="$HOST_HOME/.local/state/atri-v156-performance"
BACKUP_ROOT="$STATE_DIR/backups"
LAST_BACKUP_FILE="$STATE_DIR/last-backup"
V150_BIN="$HOST_HOME/.local/lib/atri-v150/atri-supervisor"
V151_HOST_ENABLE="$HOST_HOME/.local/state/atri-v151-shadow/enabled"
V151_READY_FILE="/root/.local/state/atri-v151-shadow/observer-ready.json"
V152_ENABLE_FILE="/root/.local/state/atri-v152-parity/enabled"
ACTION="${1:-status}"
SHADOW_ADDR="${ATRI_TELEGRAM_SHADOW_ADDR:-127.0.0.1:18750}"
SHADOW_URL="http://$SHADOW_ADDR"
RESTART_TIMEOUT="${ATRI_V156_RESTART_TIMEOUT:-180}"
HEALTH_TIMEOUT="${ATRI_V156_HEALTH_TIMEOUT:-180}"
CANARY_SOAK_SECONDS="${ATRI_V156_CANARY_SOAK_SECONDS:-60}"
LONG_SOAK_SECONDS="${ATRI_V156_SOAK_SECONDS:-300}"
SOAK_INTERVAL="${ATRI_V156_SOAK_INTERVAL:-10}"
MAX_RSS_GROWTH_MIB="${ATRI_V156_MAX_RSS_GROWTH_MIB:-384}"
MAX_SWAP_GROWTH_MIB="${ATRI_V156_MAX_SWAP_GROWTH_MIB:-256}"
MAX_THREADS="${ATRI_V156_MAX_THREADS:-192}"
MAX_FDS="${ATRI_V156_MAX_FDS:-4096}"

ROOTFS_DIR=""
REPO_SHA=""
REPORT=""
APPLY_BACKUP=""
SOURCE_APPLIED=0
BOT_RESTART_ATTEMPTED=0
ROLLBACK_RUNNING=0
NEW_PANE=""
BOT_LOG_LINES_BEFORE=0

usage() {
  cat <<'EOF'
Usage: termux-v156-performance-canary.sh <command>

Commands:
  status       Read-only V150-V156 production/resource status.
  apply        Capture pre-V156 resources, transactionally install the bounded
               executor, restart once, run a short resource soak, and re-check
               V151-V155 plus production singleton invariants.
  soak         Read-only longer resource soak against the active V156 worker.
  rollback     Restore the exact pre-V156 source snapshot and restart once.
  --self-test  CI syntax/contract checks only.

Safety contract:
- never mutates git state or replaces the customized /app tree;
- never edits bot/modules/atri_ai.py, RSS, mirror, YTDLP, MyJD, or V155 guards;
- never starts a second Telegram/AI worker or a second watchdog;
- only bot/helper/ext_utils/bot_utils.py and runtime_tuning.py are V156-managed;
- rollback validates every managed post-apply SHA before restoring anything.
EOF
}

positive_int() { [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]; }

validate_shadow_addr() {
  local port
  [[ "$SHADOW_ADDR" =~ ^127\.0\.0\.1:([0-9]{1,5})$ ]] || return 1
  port="${BASH_REMATCH[1]}"
  ((port >= 1 && port <= 65535))
}

if [[ "$ACTION" == "--self-test" ]]; then
  [[ "$EXPECTED_BRANCH" == main ]]
  for value in "$RESTART_TIMEOUT" "$HEALTH_TIMEOUT" "$CANARY_SOAK_SECONDS" "$LONG_SOAK_SECONDS" "$SOAK_INTERVAL" "$MAX_RSS_GROWTH_MIB" "$MAX_SWAP_GROWTH_MIB" "$MAX_THREADS" "$MAX_FDS"; do
    positive_int "$value"
  done
  validate_shadow_addr
  for cmd in status apply soak rollback; do
    grep -q "^    $cmd)" "$0"
  done
  if grep -Eq '^[[:space:]]*git[[:space:]]+(pull|reset|checkout|clean)|update\.py|rm[[:space:]]+-rf[[:space:]]+/app' "$0"; then
    echo "v156 performance canary self-test: FAIL (forbidden live mutation)" >&2
    exit 1
  fi
  grep -q 'git status --porcelain=v1 --untracked-files=all' "$0"
  grep -q 'PRE_V156_NEGATIVE' "$0"
  grep -q 'ATRI_PERFORMANCE_GUARD_V156_INSTALLED' "$0"
  grep -q 'POST-V156 SHORT SOAK' "$0"
  grep -q 'AUTO ROLLBACK' "$0"
  grep -q 'v155_network_patch.py' "$0"
  grep -q 'v156_performance_patch.py' "$0"
  grep -q 'v156_performance_probe.py' "$0"
  ! grep -q '/app/bot/modules/atri_ai.py' "$0"
  echo "v156 performance canary self-test: PASS"
  exit 0
fi

case "$ACTION" in
  status|apply|soak|rollback) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

for value in "$RESTART_TIMEOUT" "$HEALTH_TIMEOUT" "$CANARY_SOAK_SECONDS" "$LONG_SOAK_SECONDS" "$SOAK_INTERVAL" "$MAX_RSS_GROWTH_MIB" "$MAX_SWAP_GROWTH_MIB" "$MAX_THREADS" "$MAX_FDS"; do
  positive_int "$value" || { echo "invalid V156 numeric setting: $value" >&2; exit 2; }
done
validate_shadow_addr || { echo "ATRI_TELEGRAM_SHADOW_ADDR must be 127.0.0.1:<1-65535>" >&2; exit 2; }

mkdir -p "$STATE_DIR" "$BACKUP_ROOT"
chmod 700 "$STATE_DIR" "$BACKUP_ROOT" 2>/dev/null || true

choose_report_dir() {
  local d
  for d in /storage/emulated/0/Download /sdcard/Download; do
    if [[ -d "$d" && -w "$d" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  printf '%s\n' "$STATE_DIR"
}
REPORT_DIR="$(choose_report_dir)"
REPORT="$REPORT_DIR/atri-v156-performance-${ACTION}-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }
info() { printf '[INFO] %s\n' "$*"; }
pass() { printf '[PASS] %-24s %s\n' "$1" "$2"; }
warn() { printf '[WARN] %-24s %s\n' "$1" "$2"; }
fail() { printf '[FAIL] %-24s %s\n' "$1" "$2" >&2; return 1; }

finish_report() {
  local rc=$?
  printf 'END: %s\n' "$(date)"
  printf 'REPORT: %s\n' "$REPORT"
  return "$rc"
}
trap finish_report EXIT

printf '\n===== ATRI V156 PERFORMANCE CANARY =====\n'
printf 'START: %s\n' "$(date)"
printf 'ACTION: %s\n' "$ACTION"
printf 'REPORT: %s\n' "$REPORT"

find_rootfs() {
  local d
  for d in \
    "$HOST_PREFIX/var/lib/proot-distro/containers/debian/rootfs" \
    "$HOST_PREFIX/var/lib/proot-distro/installed-rootfs/debian"; do
    if [[ -d "$d$DEBIAN_CLONE/rewrite" ]]; then printf '%s\n' "$d"; return 0; fi
  done
  return 1
}

debian_run() { proot-distro login debian -- bash -lc "$1"; }

require_host() {
  if [[ "$HOST_PREFIX" != "/data/data/com.termux/files/usr" ]] || [[ -f /etc/debian_version ]]; then
    fail HOST_CONTEXT "must run from Termux host"; return 1
  fi
  for command in proot-distro tmux curl readlink pgrep; do
    command -v "$command" >/dev/null 2>&1 || { fail HOST_CONTEXT "$command missing"; return 1; }
  done
  ROOTFS_DIR="$(find_rootfs || true)"
  [[ -n "$ROOTFS_DIR" ]] || { fail HOST_CONTEXT "isolated Debian clone not found"; return 1; }
  debian_run "test -x /app/mltbenv/bin/python" >/dev/null 2>&1 || { fail HOST_CONTEXT "production Python missing"; return 1; }
  pass HOST_CONTEXT "Termux host rootfs=$ROOTFS_DIR"
}

repo_meta() {
  debian_run "cd '$DEBIAN_CLONE' && branch=\$(git branch --show-current) && head=\$(git rev-parse HEAD) && origin_head=\$(git rev-parse --verify 'refs/remotes/origin/$EXPECTED_BRANCH' 2>/dev/null || true) && if [ -z \"\$(git status --porcelain=v1 --untracked-files=all)\" ]; then clean=1; else clean=0; fi && printf 'branch=%s\\nhead=%s\\norigin_head=%s\\nclean=%s\\n' \"\$branch\" \"\$head\" \"\$origin_head\" \"\$clean\"" 2>/dev/null
}

require_repo() {
  local meta branch head origin_head clean f
  meta="$(repo_meta || true)"; printf '%s\n' "$meta"
  branch="$(awk -F= '$1=="branch"{print $2}' <<<"$meta")"
  head="$(awk -F= '$1=="head"{print $2}' <<<"$meta")"
  origin_head="$(awk -F= '$1=="origin_head"{print $2}' <<<"$meta")"
  clean="$(awk -F= '$1=="clean"{print $2}' <<<"$meta")"
  if [[ "$branch" != "$EXPECTED_BRANCH" || ! "$head" =~ ^[0-9a-f]{40}$ || "$origin_head" != "$head" || "$clean" != 1 ]]; then
    fail REPO "branch=${branch:-unknown} head=${head:-unknown} origin_head=${origin_head:-unknown} clean=${clean:-unknown}"; return 1
  fi
  REPO_SHA="$head"
  for f in rewrite/v151_shadow_patch.py rewrite/v152_parity_patch.py rewrite/v153_ai_guard_patch.py rewrite/v154_production_patch.py rewrite/v154_production_probe.py rewrite/v155_network_patch.py rewrite/v155_network_probe.py rewrite/v156_performance_patch.py rewrite/v156_performance_probe.py bot/helper/ext_utils/runtime_tuning.py; do
    [[ -f "$ROOTFS_DIR$DEBIAN_CLONE/$f" ]] || { fail REPO "missing $f"; return 1; }
  done
  pass REPO "branch=$branch head=$head origin/main=$origin_head clean=1"
}

validate_backup_path() {
  local backup="$1" root_real backup_real
  root_real="$(readlink -f "$BACKUP_ROOT" 2>/dev/null || true)"
  backup_real="$(readlink -f "$backup" 2>/dev/null || true)"
  [[ -n "$root_real" && -n "$backup_real" && "$backup_real" == "$root_real"/apply-* ]]
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
  if [[ -x "$HOST_HOME/atri-production-local-health.sh" ]] && "$HOST_HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1; then echo HEALTHY; else echo UNHEALTHY; fi
}

production_bot_pid() {
  local raw
  raw="$(debian_run "pgrep -f '[p]ython3 -m bot' || true" 2>/dev/null | tr -d '\r')"
  [[ "$(awk 'NF{n++} END{print n+0}' <<<"$raw")" == 1 ]] || return 1
  awk 'NF{print $1}' <<<"$raw"
}

require_healthy_production() {
  local -a legacy=() v150=()
  local session pane lock health botpid
  mapfile -t legacy < <(legacy_watchdog_pids)
  mapfile -t v150 < <(v150_watchdog_pids)
  session="$(bot_session_state)"; pane="$(bot_pane_pid || true)"; lock="$(bot_lock_state || echo UNKNOWN)"; health="$(local_health_state)"; botpid="$(production_bot_pid || true)"
  printf 'v150=%s legacy=%s session=%s pane=%s bot_pid=%s lock=%s health=%s\n' "${v150[*]:-none}" "${legacy[*]:-none}" "$session" "${pane:-unknown}" "${botpid:-unknown}" "$lock" "$health"
  if ((${#legacy[@]} != 0 || ${#v150[@]} != 1)) || [[ "$session" != PRESENT || ! "$pane" =~ ^[0-9]+$ || ! "$botpid" =~ ^[0-9]+$ || "$lock" != HELD || "$health" != HEALTHY ]]; then
    fail PRODUCTION "requires one V150 owner, one Python bot, no legacy owner, healthy lock/session"; return 1
  fi
  pass PRODUCTION "v150=${v150[0]} pane=$pane bot_pid=$botpid lock=HELD health=HEALTHY"
}

shadow_health() { curl -fsS --max-time 4 "$SHADOW_URL/healthz"; }

require_v151_gate_a() {
  local response
  [[ -f "$V151_HOST_ENABLE" ]] || { fail V151_BASELINE "host enable sentinel missing"; return 1; }
  debian_run "test -f '$V151_READY_FILE' && grep -q '\"mode\":\"observe-only\"' '$V151_READY_FILE'" >/dev/null 2>&1 || { fail V151_BASELINE "observer-ready missing"; return 1; }
  response="$(shadow_health 2>/dev/null || true)"
  [[ "$response" == *'"status":"ok"'* ]] || { fail V151_BASELINE "shadow ingress unhealthy"; return 1; }
  pass V151_BASELINE "Gate A healthy"
}

require_v152_gate_b1() {
  local response
  debian_run "test -f '$V152_ENABLE_FILE'" >/dev/null 2>&1 || { fail V152_BASELINE "enable sentinel missing"; return 1; }
  debian_run "cd '$DEBIAN_CLONE' && python3 rewrite/v152_parity_patch.py verify --source-root '$DEBIAN_CLONE' --live-root /app" >/dev/null 2>&1 || { fail V152_BASELINE "live source verification failed"; return 1; }
  response="$(shadow_health 2>/dev/null || true)"
  [[ "$response" == *'"status":"ok"'* && "$response" == *'"route_mismatch":0'* && "$response" == *'"plan_mismatch":0'* && "$response" == *'"tool_mismatch":0'* ]] || { fail V152_BASELINE "decision parity invariant failed"; return 1; }
  pass V152_BASELINE "Gate B1 source + zero-mismatch healthy"
}

require_v153_baseline() {
  debian_run "cd '$DEBIAN_CLONE' && python3 rewrite/v153_ai_guard_patch.py verify --source-root '$DEBIAN_CLONE' --live-root /app" >/dev/null 2>&1 || { fail V153_BASELINE "live source verification failed"; return 1; }
  pass V153_BASELINE "V153 source guard verified"
}

require_v154_baseline() {
  local probe
  debian_run "cd '$DEBIAN_CLONE' && python3 rewrite/v154_production_patch.py verify --source-root '$DEBIAN_CLONE' --live-root /app" >/dev/null 2>&1 || { fail V154_BASELINE "live source verification failed"; return 1; }
  probe="$(debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v154_production_probe.py smoke --live-root /app" 2>&1)" || { printf '%s\n' "$probe"; fail V154_BASELINE "V154 smoke regression"; return 1; }
  [[ "$probe" == *'"ok": true'* || "$probe" == *'"ok":true'* ]] || { fail V154_BASELINE "unexpected V154 smoke payload"; return 1; }
  pass V154_BASELINE "source + smoke guards healthy"
}

require_v155_baseline() {
  local probe
  debian_run "cd '$DEBIAN_CLONE' && python3 rewrite/v155_network_patch.py verify --source-root '$DEBIAN_CLONE' --live-root /app" >/dev/null 2>&1 || { fail V155_BASELINE "live network source verification failed"; return 1; }
  probe="$(debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v155_network_probe.py --live-root /app" 2>&1)" || { printf '%s\n' "$probe"; fail V155_BASELINE "network smoke regression"; return 1; }
  [[ "$probe" == *'"ok": true'* || "$probe" == *'"ok":true'* ]] || { fail V155_BASELINE "unexpected V155 smoke payload"; return 1; }
  pass V155_BASELINE "source + network smoke healthy"
}

require_preservation() {
  require_v151_gate_a
  require_v152_gate_b1
  require_v153_baseline
  require_v154_baseline
  require_v155_baseline
}

source_patcher() {
  local action="$1" backup="${2:-}" command
  command="cd '$DEBIAN_CLONE' && python3 rewrite/v156_performance_patch.py '$action' --source-root '$DEBIAN_CLONE' --live-root /app"
  [[ -n "$backup" ]] && command+=" --backup-dir '$backup'"
  debian_run "$command"
}

source_contract() { debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v156_performance_probe.py source-contract --live-root /app"; }
resource_snapshot() { local pid="$1"; debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v156_performance_probe.py snapshot --pid '$pid'"; }
resource_soak() {
  local pid="$1" seconds="$2"
  debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v156_performance_probe.py soak --pid '$pid' --seconds '$seconds' --interval '$SOAK_INTERVAL' --max-rss-growth-mib '$MAX_RSS_GROWTH_MIB' --max-swap-growth-mib '$MAX_SWAP_GROWTH_MIB' --max-threads '$MAX_THREADS' --max-fds '$MAX_FDS'"
}

clear_v151_ready() { debian_run "rm -f '$V151_READY_FILE'" >/dev/null 2>&1; }
wait_v151_ready() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  while ((SECONDS < deadline)); do
    if debian_run "test -f '$V151_READY_FILE' && grep -q '\"mode\":\"observe-only\"' '$V151_READY_FILE'" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

wait_new_bot_healthy() {
  local old_pane="$1" deadline=$((SECONDS + RESTART_TIMEOUT)) pane lock health botpid
  while ((SECONDS < deadline)); do
    pane="$(bot_pane_pid || true)"; lock="$(bot_lock_state || echo UNKNOWN)"; health="$(local_health_state)"; botpid="$(production_bot_pid || true)"
    if [[ "$pane" =~ ^[0-9]+$ && "$pane" != "$old_pane" && "$botpid" =~ ^[0-9]+$ && "$lock" == HELD && "$health" == HEALTHY ]]; then NEW_PANE="$pane"; return 0; fi
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

bot_log_line_count() { debian_run "if [ -f /app/log.txt ]; then wc -l < /app/log.txt; else echo 0; fi" 2>/dev/null | tail -n1 | tr -dc '0-9'; }

runtime_markers_ready_after_restart() {
  local start_line=$((BOT_LOG_LINES_BEFORE + 1)) command marker
  command="if [ ! -f /app/log.txt ]; then exit 1; fi; tmp=\$(mktemp); trap 'rm -f \"\$tmp\"' EXIT; sed -n '${start_line},\$p' /app/log.txt >\"\$tmp\";"
  for marker in ATRI_AI_RUNTIME_GUARD_V153_INSTALLED ATRI_SYSTEM_CONTRACT_GUARD_V154_INSTALLED ATRI_SYSTEM_POST_IMPORT_GUARD_V154_INSTALLED ATRI_STICKER_CHAT_PRIVACY_V154_INSTALLED ATRI_WEBAPP_NETWORK_GUARD_V154_INSTALLED ATRI_XLSX_FORMULA_SAFETY_V1541_INSTALLED ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALLED ATRI_LEGACY_NETWORK_EGRESS_GUARD_V155_INSTALLED ATRI_PERFORMANCE_GUARD_V156_INSTALLED; do
    command+=" grep -q '$marker' \"\$tmp\" || exit 20;"
  done
  command+=" ! grep -Eq 'ATRI_.*(V153|V154|V155|V156).*INSTALL_FAILED' \"\$tmp\""
  debian_run "$command" >/dev/null 2>&1
}

boot_lock_fd_clean() {
  local -a pids=()
  mapfile -t pids < <(v150_watchdog_pids)
  ((${#pids[@]} == 1)) || return 1
  ! ls -l "/proc/${pids[0]}/fd" 2>/dev/null | grep -q 'boot-hook\.lock'
}

write_canary_meta() {
  local backup="$1"
  validate_backup_path "$backup" || return 1
  cat >"$backup/canary-meta.env" <<EOF || return 1
REPO_SHA=$REPO_SHA
EOF
  chmod 600 "$backup/canary-meta.env" || return 1
  printf '%s\n' "$backup" >"$LAST_BACKUP_FILE" || return 1
  chmod 600 "$LAST_BACKUP_FILE" 2>/dev/null || true
}

rollback_apply_failure() {
  local reason="$1" pane rollback_failed=0 negative
  ((ROLLBACK_RUNNING == 0)) || return 0
  ROLLBACK_RUNNING=1
  section "AUTO ROLLBACK"
  info "reason=$reason"
  if ((SOURCE_APPLIED == 1)) && [[ -n "$APPLY_BACKUP" ]]; then
    source_patcher rollback "$APPLY_BACKUP/source" >/dev/null 2>&1 || rollback_failed=1
  fi
  if ((BOT_RESTART_ATTEMPTED == 1)) && ((rollback_failed == 0)); then
    pane="$(bot_pane_pid || true)"
    if [[ "$pane" =~ ^[0-9]+$ ]]; then
      clear_v151_ready || rollback_failed=1
      ((rollback_failed == 0)) && restart_bot_controlled "$pane" || rollback_failed=1
      ((rollback_failed == 0)) && wait_v151_ready || rollback_failed=1
    else rollback_failed=1; fi
  fi
  if ((rollback_failed == 0)) && require_healthy_production >/dev/null 2>&1 && require_preservation >/dev/null 2>&1; then
    if negative="$(source_contract 2>&1)"; then rollback_failed=1; else :; fi
  fi
  if ((rollback_failed == 0)); then pass AUTO_ROLLBACK "exact pre-V156 source restored; V151-V155 and production healthy"; else fail AUTO_ROLLBACK "rollback incomplete/stale; manual inspection required" || true; fi
}

apply_canary() {
  local pane_before pid_before pid_after negative snapshot probe soak
  require_host
  section "REPO"; require_repo
  section "PRE-PRODUCTION"; require_healthy_production; require_preservation

  section "PRE-V156 RESOURCE BASELINE"
  pid_before="$(production_bot_pid)"
  snapshot="$(resource_snapshot "$pid_before")" || { printf '%s\n' "$snapshot"; fail RESOURCE_BASELINE "snapshot failed"; return 1; }
  printf '%s\n' "$snapshot"
  pass RESOURCE_BASELINE "captured live Python pid=$pid_before before mutation"

  section "PRE-V156 NEGATIVE BASELINE"
  if negative="$(source_contract 2>&1)"; then
    printf '%s\n' "$negative"; fail PRE_V156_NEGATIVE "V156 unexpectedly active before mutation"; return 1
  fi
  printf '%s\n' "$negative"
  pass PRE_V156_NEGATIVE "expected failure confirms V156 was absent before mutation"

  section "PATCH LIVE V156 PERFORMANCE"
  APPLY_BACKUP="$BACKUP_ROOT/apply-$(date +%Y%m%d-%H%M%S)-$$"
  mkdir -p "$APPLY_BACKUP"
  probe="$(source_patcher apply "$APPLY_BACKUP/source")" || { printf '%s\n' "$probe"; fail SOURCE_PATCH "transactional patch failed"; return 1; }
  printf '%s\n' "$probe"
  SOURCE_APPLIED=1
  write_canary_meta "$APPLY_BACKUP" || { rollback_apply_failure "backup provenance write failed"; return 1; }
  pass SOURCE_PATCH "2-file transactional patch installed; backup=$APPLY_BACKUP/source"

  section "CONTROLLED BOT RESTART"
  pane_before="$(bot_pane_pid)"; BOT_LOG_LINES_BEFORE="$(bot_log_line_count)"; clear_v151_ready
  BOT_RESTART_ATTEMPTED=1
  restart_bot_controlled "$pane_before" || { rollback_apply_failure "controlled bot restart failed"; return 1; }
  wait_v151_ready || { rollback_apply_failure "V151 observer did not recover"; return 1; }
  pass BOT_RESTART "old_pane=$pane_before new_pane=$NEW_PANE V151_ready=1"
  source_patcher verify >/dev/null || { rollback_apply_failure "V156 source verification failed"; return 1; }
  source_contract >/dev/null || { rollback_apply_failure "V156 source contract failed"; return 1; }
  pass SOURCE_VERIFY "V156 live source compile + SHA/anchor verified"
  runtime_markers_ready_after_restart || { rollback_apply_failure "runtime marker missing after restart"; return 1; }
  pass RUNTIME_MARKERS "V153 + V154.x + V155 + V156 installed in real bot process"

  section "POST-V156 SHORT SOAK"
  pid_after="$(production_bot_pid || true)"
  [[ "$pid_after" =~ ^[0-9]+$ ]] || { rollback_apply_failure "production Python singleton missing"; return 1; }
  soak="$(resource_soak "$pid_after" "$CANARY_SOAK_SECONDS")" || { printf '%s\n' "$soak"; rollback_apply_failure "V156 short soak exceeded resource bounds"; return 1; }
  printf '%s\n' "$soak"
  pass V156_SOAK "pid=$pid_after duration=${CANARY_SOAK_SECONDS}s bounded RSS/swap/thread/fd growth"

  section "POST-PRESERVATION GATES"
  require_preservation || { rollback_apply_failure "V151-V155 preservation gate failed"; return 1; }
  boot_lock_fd_clean || { rollback_apply_failure "V150 boot lock FD leaked"; return 1; }
  pass BOOT_LOCK_FD "NO_BOOT_LOCK_FD"

  section "FINAL PRODUCTION"
  require_healthy_production || { rollback_apply_failure "final production invariant failed"; return 1; }
  free -h || true
  pass CANARY "V156 active; V151-V155 preserved; Python remains sole Telegram/AI owner"
}

status_readonly() {
  local pid snapshot
  require_host
  section "REPO"; require_repo
  section "PRODUCTION"; require_healthy_production; require_preservation
  pid="$(production_bot_pid)"
  section "RESOURCE SNAPSHOT"; snapshot="$(resource_snapshot "$pid")"; printf '%s\n' "$snapshot"
  if source_patcher verify >/dev/null 2>&1 && source_contract >/dev/null 2>&1; then pass V156_STATUS "active and source-verified"; else warn V156_STATUS "not active or not source-verified"; fi
}

soak_readonly() {
  local pid result
  require_host
  section "REPO"; require_repo
  section "PRE-SOAK PRODUCTION"; require_healthy_production; require_preservation
  source_patcher verify >/dev/null && source_contract >/dev/null || { fail V156_STATUS "V156 must be active before soak"; return 1; }
  pid="$(production_bot_pid)"
  section "V156 SOAK"
  result="$(resource_soak "$pid" "$LONG_SOAK_SECONDS")" || { printf '%s\n' "$result"; fail V156_SOAK "resource bound exceeded"; return 1; }
  printf '%s\n' "$result"
  require_healthy_production
  require_preservation
  pass V156_SOAK "duration=${LONG_SOAK_SECONDS}s PASS; no restart/mutation performed"
}

manual_rollback() {
  local backup pane meta_sha negative
  require_host
  section "REPO"; require_repo
  [[ -f "$LAST_BACKUP_FILE" ]] || { fail ROLLBACK "last-backup pointer missing"; return 1; }
  backup="$(cat "$LAST_BACKUP_FILE")"
  validate_backup_path "$backup" || { fail ROLLBACK "untrusted backup path"; return 1; }
  [[ -f "$backup/canary-meta.env" ]] || { fail ROLLBACK "canary metadata missing"; return 1; }
  meta_sha="$(awk -F= '$1=="REPO_SHA"{print $2}' "$backup/canary-meta.env")"
  [[ "$meta_sha" == "$REPO_SHA" ]] || { fail ROLLBACK "backup repo SHA differs from current trusted clone"; return 1; }
  require_healthy_production
  source_patcher verify >/dev/null || { fail ROLLBACK "V156 source is not in verified applied state"; return 1; }
  source_patcher rollback "$backup/source"
  pane="$(bot_pane_pid)"; clear_v151_ready; restart_bot_controlled "$pane"; wait_v151_ready
  require_healthy_production; require_preservation
  if negative="$(source_contract 2>&1)"; then printf '%s\n' "$negative"; fail ROLLBACK "V156 source still active after rollback"; return 1; fi
  pass ROLLBACK "exact pre-V156 source restored and production healthy"
}

case "$ACTION" in
    status) status_readonly ;;
    apply) apply_canary ;;
    soak) soak_readonly ;;
    rollback) manual_rollback ;;
esac
