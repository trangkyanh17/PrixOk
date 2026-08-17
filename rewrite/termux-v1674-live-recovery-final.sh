#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# V167.4 live recovery verifier.
# Run from the Termux host after the exact-main bot has already reached
# Bot Started!/ATRI_PRODUCTION_WORKER_V133_ONLINE. It does not cut over source,
# delete locks, or restart the watchdog before validation. It follows /app when
# validating persistent Pyrogram storage because /app is a symlink in PRoot.

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
REPO="/app"
WATCH_SESSION="atri-v150-watchdog"
BOT_SESSION="prixok-bot"
HOST_HEALTH="$HOST_HOME/atri-production-local-health.sh"
MAIN_LIVE_LOG="$HOST_HOME/atri-v150-main-live.log"
RECOVERY_TIMEOUT="${ATRI_V1674_RECOVERY_TIMEOUT:-240}"
STABILITY_ROUNDS="${ATRI_V1674_TEST_ROUNDS:-10}"
STABILITY_INTERVAL="${ATRI_V1674_TEST_INTERVAL:-6}"
SUPERVISOR_TIMEOUT="${ATRI_V1674_SUPERVISOR_TIMEOUT:-90}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_DIR="$HOST_HOME/.local/state/atri-v1674-live-recovery/$RUN_ID"
REPORT="$HOST_HOME/storage/downloads/atri-v1674-live-recovery-$RUN_ID.txt"
BUNDLE="$HOST_HOME/storage/downloads/atri-v1674-live-recovery-$RUN_ID.tar.gz"
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
pane_start(){ tmux list-panes -t "$1" -F '#{pane_start_command}' 2>/dev/null | head -n1; }
capture(){ tmux capture-pane -p -S -3000 -t "$1" 2>/dev/null || true; }

session_files(){
  debian "find -L /app -maxdepth 1 -type f -name '[0-9]*.session' -printf '%p|%s\\n' 2>/dev/null | sort -u"
}
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

wait_session(){
  local name="$1" timeout="$2" deadline=$((SECONDS+timeout))
  while ((SECONDS < deadline)); do has "$name" && return 0; sleep 2; done
  return 1
}

wait_ready(){
  local timeout="$1" deadline=$((SECONDS+timeout)) pane flood last=0
  while ((SECONDS < deadline)); do
    if ready; then return 0; fi
    pane="$(capture "$BOT_SESSION")"
    flood="$(grep -E 'TELEGRAM_BOT_START_FLOOD_WAIT|FloodWait|FLOOD_WAIT|ImportBotAuthorization' <<<"$pane" | tail -n1 || true)"
    if ((SECONDS-last>=15)); then
      [[ -n "$flood" ]] && warn "waiting READY; Telegram rate-limit=$flood" || log "[WAIT] READY elapsed=${SECONDS}s"
      last=$SECONDS
    fi
    sleep 2
  done
  return 1
}

health_ok(){ [[ -x "$HOST_HEALTH" ]] && "$HOST_HEALTH" --quiet >/dev/null 2>&1; }

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

  while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    args="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    [[ "$args" == *atri-supervisor* ]] || continue
    printf '%s\n' "$pid"
    return 0
  done < <(pgrep -f "$HOST_HOME/.local/lib/atri-v150/atri-supervisor" 2>/dev/null || true)
  return 1
}

wait_new_supervisor(){
  local old="$1" timeout="$2" deadline=$((SECONDS+timeout)) pid
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
    echo "WATCH_START=$(pane_start "$WATCH_SESSION" 2>/dev/null || true)"
    echo "BOT_START=$(pane_start "$BOT_SESSION" 2>/dev/null || true)"
    echo "SUPERVISOR_PID=$(supervisor_pid 2>/dev/null || true)"
    echo "BOT_LOCK=$(bot_lock_state 2>/dev/null || true)"
    echo "SESSION_COUNT=$(session_count 2>/dev/null || true)"
    echo '===== SESSION METADATA ====='
    session_files 2>/dev/null || true
    echo '===== TMUX ====='
    tmux list-panes -a -F 'session=#{session_name} pid=#{pane_pid} dead=#{pane_dead} cmd=#{pane_current_command} start=#{pane_start_command}' 2>&1 || true
    echo '===== MEMORY ====='
    free -h 2>&1 || true
    grep -E 'MemAvailable|SwapTotal|SwapFree' /proc/meminfo 2>/dev/null || true
  } >"$d/host.txt" 2>&1
  capture "$BOT_SESSION" >"$d/bot-pane.txt" 2>&1 || true
  capture "$WATCH_SESSION" >"$d/watchdog-pane.txt" 2>&1 || true
  tail -n 2500 "$MAIN_LIVE_LOG" >"$d/main-watchdog.log" 2>&1 || true
  debian '
cd /app || exit 0
echo "===== GIT ====="
git status --short --branch 2>&1 || true
git rev-parse HEAD 2>&1 || true
echo "===== SESSION FILES ====="
find -L /app -maxdepth 1 -type f \( -name "*.session" -o -name "*.session-journal" \) -printf "%p %s bytes %TY-%Tm-%Td %TH:%TM:%TS\\n" 2>/dev/null || true
echo "===== LOCK ====="
ls -l /app/.atri-prixok-bot-v133.lock 2>&1 || true
cat /app/.atri-prixok-bot-v133.lock 2>&1 || true
' >"$d/debian.txt" 2>&1 || true
}

bundle(){
  cp -f "$REPORT" "$RUN_DIR/report.txt" 2>/dev/null || true
  tar -C "$RUN_DIR" -czf "$BUNDLE" . 2>/dev/null || true
  log "REPORT=$REPORT"
  log "BUNDLE=$BUNDLE"
}

fail(){
  log "[FAIL] $*"
  collect failure || true
  bundle || true
  echo 'ATRI_V1674_LIVE_RECOVERY=FAIL'
  exit 1
}

if [[ "${1:-}" == "--self-test" ]]; then
  bash -n "$0"
  grep -q 'find -L /app' "$0"
  grep -q 'SUPERVISOR_START pid=' "$0"
  grep -q 'tmux kill-session -t "$BOT_SESSION"' "$0"
  grep -q 'kill -TERM "$old_sup"' "$0"
  grep -q 'ATRI_V1674_LIVE_RECOVERY=PASS' "$0"
  ! grep -Eq 'git[[:space:]]+(pull|reset|checkout|clean)|stash[[:space:]]+pop' "$0"
  ! grep -Eq 'rm[[:space:]].*\.atri-prixok-bot.*lock' "$0"
  echo 'atri v1674 live recovery self-test: PASS'
  exit 0
fi
if [[ "${1:-}" == "--export-only" ]]; then
  collect manual-export
  bundle
  echo "$BUNDLE"
  exit 0
fi
(($#==0)) || { echo "Usage: bash $0 [--self-test|--export-only]" >&2; exit 2; }
for value in "$RECOVERY_TIMEOUT" "$STABILITY_ROUNDS" "$STABILITY_INTERVAL" "$SUPERVISOR_TIMEOUT"; do positive_int "$value" || exit 2; done

section 'ATRI V167.4 LIVE RECOVERY FINAL'
log "RUN_ID=$RUN_ID"
log 'Policy: no source cutover; validate live exact-main runtime; one controlled bot recovery; one controlled supervisor recovery'
collect before

section '1. EXACT MAIN + LIVE RUNTIME'
command -v proot-distro >/dev/null || fail 'proot-distro missing'
command -v tmux >/dev/null || fail 'tmux missing'
debian "cd '$REPO' && git fetch --quiet origin main" || fail 'git fetch origin main failed'
branch="$(debian "cd '$REPO' && git branch --show-current" | tail -n1 | tr -d '\r')"
head="$(debian "cd '$REPO' && git rev-parse HEAD" | tail -n1 | tr -d '\r')"
remote="$(debian "cd '$REPO' && git rev-parse origin/main" | tail -n1 | tr -d '\r')"
[[ "$branch" == main && "$head" == "$remote" ]] || fail "repo mismatch branch=$branch head=$head remote=$remote"
debian "cd '$REPO' && git diff --quiet && git diff --cached --quiet" || fail 'tracked working tree dirty'
has "$WATCH_SESSION" || fail 'V150 watchdog tmux missing'
has "$BOT_SESSION" || fail 'bot tmux missing'
ready || fail 'current bot is not Bot Started!/ONLINE'
[[ "$(bot_lock_state)" == HELD ]] || fail 'bot singleton lock is not HELD'
count="$(session_count)"
[[ "$count" =~ ^[0-9]+$ && "$count" -ge 1 ]] || fail 'persistent session not found through symlink-safe find -L /app'
health_ok || fail 'production health failed before recovery'
pass "live runtime online main=$head persistent_session=$count lock=HELD health=PASS"

section '2. PRE-RECOVERY 10-ROUND STABILITY'
watch_before="$(pane_pid "$WATCH_SESSION")"
bot_before="$(pane_pid "$BOT_SESSION")"
for ((i=1;i<=STABILITY_ROUNDS;i++)); do
  sleep "$STABILITY_INTERVAL"
  has "$WATCH_SESSION" && has "$BOT_SESSION" || fail "session vanished round=$i"
  [[ "$(pane_pid "$WATCH_SESSION")" == "$watch_before" ]] || fail "watchdog pane changed round=$i"
  [[ "$(pane_pid "$BOT_SESSION")" == "$bot_before" ]] || fail "bot pane changed round=$i"
  [[ "$(bot_lock_state)" == HELD ]] || fail "lock not held round=$i"
  [[ "$(session_count)" -ge 1 ]] || fail "persistent session missing round=$i"
  health_ok || fail "health failed round=$i"
  printf '[CHECK %02d/%02d] bot=%s watchdog=%s session=PASS lock=HELD health=PASS\n' "$i" "$STABILITY_ROUNDS" "$bot_before" "$watch_before"
done
pass "PRE_STABILITY=$STABILITY_ROUNDS/$STABILITY_ROUNDS"

section '3. CONTROLLED BOT SESSION RECOVERY'
pre_sessions="$(session_files)"
old_bot="$bot_before"
tmux kill-session -t "$BOT_SESSION" || fail 'controlled bot tmux kill failed'
wait_session "$BOT_SESSION" "$RECOVERY_TIMEOUT" || fail 'watchdog did not recreate bot tmux'
new_bot="$(pane_pid "$BOT_SESSION" || true)"
[[ "$new_bot" =~ ^[0-9]+$ && "$new_bot" != "$old_bot" ]] || fail "bot pane not rotated old=$old_bot new=$new_bot"
wait_ready "$RECOVERY_TIMEOUT" || fail 'recovered bot did not reach Bot Started!/ONLINE'
recovered="$(capture "$BOT_SESSION")"
! grep -Eq 'TELEGRAM_BOT_START_FLOOD_WAIT|FLOOD_WAIT|ImportBotAuthorization' <<<"$recovered" || fail 'recovered boot re-authorized Telegram instead of using persistent session'
[[ "$(session_count)" -ge 1 ]] || fail 'persistent session disappeared after bot recovery'
[[ "$(bot_lock_state)" == HELD ]] || fail 'lock not HELD after bot recovery'
health_ok || fail 'health failed after bot recovery'
post_sessions="$(session_files)"
[[ -n "$pre_sessions" && -n "$post_sessions" ]] || fail 'session metadata unavailable around recovery'
pass "BOT_SESSION_RECOVERY=$old_bot->$new_bot persistent-session=PASS no-FloodWait=PASS"

section '4. CONTROLLED SUPERVISOR RECOVERY'
watch_before="$(pane_pid "$WATCH_SESSION")"
bot_before_sup="$(pane_pid "$BOT_SESSION")"
old_sup="$(supervisor_pid || true)"
[[ "$old_sup" =~ ^[0-9]+$ ]] || fail 'cannot safely identify current supervisor PID'
kill -TERM "$old_sup" || fail "cannot signal supervisor pid=$old_sup"
new_sup="$(wait_new_supervisor "$old_sup" "$SUPERVISOR_TIMEOUT" || true)"
[[ "$new_sup" =~ ^[0-9]+$ && "$new_sup" != "$old_sup" ]] || fail "supervisor did not respawn old=$old_sup new=${new_sup:-NONE}"
[[ "$(pane_pid "$WATCH_SESSION" || true)" == "$watch_before" ]] || fail 'watchdog wrapper pane changed during supervisor recovery'
[[ "$(pane_pid "$BOT_SESSION" || true)" == "$bot_before_sup" ]] || fail 'healthy bot restarted when only supervisor was killed'
ready || fail 'bot not ONLINE after supervisor recovery'
health_ok || fail 'health failed after supervisor recovery'
pass "SUPERVISOR_RECOVERY=$old_sup->$new_sup bot_unchanged=$bot_before_sup"

section '5. FINAL 10-ROUND STABILITY'
bot_final="$(pane_pid "$BOT_SESSION")"
watch_final="$(pane_pid "$WATCH_SESSION")"
for ((i=1;i<=STABILITY_ROUNDS;i++)); do
  sleep "$STABILITY_INTERVAL"
  has "$WATCH_SESSION" && has "$BOT_SESSION" || fail "final session missing round=$i"
  [[ "$(pane_pid "$WATCH_SESSION")" == "$watch_final" ]] || fail "final watchdog pane changed round=$i"
  [[ "$(pane_pid "$BOT_SESSION")" == "$bot_final" ]] || fail "final bot pane changed round=$i"
  [[ "$(supervisor_pid || true)" == "$new_sup" ]] || fail "supervisor changed again round=$i"
  [[ "$(bot_lock_state)" == HELD ]] || fail "final lock not held round=$i"
  [[ "$(session_count)" -ge 1 ]] || fail "final persistent session missing round=$i"
  health_ok || fail "final health failed round=$i"
  printf '[CHECK %02d/%02d] bot=%s supervisor=%s session=PASS lock=HELD health=PASS\n' "$i" "$STABILITY_ROUNDS" "$bot_final" "$new_sup"
done
pass "POST_STABILITY=$STABILITY_ROUNDS/$STABILITY_ROUNDS"

section '6. FINAL AUDIT'
wrapper_count="$(pgrep -af '[a]tri-v150-production-watchdog\.sh' 2>/dev/null | awk 'NF{n++} END{print n+0}')"
[[ "$wrapper_count" == 1 ]] || fail "V150 wrapper count=$wrapper_count"
! tmux list-panes -a -F '#{pane_start_command}' 2>/dev/null | grep -q 'atri-v1681r9' || fail 'stale R9 tmux command still active'
ready || fail 'final bot not ONLINE'
[[ "$(bot_lock_state)" == HELD ]] || fail 'final lock not HELD'
[[ "$(session_count)" -ge 1 ]] || fail 'final persistent session missing'
health_ok || fail 'final health failed'
collect success
bundle
pass 'FINAL_AUDIT single-wrapper + exact-main bot + persistent-session + lock + health'
echo "MAIN_SHA=$head"
echo 'ATRI_V1674_LIVE_RECOVERY=PASS'
echo "REPORT=$REPORT"
echo "BUNDLE=$BUNDLE"
