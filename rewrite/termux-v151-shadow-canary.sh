#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

EXPECTED_BRANCH="main"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
STATE_DIR="$HOST_HOME/.local/state/atri-v151-shadow"
BACKUP_ROOT="$STATE_DIR/backups"
ENABLE_FILE="$STATE_DIR/enabled"
RUNTIME_ENV="$STATE_DIR/runtime.env"
LAST_BACKUP_FILE="$STATE_DIR/last-backup"
DEPLOY_STATE_DIR="$HOST_HOME/.local/state/atri-v150-deploy"
DEPLOY_MANAGER="$HOST_HOME/termux-v150-deploy.sh"
V150_BIN="$HOST_HOME/.local/lib/atri-v150/atri-supervisor"
ACTION="${1:-status}"
SHADOW_ADDR="${ATRI_TELEGRAM_SHADOW_ADDR:-127.0.0.1:18750}"
SHADOW_URL="http://$SHADOW_ADDR"
RESTART_TIMEOUT="${ATRI_V151_RESTART_TIMEOUT:-150}"
HEALTH_TIMEOUT="${ATRI_V151_HEALTH_TIMEOUT:-180}"

ROOTFS_DIR=""
REPO_SHA=""
REPORT=""
APPLY_BACKUP=""
SOURCE_APPLIED=0
UPGRADE_DONE=0
BOT_RESTART_ATTEMPTED=0
ROLLBACK_RUNNING=0

usage() {
  cat <<'EOF'
Usage: termux-v151-shadow-canary.sh <command>

Commands:
  status       Read-only V151 shadow + production status.
  apply        Guardedly patch the live Python worker, upgrade V150, restart once,
               validate local shadow ingress, and auto-rollback on failure.
  rollback     Restore the exact source/runtime snapshot created by the last apply.
  --self-test  CI syntax/contract checks.

This manager never runs git pull/reset/checkout/clean and never replaces /app.
Only /app/bot/__main__.py and /app/bot/modules/atri_v150_shadow.py are touched,
through the guarded source patcher with a timestamped backup.
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
  [[ "$ENABLE_FILE" == */.local/state/atri-v151-shadow/enabled ]]
  [[ "$RUNTIME_ENV" == */.local/state/atri-v151-shadow/runtime.env ]]
  positive_int "$RESTART_TIMEOUT"
  positive_int "$HEALTH_TIMEOUT"
  validate_shadow_addr
  for cmd in status apply rollback; do
    grep -q "^    $cmd)" "$0"
  done
  if grep -Eq 'git[[:space:]]+(pull|reset|checkout|clean)|update\.py|rm[[:space:]]+-rf[[:space:]]+/app' "$0"; then
    echo "v151 shadow self-test: FAIL (forbidden source mutation command)" >&2
    exit 1
  fi
  grep -q 'tmux send-keys -t prixok-bot C-c' "$0"
  grep -q 'v151_shadow_patch.py' "$0"
  grep -q 'ATRI_V150_TELEGRAM_SHADOW=true' "$0"
  echo "v151 shadow self-test: PASS"
  exit 0
fi

case "$ACTION" in
  status|apply|rollback) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! positive_int "$RESTART_TIMEOUT" || ! positive_int "$HEALTH_TIMEOUT"; then
  echo "invalid ATRI_V151_RESTART_TIMEOUT/ATRI_V151_HEALTH_TIMEOUT" >&2
  exit 2
fi
if ! validate_shadow_addr; then
  echo "ATRI_TELEGRAM_SHADOW_ADDR must be loopback 127.0.0.1:<1-65535>" >&2
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
REPORT="$REPORT_DIR/atri-v151-shadow-${ACTION}-$(date +%Y%m%d-%H%M%S).txt"
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
  [[ -f "$ROOTFS_DIR$DEBIAN_CLONE/rewrite/v151_shadow_patch.py" ]] || {
    fail REPO "v151 source patcher missing"
    return 1
  }
  [[ -f "$ROOTFS_DIR$DEBIAN_CLONE/bot/modules/atri_v150_shadow.py" ]] || {
    fail REPO "v151 Python shadow module missing"
    return 1
  }
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

atomic_install() {
  local src="$1" dst="$2" tmp
  tmp="$dst.tmp.$$"
  install -m 700 "$src" "$tmp"
  mv -f "$tmp" "$dst"
}

install_deploy_manager() {
  atomic_install "$ROOTFS_DIR$DEBIAN_CLONE/rewrite/termux-v150-deploy.sh" "$DEPLOY_MANAGER"
}

write_shadow_runtime_state() {
  local tmp="$RUNTIME_ENV.tmp.$$"
  cat >"$tmp" <<EOF
ATRI_V150_TELEGRAM_SHADOW=true
ATRI_TELEGRAM_SHADOW_ADDR=$SHADOW_ADDR
ATRI_TELEGRAM_SHADOW_RETRY=15
EOF
  chmod 600 "$tmp"
  mv -f "$tmp" "$RUNTIME_ENV"
  printf 'enabled_at=%s\nrepo_sha=%s\n' "$(date +%s)" "$REPO_SHA" >"$ENABLE_FILE"
  chmod 600 "$ENABLE_FILE"
}

disable_shadow_runtime_state() {
  rm -f "$ENABLE_FILE" "$RUNTIME_ENV"
}

source_patcher() {
  local action="$1" backup="${2:-}"
  local command="cd '$DEBIAN_CLONE' && python3 rewrite/v151_shadow_patch.py '$action' --source-root '$DEBIAN_CLONE' --live-root /app"
  if [[ -n "$backup" ]]; then
    command+=" --backup-dir '$backup'"
  fi
  debian_run "$command"
}

shadow_health() {
  curl -fsS --max-time 4 "$SHADOW_URL/healthz"
}

shadow_synthetic_probe() {
  local code
  code="$(curl -sS --max-time 4 -o "$STATE_DIR/probe-response.json" -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -X POST "$SHADOW_URL/v1/telegram/shadow" \
    --data '{"version":1,"kind":"message","message_id":2147483000,"chat_type":"private","command":"v151_probe"}' || true)"
  [[ "$code" == 202 ]]
}

wait_ingress() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  while ((SECONDS < deadline)); do
    if shadow_health >/dev/null 2>&1; then return 0; fi
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
  tmux send-keys -t prixok-bot C-c
  wait_new_bot_healthy "$old_pane"
}

shadow_log_marker_after() {
  local offset="$1"
  debian_run "if [ -f /app/log.txt ]; then tail -c +$((offset + 1)) /app/log.txt | grep -q 'ATRI_V150_TELEGRAM_SHADOW_ENABLED'; else exit 1; fi" >/dev/null 2>&1
}

boot_lock_fd_clean() {
  local -a pids=()
  mapfile -t pids < <(v150_watchdog_pids)
  ((${#pids[@]} == 1)) || return 1
  ! ls -l "/proc/${pids[0]}/fd" 2>/dev/null | grep -q 'boot-hook\.lock'
}

write_canary_meta() {
  local backup="$1" pane_before="$2" deploy_backup="$3" previous_sha="$4"
  cat >"$backup/canary-meta.env" <<EOF
REPO_SHA=$REPO_SHA
PANE_BEFORE=$pane_before
DEPLOY_BACKUP=$deploy_backup
PREVIOUS_DEPLOYED_SHA=$previous_sha
EOF
  chmod 600 "$backup/canary-meta.env"
  printf '%s\n' "$backup" >"$LAST_BACKUP_FILE"
}

rollback_apply_failure() {
  local reason="$1" pane
  ((ROLLBACK_RUNNING == 0)) || return 0
  ROLLBACK_RUNNING=1
  section "AUTO ROLLBACK"
  info "reason=$reason"
  disable_shadow_runtime_state || true

  if ((SOURCE_APPLIED == 1)) && [[ -n "$APPLY_BACKUP" ]]; then
    source_patcher rollback "$APPLY_BACKUP/source" || true
  fi

  if ((UPGRADE_DONE == 1)) && [[ -x "$DEPLOY_MANAGER" ]]; then
    bash "$DEPLOY_MANAGER" rollback || true
  fi

  if ((BOT_RESTART_ATTEMPTED == 1)); then
    pane="$(bot_pane_pid || true)"
    if [[ "$pane" =~ ^[0-9]+$ ]]; then
      restart_bot_controlled "$pane" || true
    else
      wait_new_bot_healthy 0 || true
    fi
  fi

  if [[ "$(local_health_state)" == HEALTHY && "$(bot_lock_state || echo UNKNOWN)" == HELD ]]; then
    pass AUTO_ROLLBACK "previous production state restored"
  else
    fail AUTO_ROLLBACK "rollback attempted; production needs manual inspection" || true
  fi
}

apply_canary() {
  local pane_before log_offset previous_sha deploy_backup
  require_host
  section "REPO"
  require_repo
  section "PRE-PRODUCTION"
  require_healthy_production
  [[ ! -e "$ENABLE_FILE" ]] || {
    fail MODE "shadow canary already enabled; use status or rollback"
    return 1
  }

  pane_before="$HEALTHY_PANE"
  log_offset="$(debian_run 'stat -c %s /app/log.txt 2>/dev/null || echo 0' | tail -n1 | tr -d '\r')"
  [[ "$log_offset" =~ ^[0-9]+$ ]] || log_offset=0
  previous_sha="$(cat "$DEPLOY_STATE_DIR/current-sha" 2>/dev/null || echo unmanaged)"
  APPLY_BACKUP="$BACKUP_ROOT/apply-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$APPLY_BACKUP"

  section "PATCH LIVE WORKER"
  if ! source_patcher apply "$APPLY_BACKUP/source"; then
    rollback_apply_failure "source patch failed"
    return 1
  fi
  SOURCE_APPLIED=1
  pass SOURCE_PATCH "guarded V151 hook installed; backup=$APPLY_BACKUP/source"

  write_shadow_runtime_state
  pass SHADOW_ENABLE "host sentinel + runtime.env installed"

  section "UPGRADE V150 SUPERVISOR"
  install_deploy_manager
  if ! bash "$DEPLOY_MANAGER" upgrade; then
    rollback_apply_failure "V150 upgrade failed"
    return 1
  fi
  UPGRADE_DONE=1
  deploy_backup="$(cat "$DEPLOY_STATE_DIR/last-backup" 2>/dev/null || true)"
  [[ -n "$deploy_backup" ]] || {
    rollback_apply_failure "deploy backup metadata missing"
    return 1
  }
  write_canary_meta "$APPLY_BACKUP" "$pane_before" "$deploy_backup" "$previous_sha"
  pass V150_UPGRADE "repo_sha=$REPO_SHA deploy_backup=$deploy_backup"

  if ! wait_ingress; then
    rollback_apply_failure "shadow ingress did not become healthy"
    return 1
  fi
  pass SHADOW_INGRESS "$(shadow_health)"

  section "CONTROLLED BOT RESTART"
  BOT_RESTART_ATTEMPTED=1
  if ! restart_bot_controlled "$pane_before"; then
    rollback_apply_failure "bot did not restart healthy within timeout"
    return 1
  fi
  pass BOT_RESTART "old_pane=$pane_before new_pane=$NEW_PANE"

  if ! shadow_log_marker_after "$log_offset"; then
    rollback_apply_failure "Python shadow enable marker missing after restart"
    return 1
  fi
  pass PYTHON_OBSERVER "ATRI_V150_TELEGRAM_SHADOW_ENABLED observed after restart"

  if ! shadow_synthetic_probe; then
    rollback_apply_failure "local ingress synthetic probe failed"
    return 1
  fi
  pass SYNTHETIC_PROBE "POST /v1/telegram/shadow -> 202"

  if ! boot_lock_fd_clean; then
    rollback_apply_failure "boot-hook FD lock leak detected"
    return 1
  fi
  pass BOOT_LOCK_FD "NO_BOOT_LOCK_FD"

  section "FINAL PRODUCTION"
  require_healthy_production
  pass CANARY "V151 shadow enabled observe-only; Python remains Telegram owner"
}

status_canary() {
  local enabled="NO" source="NOT_APPLIED" ingress="DOWN" fd="UNKNOWN" response meta
  require_host
  section "REPO"
  meta="$(repo_meta || true)"
  printf '%s\n' "$meta"
  section "PRODUCTION"
  require_healthy_production || true

  [[ -f "$ENABLE_FILE" ]] && enabled=YES
  if source_patcher verify >/dev/null 2>&1; then source=APPLIED; fi
  if response="$(shadow_health 2>/dev/null)"; then ingress="$response"; fi
  if boot_lock_fd_clean; then fd=NO_BOOT_LOCK_FD; else fd=CHECK_FAILED; fi

  section "V151 SHADOW"
  printf 'enabled=%s\n' "$enabled"
  printf 'source=%s\n' "$source"
  printf 'ingress=%s\n' "$ingress"
  printf 'boot_lock_fd=%s\n' "$fd"
  printf 'last_backup=%s\n' "$(cat "$LAST_BACKUP_FILE" 2>/dev/null || echo none)"
  printf 'report=%s\n' "$REPORT"
}

rollback_canary() {
  local backup meta candidate deploy_backup current_deploy_backup pane
  require_host
  section "REPO"
  require_repo
  backup="$(cat "$LAST_BACKUP_FILE" 2>/dev/null || true)"
  [[ -n "$backup" && -f "$backup/canary-meta.env" && -f "$backup/source/source-manifest.json" ]] || {
    fail ROLLBACK "no complete V151 canary backup available"
    return 1
  }
  meta="$backup/canary-meta.env"
  candidate="$(awk -F= '$1=="REPO_SHA"{print $2}' "$meta")"
  deploy_backup="$(sed -n 's/^DEPLOY_BACKUP=//p' "$meta")"
  current_deploy_backup="$(cat "$DEPLOY_STATE_DIR/last-backup" 2>/dev/null || true)"
  if [[ "$current_deploy_backup" != "$deploy_backup" ]]; then
    fail ROLLBACK "deploy backup pointer changed since canary; refusing stale rollback"
    return 1
  fi
  if [[ "$(cat "$DEPLOY_STATE_DIR/current-sha" 2>/dev/null || true)" != "$candidate" ]]; then
    fail ROLLBACK "deployed SHA changed since canary; refusing stale rollback"
    return 1
  fi

  section "ROLLBACK V151"
  disable_shadow_runtime_state
  source_patcher rollback "$backup/source"
  pass SOURCE_ROLLBACK "restored guarded source backup"
  bash "$DEPLOY_MANAGER" rollback
  pass RUNTIME_ROLLBACK "restored pre-canary V150 runtime"

  pane="$(bot_pane_pid || true)"
  [[ "$pane" =~ ^[0-9]+$ ]] || {
    fail BOT_ROLLBACK "production pane missing before controlled restart"
    return 1
  }
  restart_bot_controlled "$pane"
  pass BOT_ROLLBACK "old_pane=$pane new_pane=$NEW_PANE"
  require_healthy_production
  pass ROLLBACK "V151 shadow disabled and pre-canary worker/runtime restored"
}

section "ATRI V151 TELEGRAM SHADOW CANARY"
echo "START: $(date)"
echo "ACTION: $ACTION"
echo "REPORT: $REPORT"

rc=0
case "$ACTION" in
    status) status_canary || rc=$? ;;
    apply) apply_canary || rc=$? ;;
    rollback) rollback_canary || rc=$? ;;
esac

echo "END: $(date)"
echo "REPORT: $REPORT"
exit "$rc"
