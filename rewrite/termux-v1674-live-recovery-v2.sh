#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
REPO="/app"
WATCH_SESSION="atri-v150-watchdog"
BOT_SESSION="prixok-bot"
HOST_HEALTH="$HOST_HOME/atri-production-local-health.sh"
MAIN_LIVE_LOG="$HOST_HOME/.atri-v150-main-live.log"
RECOVERY_TIMEOUT="${ATRI_V1674_RECOVERY_TIMEOUT:-240}"
STABILITY_ROUNDS="${ATRI_V1674_TEST_ROUNDS:-10}"
STABILITY_INTERVAL="${ATRI_V1674_TEST_INTERVAL:-6}"
SUPERVISOR_TIMEOUT="${ATRI_V1674_SUPERVISOR_TIMEOUT:-90}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_DIR="$HOST_HOME/.local/state/atri-v1674-live-recovery-v2/$RUN_ID"
REPORT="$HOST_HOME/storage/downloads/atri-v1674-live-recovery-v2-$RUN_ID.txt"
BUNDLE="$HOST_HOME/storage/downloads/atri-v1674-live-recovery-v2-$RUN_ID.tar.gz"
mkdir -p "$RUN_DIR" "$(dirname "$REPORT")"
: >"$REPORT"
exec > >(tee -a "$REPORT") 2>&1

positive_int(){ [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]; }
section(){ printf '\n===== %s =====\n' "$1"; }
log(){ printf '%s %s\n' "$(date '+%F %T')" "$*"; }
pass(){ log "[PASS] $*"; }
warn(){ log "[WARN] $*"; }
debian(){ proot-distro login debian -- bash -lc "$1"; }
has(){ tmux has-session -t "$1" 2>/dev/null; }
pane_pid(){ tmux list-panes -t "$1" -F '#{pane_pid}' 2>/dev/null | head -n1 | tr -d '\r'; }
capture(){ tmux capture-pane -p -S -3000 -t "$1" 2>/dev/null || true; }

session_files(){ debian "find -L /app -maxdepth 1 -type f -name '[0-9]*.session' -printf '%p|%s\\n' 2>/dev/null | sort -u"; }
session_count(){ session_files | awk 'NF{n++} END{print n+0}'; }
bot_lock_state(){
  debian '
set -Eeuo pipefail
p=/app/.atri-prixok-bot-v133.lock
[[ -e "$p" ]] || { echo MISSING; exit 0; }
exec 9<>"$p"
if flock -n 9; then flock -u 9; echo FREE; else echo HELD; fi
' 2>/dev/null | tail -n1 | tr -d '\r'
}
ready(){
  has "$BOT_SESSION" || return 1
  local pane
  pane="$(capture "$BOT_SESSION")"
  grep -q 'Bot Started!' <<<"$pane" && grep -q 'ATRI_PRODUCTION_WORKER_V133_ONLINE' <<<"$pane"
}
health_ok(){ [[ -x "$HOST_HEALTH" ]] && "$HOST_HEALTH" --quiet >/dev/null 2>&1; }

wait_session(){
  local name="$1" timeout="$2"
  local deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do has "$name" && return 0; sleep 2; done
  return 1
}
wait_lock_release(){
  local timeout="$1"
  local deadline=$((SECONDS + timeout))
  local state
  while ((SECONDS < deadline)); do
    state="$(bot_lock_state || true)"
    [[ "$state" != HELD ]] && return 0
    sleep 1
  done
  return 1
}
wait_ready(){
  local timeout="$1"
  local deadline=$((SECONDS + timeout)) pane flood last=0
  while ((SECONDS < deadline)); do
    ready && return 0
    pane="$(capture "$BOT_SESSION")"
    flood="$(grep -E 'TELEGRAM_BOT_START_FLOOD_WAIT|FloodWait|FLOOD_WAIT|ImportBotAuthorization' <<<"$pane" | tail -n1 || true)"
    if ((SECONDS - last >= 15)); then
      [[ -n "$flood" ]] && warn "waiting READY; Telegram rate-limit=$flood" || log '[WAIT] READY'
      last=$SECONDS
    fi
    sleep 2
  done
  return 1
}

supervisor_candidates(){
  {
    [[ -f "$MAIN_LIVE_LOG" ]] && grep -aoE 'SUPERVISOR_START pid=[0-9]+' "$MAIN_LIVE_LOG" 2>/dev/null || true
    capture "$WATCH_SESSION" | grep -aoE 'SUPERVISOR_START pid=[0-9]+' || true
  } | sed -E 's/.*pid=([0-9]+)/\1/' | tac | awk '!seen[$0]++'
}
supervisor_pid(){
  local pid args
  while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    args="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    [[ "$args" == *atri-supervisor* ]] || continue
    printf '%s\n' "$pid"
    return 0
  done < <(supervisor_candidates)
  return 1
}
wait_new_supervisor(){
  local old="$1" timeout="$2"
  local deadline=$((SECONDS + timeout)) pid
  while ((SECONDS < deadline)); do
    pid="$(supervisor_pid || true)"
    [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$old" ]] && { printf '%s\n' "$pid"; return 0; }
    sleep 1
  done
  return 1
}

collect(){
  local tag="$1"
  local d="$RUN_DIR/$tag"
  mkdir -p "$d"
  {
    echo "DATE=$(date)"
    echo "WATCH_PID=$(pane_pid "$WATCH_SESSION" 2>/dev/null || true)"
    echo "BOT_PID=$(pane_pid "$BOT_SESSION" 2>/dev/null || true)"
    echo "SUPERVISOR_PID=$(supervisor_pid 2>/dev/null || true)"
    echo "BOT_LOCK=$(bot_lock_state 2>/dev/null || true)"
    echo "SESSION_COUNT=$(session_count 2>/dev/null || true)"
    echo '===== TMUX ====='
    tmux list-panes -a -F 'session=#{session_name} pid=#{pane_pid} dead=#{pane_dead} cmd=#{pane_current_command} start=#{pane_start_command}' 2>&1 || true
    echo '===== MEMORY ====='
    free -h 2>&1 || true
  } >"$d/host.txt" 2>&1
  capture "$BOT_SESSION" >"$d/bot-pane.txt" 2>&1 || true
  capture "$WATCH_SESSION" >"$d/watchdog-pane.txt" 2>&1 || true
  tail -n 2500 "$MAIN_LIVE_LOG" >"$d/main-watchdog.log" 2>&1 || true
}
bundle(){ cp -f "$REPORT" "$RUN_DIR/report.txt" 2>/dev/null || true; tar -C "$RUN_DIR" -czf "$BUNDLE" . 2>/dev/null || true; log "REPORT=$REPORT"; log "BUNDLE=$BUNDLE"; }
fail(){ log "[FAIL] $*"; collect failure || true; bundle || true; echo 'ATRI_V1674_LIVE_RECOVERY_V2=FAIL'; exit 1; }

if [[ "${1:-}" == "--self-test" ]]; then
  bash -n "$0"
  grep -q 'find -L /app' "$0"
  grep -q 'tmux send-keys -t "$BOT_SESSION" C-c' "$0"
  ! grep -q 'tmux kill-session -t "$BOT_SESSION"' "$0"
  ! grep -Eq 'local [^\n]*timeout="\$[12]"[^\n]*deadline=.*timeout' "$0"
  ! grep -Eq 'rm[[:space:]].*\.atri-prixok-bot.*lock|git[[:space:]]+(reset|clean)|stash[[:space:]]+pop' "$0"
  echo 'atri v1674 live recovery v2 self-test: PASS'
  exit 0
fi
if [[ "${1:-}" == "--export-only" ]]; then collect manual-export; bundle; echo "$BUNDLE"; exit 0; fi
(($# == 0)) || { echo "Usage: bash $0 [--self-test|--export-only]" >&2; exit 2; }
for value in "$RECOVERY_TIMEOUT" "$STABILITY_ROUNDS" "$STABILITY_INTERVAL" "$SUPERVISOR_TIMEOUT"; do positive_int "$value" || exit 2; done

section 'ATRI V167.4 LIVE RECOVERY V2'
collect before
command -v proot-distro >/dev/null || fail 'proot-distro missing'
command -v tmux >/dev/null || fail 'tmux missing'
debian "cd '$REPO' && git fetch --quiet origin main" || fail 'git fetch origin main failed'
branch="$(debian "cd '$REPO' && git branch --show-current" | tail -n1 | tr -d '\r')"
head="$(debian "cd '$REPO' && git rev-parse HEAD" | tail -n1 | tr -d '\r')"
remote="$(debian "cd '$REPO' && git rev-parse origin/main" | tail -n1 | tr -d '\r')"
[[ "$branch" == main && "$head" == "$remote" ]] || fail "repo mismatch branch=$branch head=$head remote=$remote"
critical_dirty="$(debian "cd '$REPO' && git status --porcelain -- bot rewrite" | tr -d '\r')"
[[ -z "$critical_dirty" ]] || fail "critical source dirty: $critical_dirty"
has "$WATCH_SESSION" || fail 'watchdog tmux missing'
has "$BOT_SESSION" || fail 'bot tmux missing; run orphan-rescue first if lock remains HELD'
ready || fail 'current bot not ONLINE'
[[ "$(bot_lock_state)" == HELD ]] || fail 'singleton lock not HELD'
[[ "$(session_count)" -ge 1 ]] || fail 'persistent session missing'
health_ok || fail 'health failed before recovery'
pass "LIVE_BASELINE main=$head"

section 'PRE-STABILITY'
watch_before="$(pane_pid "$WATCH_SESSION")"
bot_before="$(pane_pid "$BOT_SESSION")"
for ((i=1;i<=STABILITY_ROUNDS;i++)); do
  sleep "$STABILITY_INTERVAL"
  has "$WATCH_SESSION" && has "$BOT_SESSION" || fail "session vanished round=$i"
  [[ "$(pane_pid "$WATCH_SESSION")" == "$watch_before" ]] || fail "watchdog pane changed round=$i"
  [[ "$(pane_pid "$BOT_SESSION")" == "$bot_before" ]] || fail "bot pane changed round=$i"
  [[ "$(bot_lock_state)" == HELD ]] || fail "lock not HELD round=$i"
  [[ "$(session_count)" -ge 1 ]] || fail "session file missing round=$i"
  health_ok || fail "health failed round=$i"
  printf '[CHECK %02d/%02d] PASS\n' "$i" "$STABILITY_ROUNDS"
done
pass "PRE_STABILITY=$STABILITY_ROUNDS/$STABILITY_ROUNDS"

section 'CONTROLLED BOT RECOVERY'
pre_sessions="$(session_files)"
old_bot="$bot_before"
# Send SIGINT through the existing PTY so PRoot and the foreground Python worker
# unwind together. Do not destroy the tmux server-side session first: doing so
# can orphan the guest worker while it still owns the singleton lock.
tmux send-keys -t "$BOT_SESSION" C-c || fail 'cannot send controlled SIGINT to bot pane'
wait_lock_release 45 || fail 'bot worker did not release singleton lock after controlled SIGINT; refusing destructive fallback'
# The old pane may close immediately or remain briefly while the wrapper exits.
# Watchdog owns creation of the next production session.
wait_session "$BOT_SESSION" "$RECOVERY_TIMEOUT" || fail 'watchdog did not provide bot tmux after controlled SIGINT'
new_bot="$(pane_pid "$BOT_SESSION" || true)"
if [[ "$new_bot" == "$old_bot" ]]; then
  local_deadline=$((SECONDS + RECOVERY_TIMEOUT))
  while ((SECONDS < local_deadline)); do
    sleep 2
    new_bot="$(pane_pid "$BOT_SESSION" || true)"
    [[ "$new_bot" =~ ^[0-9]+$ && "$new_bot" != "$old_bot" ]] && break
  done
fi
[[ "$new_bot" =~ ^[0-9]+$ && "$new_bot" != "$old_bot" ]] || fail "bot pane did not rotate old=$old_bot new=${new_bot:-NONE}"
wait_ready "$RECOVERY_TIMEOUT" || fail 'recovered bot did not reach ONLINE'
recovered="$(capture "$BOT_SESSION")"
! grep -Eq 'TELEGRAM_BOT_START_FLOOD_WAIT|FLOOD_WAIT|ImportBotAuthorization' <<<"$recovered" || fail 'recovered boot re-authorized Telegram'
[[ "$(session_count)" -ge 1 ]] || fail 'persistent session disappeared after recovery'
[[ "$(bot_lock_state)" == HELD ]] || fail 'lock not HELD after recovery'
health_ok || fail 'health failed after recovery'
post_sessions="$(session_files)"
[[ -n "$pre_sessions" && -n "$post_sessions" ]] || fail 'session metadata unavailable'
pass "BOT_SESSION_RECOVERY=$old_bot->$new_bot persistent-session=PASS no-FloodWait=PASS"

section 'CONTROLLED SUPERVISOR RECOVERY'
watch_before="$(pane_pid "$WATCH_SESSION")"
bot_before_sup="$(pane_pid "$BOT_SESSION")"
old_sup="$(supervisor_pid || true)"
[[ "$old_sup" =~ ^[0-9]+$ ]] || fail 'cannot identify supervisor PID'
kill -TERM "$old_sup" || fail "cannot TERM supervisor pid=$old_sup"
new_sup="$(wait_new_supervisor "$old_sup" "$SUPERVISOR_TIMEOUT" || true)"
[[ "$new_sup" =~ ^[0-9]+$ && "$new_sup" != "$old_sup" ]] || fail "supervisor did not respawn old=$old_sup new=${new_sup:-NONE}"
[[ "$(pane_pid "$WATCH_SESSION")" == "$watch_before" ]] || fail 'watchdog wrapper pane changed'
[[ "$(pane_pid "$BOT_SESSION")" == "$bot_before_sup" ]] || fail 'healthy bot restarted during supervisor-only recovery'
ready || fail 'bot not ONLINE after supervisor recovery'
health_ok || fail 'health failed after supervisor recovery'
pass "SUPERVISOR_RECOVERY=$old_sup->$new_sup bot_unchanged=$bot_before_sup"

section 'POST-STABILITY'
bot_final="$(pane_pid "$BOT_SESSION")"
watch_final="$(pane_pid "$WATCH_SESSION")"
for ((i=1;i<=STABILITY_ROUNDS;i++)); do
  sleep "$STABILITY_INTERVAL"
  has "$WATCH_SESSION" && has "$BOT_SESSION" || fail "final session missing round=$i"
  [[ "$(pane_pid "$WATCH_SESSION")" == "$watch_final" ]] || fail "final watchdog changed round=$i"
  [[ "$(pane_pid "$BOT_SESSION")" == "$bot_final" ]] || fail "final bot changed round=$i"
  [[ "$(bot_lock_state)" == HELD ]] || fail "final lock not HELD round=$i"
  [[ "$(session_count)" -ge 1 ]] || fail "final session file missing round=$i"
  health_ok || fail "final health failed round=$i"
  printf '[CHECK %02d/%02d] PASS\n' "$i" "$STABILITY_ROUNDS"
done
pass "POST_STABILITY=$STABILITY_ROUNDS/$STABILITY_ROUNDS"
collect success
bundle
pass 'FINAL_AUDIT=PASS'
echo "MAIN_SHA=$head"
echo 'ATRI_V1674_LIVE_RECOVERY_V2=PASS'
echo "REPORT=$REPORT"
echo "BUNDLE=$BUNDLE"
