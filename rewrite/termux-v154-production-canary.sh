#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

EXPECTED_BRANCH="main"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
STATE_DIR="$HOST_HOME/.local/state/atri-v154-production"
BACKUP_ROOT="$STATE_DIR/backups"
LAST_BACKUP_FILE="$STATE_DIR/last-backup"
V150_BIN="$HOST_HOME/.local/lib/atri-v150/atri-supervisor"
V151_HOST_ENABLE="$HOST_HOME/.local/state/atri-v151-shadow/enabled"
V151_READY_FILE="/root/.local/state/atri-v151-shadow/observer-ready.json"
V152_ENABLE_FILE="/root/.local/state/atri-v152-parity/enabled"
ACTION="${1:-status}"
SHADOW_ADDR="${ATRI_TELEGRAM_SHADOW_ADDR:-127.0.0.1:18750}"
SHADOW_URL="http://$SHADOW_ADDR"
RESTART_TIMEOUT="${ATRI_V154_RESTART_TIMEOUT:-180}"
HEALTH_TIMEOUT="${ATRI_V154_HEALTH_TIMEOUT:-180}"

ROOTFS_DIR=""
REPO_SHA=""
REPORT=""
APPLY_BACKUP=""
SOURCE_APPLIED=0
PACKAGE_MUTATED=0
BOT_RESTART_ATTEMPTED=0
ROLLBACK_RUNNING=0
HEALTHY_PANE=""
NEW_PANE=""
BOT_LOG_LINES_BEFORE=0

usage() {
  cat <<'EOF'
Usage: termux-v154-production-canary.sh <command>

Commands:
  status       Read-only V151/V152/V153/V154 + dependency/production status.
  apply        Ensure V154 runtime dependencies, transactionally patch the live
               guard hooks/modules, restart the Python bot once, run isolated
               production smoke probes, and auto-rollback on any failure.
  rollback     Restore the exact pre-V154 source/package snapshot and restart once.
  --self-test  CI syntax/contract checks only.

Safety contract:
- never runs git source mutation against /app and never replaces /app;
- never edits bot/modules/atri_ai.py;
- never starts a second Telegram/AI worker;
- requires V151 Gate A, V152 Gate B1 and V153 source baseline first;
- source rollback is stale-safe and package rollback removes only distributions
  that did not exist before this canary;
- writes one complete report into /storage/emulated/0/Download when available.
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
  positive_int "$RESTART_TIMEOUT"
  positive_int "$HEALTH_TIMEOUT"
  validate_shadow_addr
  for cmd in status apply rollback; do
    grep -q "^    $cmd)" "$0"
  done
  if grep -Eq '^[[:space:]]*git[[:space:]]+(pull|reset|checkout|clean)|update\.py|rm[[:space:]]+-rf[[:space:]]+/app' "$0"; then
    echo "v154 production canary self-test: FAIL (forbidden live source mutation)" >&2
    exit 1
  fi
  grep -q 'v154_production_patch.py' "$0"
  grep -q 'v154_production_probe.py' "$0"
  grep -q 'v153_ai_guard_patch.py' "$0"
  grep -q 'ATRI_SYSTEM_CONTRACT_GUARD_V154_INSTALLED' "$0"
  grep -q 'ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALLED' "$0"
  grep -q 'python-docx openpyxl PyMuPDF PyYAML playwright' "$0"
  grep -q 'pip-new.txt' "$0"
  grep -q 'AUTO ROLLBACK' "$0"
  grep -q 'tmux send-keys -t prixok-bot C-c' "$0"
  grep -q 'trap finish_report EXIT' "$0"
  ! grep -q '/app/bot/modules/atri_ai.py' "$0"
  echo "v154 production canary self-test: PASS"
  exit 0
fi

case "$ACTION" in
  status|apply|rollback) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! positive_int "$RESTART_TIMEOUT" || ! positive_int "$HEALTH_TIMEOUT"; then
  echo "invalid ATRI_V154_RESTART_TIMEOUT/ATRI_V154_HEALTH_TIMEOUT" >&2
  exit 2
fi
if ! validate_shadow_addr; then
  echo "ATRI_TELEGRAM_SHADOW_ADDR must be 127.0.0.1:<1-65535>" >&2
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
REPORT="$REPORT_DIR/atri-v154-production-${ACTION}-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }
info() { printf '[INFO] %s\n' "$*"; }
pass() { printf '[PASS] %-24s %s\n' "$1" "$2"; }
warn() { printf '[WARN] %-24s %s\n' "$1" "$2"; }
fail() { printf '[FAIL] %-24s %s\n' "$1" "$2" >&2; return 1; }

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
  for command in proot-distro tmux curl comm; do
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
  debian_run "test -x /app/mltbenv/bin/python && test -x /app/mltbenv/bin/pip" >/dev/null 2>&1 || {
    fail HOST_CONTEXT "production venv missing /app/mltbenv/bin/python|pip"
    return 1
  }
  pass HOST_CONTEXT "Termux host rootfs=$ROOTFS_DIR"
}

repo_meta() {
  debian_run "cd '$DEBIAN_CLONE' && printf 'branch=%s\\n' \"\$(git branch --show-current)\" && printf 'head=%s\\n' \"\$(git rev-parse HEAD)\" && if git diff --quiet && git diff --cached --quiet; then echo clean=1; else echo clean=0; fi" 2>/dev/null
}

require_repo() {
  local meta branch head clean f
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
    rewrite/v151_shadow_patch.py \
    rewrite/v152_parity_patch.py \
    rewrite/v153_ai_guard_patch.py \
    rewrite/v154_production_patch.py \
    rewrite/v154_production_probe.py \
    bot/modules/atri_system_guard.py \
    bot/modules/atri_sticker_privacy_guard.py \
    bot/modules/atri_webapp_safety_guard.py \
    bot/modules/atri_xlsx_formula_guard.py \
    bot/modules/atri_artifact_relevance_guard.py; do
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
  pass V151_BASELINE "Gate A healthy"
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
  pass V152_BASELINE "Gate B1 source + zero-mismatch healthy"
}

v153_source_verify() {
  debian_run "cd '$DEBIAN_CLONE' && python3 rewrite/v153_ai_guard_patch.py verify --source-root '$DEBIAN_CLONE' --live-root /app" >/dev/null 2>&1
}

require_v153_baseline() {
  if ! v153_source_verify; then
    fail V153_BASELINE "V153 live source verification failed; apply V153 before V154"
    return 1
  fi
  pass V153_BASELINE "V153 source guard verified"
}

source_patcher() {
  local action="$1" backup="${2:-}"
  local command="cd '$DEBIAN_CLONE' && python3 rewrite/v154_production_patch.py '$action' --source-root '$DEBIAN_CLONE' --live-root /app"
  if [[ -n "$backup" ]]; then
    command+=" --backup-dir '$backup'"
  fi
  debian_run "$command"
}

dependency_probe() {
  debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v154_production_probe.py deps --live-root /app"
}

smoke_probe() {
  debian_run "cd '$DEBIAN_CLONE' && /app/mltbenv/bin/python rewrite/v154_production_probe.py smoke --live-root /app"
}

package_snapshot() {
  debian_run "/app/mltbenv/bin/python -c 'import importlib.metadata as m; print(\"\\n\".join(sorted({str(d.metadata.get(\"Name\") or \"\").strip().lower().replace(\"_\", \"-\") for d in m.distributions() if str(d.metadata.get(\"Name\") or \"\").strip()})))'" | sed '/^$/d'
}

compute_package_delta() {
  local backup="$1"
  package_snapshot >"$backup/pip-after.txt"
  comm -13 "$backup/pip-before.txt" "$backup/pip-after.txt" >"$backup/pip-new.txt"
}

validate_package_delta() {
  local backup="$1" name
  [[ -f "$backup/pip-new.txt" ]] || return 0
  while IFS= read -r name; do
    [[ -z "$name" || "$name" =~ ^[a-z0-9][a-z0-9._-]{0,127}$ ]] || return 1
  done <"$backup/pip-new.txt"
}

rollback_new_packages() {
  local backup="$1" name names=""
  [[ -f "$backup/pip-new.txt" ]] || return 0
  validate_package_delta "$backup" || return 1
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    names+=" $name"
  done <"$backup/pip-new.txt"
  [[ -n "$names" ]] || return 0
  debian_run "/app/mltbenv/bin/python -m pip uninstall -y $names" >/dev/null
}

ensure_dependencies() {
  local backup="$1" probe
  package_snapshot >"$backup/pip-before.txt"
  if probe="$(dependency_probe 2>&1)"; then
    printf '%s\n' "$probe"
    cp "$backup/pip-before.txt" "$backup/pip-after.txt"
    : >"$backup/pip-new.txt"
    pass DEPENDENCIES "all V154 runtime imports already present"
    return 0
  fi

  info "dependency preflight missing/incomplete; installing only V154 top-level runtime packages"
  PACKAGE_MUTATED=1
  if ! debian_run "/app/mltbenv/bin/python -m pip install --disable-pip-version-check --no-input --retries 2 --timeout 60 python-docx openpyxl PyMuPDF PyYAML playwright"; then
    compute_package_delta "$backup" || true
    rollback_new_packages "$backup" || true
    fail DEPENDENCIES "pip install failed; newly-added distributions rolled back where identifiable"
    return 1
  fi
  compute_package_delta "$backup"
  validate_package_delta "$backup" || {
    fail DEPENDENCIES "unsafe package delta metadata"
    return 1
  }
  if ! probe="$(dependency_probe 2>&1)"; then
    printf '%s\n' "$probe"
    fail DEPENDENCIES "imports still fail after install"
    return 1
  fi
  printf '%s\n' "$probe"
  pass DEPENDENCIES "V154 imports ready; new_distributions=$(paste -sd, "$backup/pip-new.txt" 2>/dev/null || true)"
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

runtime_markers_ready_after_restart() {
  local start_line=$((BOT_LOG_LINES_BEFORE + 1))
  local command marker
  command="if [ ! -f /app/log.txt ]; then exit 1; fi; tmp=\$(mktemp); trap 'rm -f \"\$tmp\"' EXIT; sed -n '${start_line},\$p' /app/log.txt >\"\$tmp\";"
  for marker in \
    ATRI_AI_RUNTIME_GUARD_V153_INSTALLED \
    ATRI_SYSTEM_CONTRACT_GUARD_V154_INSTALLED \
    ATRI_SYSTEM_POST_IMPORT_GUARD_V154_INSTALLED \
    ATRI_STICKER_CHAT_PRIVACY_V154_INSTALLED \
    ATRI_WEBAPP_NETWORK_GUARD_V154_INSTALLED \
    ATRI_XLSX_FORMULA_SAFETY_V1541_INSTALLED \
    ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALLED; do
    command+=" grep -q '$marker' \"\$tmp\" || exit 20;"
  done
  command+=" ! grep -Eq 'ATRI_(AI_RUNTIME_GUARD_V153|SYSTEM_CONTRACT_GUARD_V154|STICKER_CHAT_PRIVACY_V154|WEBAPP_NETWORK_GUARD_V154|XLSX_FORMULA_SAFETY_V1541|ARTIFACT_RELEVANCE_GUARD_V1542)_INSTALL_FAILED' \"\$tmp\""
  debian_run "$command" >/dev/null 2>&1
}

browser_runtime_probe() {
  local endpoint
  endpoint="$(debian_run "/app/mltbenv/bin/python -c 'import json,pathlib; p=pathlib.Path(\"/app/atri_data/atri_skill_runtime.json\"); print(\"\" if not p.is_file() else str(json.loads(p.read_text()).get(\"cdp_endpoint\",\"\")))'" 2>/dev/null | tail -n1 | tr -d '\r')"
  if [[ -z "$endpoint" ]]; then
    warn BROWSER_RUNTIME "optional CDP runtime state absent; pure webapp safety probe still required"
    return 0
  fi
  [[ "$endpoint" =~ ^http://127\.0\.0\.1:([0-9]{1,5})$ ]] || {
    fail BROWSER_RUNTIME "unsafe/invalid cdp_endpoint=$endpoint"
    return 1
  }
  if curl -fsS --max-time 5 "$endpoint/json/version" | grep -q 'Browser'; then
    pass BROWSER_RUNTIME "CDP reachable endpoint=$endpoint"
    return 0
  fi
  fail BROWSER_RUNTIME "CDP state exists but endpoint is unreachable: $endpoint"
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

  if ((PACKAGE_MUTATED == 1)) && [[ -n "$APPLY_BACKUP" ]]; then
    compute_package_delta "$APPLY_BACKUP" || true
    rollback_new_packages "$APPLY_BACKUP" || true
  fi

  if [[ "$(local_health_state)" == HEALTHY && "$(bot_lock_state || echo UNKNOWN)" == HELD ]] && \
     require_v151_gate_a >/dev/null 2>&1 && \
     require_v152_gate_b1 >/dev/null 2>&1 && \
     require_v153_baseline >/dev/null 2>&1; then
    pass AUTO_ROLLBACK "pre-V154 source/package state restored; V151/V152/V153 healthy"
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
  require_v153_baseline

  if source_patcher verify >/dev/null 2>&1; then
    fail MODE "V154 production guards already applied; use status or rollback"
    return 1
  fi

  pane_before="$HEALTHY_PANE"
  BOT_LOG_LINES_BEFORE="$(bot_log_line_count || echo 0)"
  [[ "$BOT_LOG_LINES_BEFORE" =~ ^[0-9]+$ ]] || BOT_LOG_LINES_BEFORE=0
  APPLY_BACKUP="$BACKUP_ROOT/apply-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$APPLY_BACKUP"

  section "DEPENDENCY PREFLIGHT"
  if ! ensure_dependencies "$APPLY_BACKUP"; then
    rollback_apply_failure "V154 dependency preflight/install failed"
    return 1
  fi

  section "PATCH LIVE V154 GUARDS"
  if ! source_patcher apply "$APPLY_BACKUP/source"; then
    rollback_apply_failure "V154 source patch failed"
    return 1
  fi
  SOURCE_APPLIED=1
  if ! write_canary_meta "$APPLY_BACKUP"; then
    rollback_apply_failure "V154 backup metadata write failed"
    return 1
  fi
  pass SOURCE_PATCH "7-file guarded patch installed; backup=$APPLY_BACKUP/source"

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
    rollback_apply_failure "V151 observer did not become ready after V154 restart"
    return 1
  fi
  pass BOT_RESTART "old_pane=$pane_before new_pane=$NEW_PANE V151_ready=1"

  if ! source_patcher verify >/dev/null 2>&1; then
    rollback_apply_failure "V154 source verification failed after restart"
    return 1
  fi
  pass SOURCE_VERIFY "V154 hooks/modules compile + SHA verified"

  if ! runtime_markers_ready_after_restart; then
    rollback_apply_failure "one or more V153/V154 runtime install markers missing/failed"
    return 1
  fi
  pass RUNTIME_MARKERS "V153 + V154 + post-import + sticker + webapp + XLSX + RAG installed in real bot process"

  section "ISOLATED PRODUCTION SMOKE"
  if ! probe_result="$(smoke_probe 2>&1)"; then
    printf '%s\n' "$probe_result"
    rollback_apply_failure "V154 isolated production smoke probe failed"
    return 1
  fi
  printf '%s\n' "$probe_result"
  [[ "$probe_result" == *'"ok": true'* || "$probe_result" == *'"ok":true'* ]] || {
    rollback_apply_failure "V154 smoke probe returned unexpected payload"
    return 1
  }
  pass V154_SMOKE "archive/audio/tool-round/sticker/RAG/XLSX/webapp safety probes passed"

  if ! browser_runtime_probe; then
    rollback_apply_failure "configured browser runtime is unhealthy"
    return 1
  fi

  section "PRESERVATION GATES"
  if ! require_v151_gate_a; then
    rollback_apply_failure "V151 Gate A regressed after V154"
    return 1
  fi
  if ! require_v152_gate_b1; then
    rollback_apply_failure "V152 Gate B1 regressed after V154"
    return 1
  fi
  if ! require_v153_baseline; then
    rollback_apply_failure "V153 source baseline regressed after V154"
    return 1
  fi
  if ! boot_lock_fd_clean; then
    rollback_apply_failure "boot-hook FD lock leak detected"
    return 1
  fi
  pass BOOT_LOCK_FD "NO_BOOT_LOCK_FD"

  section "FINAL PRODUCTION"
  if ! require_healthy_production; then
    rollback_apply_failure "final production health/singleton check failed"
    return 1
  fi
  free -h || true
  pass CANARY "V154/V154.1/V154.2 active; Python remains sole Telegram/AI owner"
}

status_canary() {
  local source="NOT_APPLIED" runtime="UNKNOWN" deps="MISSING" fd="UNKNOWN" meta
  require_host
  section "REPO"
  meta="$(repo_meta || true)"
  printf '%s\n' "$meta"
  section "PRODUCTION"
  require_healthy_production || true
  require_v151_gate_a || true
  require_v152_gate_b1 || true
  require_v153_baseline || true

  if source_patcher verify >/dev/null 2>&1; then source=APPLIED; fi
  if dependency_probe >/dev/null 2>&1; then deps=READY; fi
  if debian_run "grep -q 'ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALLED' /app/log.txt 2>/dev/null" >/dev/null 2>&1; then runtime=SEEN; fi
  if boot_lock_fd_clean; then fd=NO_BOOT_LOCK_FD; else fd=CHECK_FAILED; fi

  section "V154 PRODUCTION"
  printf 'source=%s\n' "$source"
  printf 'dependencies=%s\n' "$deps"
  printf 'runtime_marker=%s\n' "$runtime"
  printf 'boot_lock_fd=%s\n' "$fd"
  printf 'last_backup=%s\n' "$(cat "$LAST_BACKUP_FILE" 2>/dev/null || echo none)"
  browser_runtime_probe || true
  printf 'report=%s\n' "$REPORT"
}

rollback_canary() {
  local backup candidate pane
  require_host
  section "REPO"
  require_repo
  backup="$(cat "$LAST_BACKUP_FILE" 2>/dev/null || true)"
  [[ -n "$backup" && -f "$backup/canary-meta.env" && -f "$backup/source/source-manifest.json" ]] || {
    fail ROLLBACK "no complete V154 canary backup available"
    return 1
  }
  candidate="$(awk -F= '$1=="REPO_SHA"{print $2}' "$backup/canary-meta.env")"
  [[ "$candidate" =~ ^[0-9a-f]{40}$ ]] || {
    fail ROLLBACK "invalid V154 backup metadata"
    return 1
  }

  section "ROLLBACK V154 SOURCE"
  source_patcher rollback "$backup/source"
  pass SOURCE_ROLLBACK "restored exact pre-V154 7-file source snapshot"

  pane="$(bot_pane_pid || true)"
  [[ "$pane" =~ ^[0-9]+$ ]] || {
    fail BOT_ROLLBACK "production pane missing before controlled restart"
    return 1
  }
  clear_v151_ready
  restart_bot_controlled "$pane"
  wait_v151_ready
  pass BOT_ROLLBACK "old_pane=$pane new_pane=$NEW_PANE"

  if [[ -f "$backup/pip-new.txt" ]]; then
    rollback_new_packages "$backup"
    pass PACKAGE_ROLLBACK "removed only distributions introduced by V154 canary"
  fi

  require_healthy_production
  require_v151_gate_a
  require_v152_gate_b1
  require_v153_baseline
  pass ROLLBACK "V154 disabled; pre-V154 production baseline restored"
}

finish_report() {
  local rc=$?
  trap - EXIT
  echo "END: $(date)"
  echo "REPORT: $REPORT"
  exit "$rc"
}

trap finish_report EXIT
section "ATRI V154 PRODUCTION CANARY"
echo "START: $(date)"
echo "ACTION: $ACTION"
echo "REPORT: $REPORT"

case "$ACTION" in
    status) status_canary ;;
    apply) apply_canary ;;
    rollback) rollback_canary ;;
esac
