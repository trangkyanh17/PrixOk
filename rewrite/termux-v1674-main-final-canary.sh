#!/usr/bin/env bash
set -Eeuo pipefail

# V167.4 final production canary.
# Run from the Debian /app checkout. It validates exact origin/main, stages only
# the two V150 host wrappers changed by main, then exercises real boot/recovery.
# It never git-pulls/resets the live tree and never deletes a bot lock.

EXPECTED_BRANCH="main"
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HOST_PREFIX="${ATRI_TERMUX_PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${ATRI_TERMUX_HOME:-/data/data/com.termux/files/home}"
HOST_BASH="$HOST_PREFIX/bin/bash"
HOST_PATH="$HOST_PREFIX/bin:/system/bin:/system/xbin"
MAIN_WATCHDOG="$ROOT_DIR/rewrite/termux-v150-production-watchdog.sh"
MAIN_BOT_WRAPPER="$ROOT_DIR/rewrite/termux-v150-bot-launcher.sh"
HOST_WATCHDOG="$HOST_HOME/atri-v150-production-watchdog.sh"
HOST_BOT_WRAPPER="$HOST_HOME/.local/lib/atri-v150/prixok-bot-v150.sh"
HOST_SUPERVISOR="$HOST_HOME/.local/lib/atri-v150/atri-supervisor"
HOST_CANONICAL_LAUNCHER="$HOST_HOME/prixok-bot.sh"
STARTUP_TIMEOUT="${ATRI_V1674_STARTUP_TIMEOUT:-1200}"
RECOVERY_TIMEOUT="${ATRI_V1674_RECOVERY_TIMEOUT:-240}"
STABILITY_ROUNDS="${ATRI_V1674_TEST_ROUNDS:-10}"
STABILITY_INTERVAL="${ATRI_V1674_TEST_INTERVAL:-6}"
TEST_ID="$(date +%Y%m%d-%H%M%S)-$$"
STATE_DIR="$HOST_HOME/.local/state/atri-v1674-main-canary"
BACKUP_DIR="$STATE_DIR/backups/$TEST_ID"
BOT_CAPTURE="$STATE_DIR/bot-$TEST_ID.log"
WATCH_CAPTURE="$STATE_DIR/watchdog-$TEST_ID.log"
LIVE_STAGE_STARTED=0

positive_int() { [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]; }

if [[ "${1:-}" == "--self-test" ]]; then
  [[ "$EXPECTED_BRANCH" == "main" ]]
  positive_int "$STARTUP_TIMEOUT"; positive_int "$RECOVERY_TIMEOUT"
  positive_int "$STABILITY_ROUNDS"; positive_int "$STABILITY_INTERVAL"
  grep -q 'EXPECTED_BRANCH="main"' "$0"
  grep -q 'ATRI_V1674_MAIN_FINAL_CANARY=PASS' "$0"
  grep -q 'TELEGRAM_BOT_START_FLOOD_WAIT' "$0"
  grep -q 'BOT_SESSION_RECOVERY=PASS' "$0"
  grep -q 'SUPERVISOR_RECOVERY=PASS' "$0"
  if grep -Eq 'git[[:space:]]+(pull|reset|checkout|clean)' "$0"; then
    echo "v1674 main canary self-test: FAIL (live source mutation command found)" >&2; exit 1
  fi
  if grep -Eq 'rm[[:space:]].*\.atri-prixok-bot.*lock' "$0"; then
    echo "v1674 main canary self-test: FAIL (bot lock deletion found)" >&2; exit 1
  fi
  echo "v1674 main canary self-test: PASS"; exit 0
fi
(($# == 0)) || { echo "Usage: bash rewrite/termux-v1674-main-final-canary.sh [--self-test]" >&2; exit 2; }
for value in "$STARTUP_TIMEOUT" "$RECOVERY_TIMEOUT" "$STABILITY_ROUNDS" "$STABILITY_INTERVAL"; do positive_int "$value" || exit 2; done

choose_report_dir() {
  local d
  for d in "$HOST_HOME/storage/downloads" /storage/emulated/0/Download /sdcard/Download; do
    [[ -d "$d" && -w "$d" ]] && { printf '%s\n' "$d"; return; }
  done
  printf '%s\n' "$ROOT_DIR"
}
REPORT_DIR="$(choose_report_dir)"
REPORT="$REPORT_DIR/atri-v1674-main-final-canary-$(date +%Y%m%d-%H%M%S).txt"
mkdir -p "$REPORT_DIR" "$STATE_DIR"; touch "$REPORT"; exec > >(tee -a "$REPORT") 2>&1
section(){ printf '\n===== %s =====\n' "$1"; }; info(){ printf '[INFO] %s\n' "$*"; }; warn(){ printf '[WARN] %s\n' "$*"; }; pass(){ printf '[PASS] %-26s %s\n' "$1" "${2:-}"; }
host_run(){ HOME="$HOST_HOME" PREFIX="$HOST_PREFIX" TMPDIR="$HOST_PREFIX/tmp" PATH="$HOST_PATH" LD_LIBRARY_PATH="$HOST_PREFIX/lib" "$HOST_BASH" --noprofile --norc -c "$1"; }
tmux_has(){ host_run "tmux has-session -t '$1' 2>/dev/null"; }
tmux_pane_pid(){ host_run "tmux list-panes -t '$1' -F '#{pane_pid}' 2>/dev/null | head -n1" | tr -d '\r'; }
capture_bot(){ host_run "tmux capture-pane -p -S -3000 -t prixok-bot 2>/dev/null || true"; }
capture_watchdog(){ host_run "tmux capture-pane -p -S -3000 -t atri-v150-watchdog 2>/dev/null || true"; }

snapshot_all(){
  section "RUNTIME SNAPSHOT"; date || true
  git -C "$ROOT_DIR" status --short --branch 2>&1 || true
  host_run "tmux list-panes -a -F 'session=#{session_name} pid=#{pane_pid} dead=#{pane_dead} cmd=#{pane_current_command} start=#{pane_start_command}' 2>/dev/null || true" || true
  echo "--- watchdog pane ---"; capture_watchdog | tee "$WATCH_CAPTURE" || true
  echo "--- bot pane ---"; capture_bot | tee "$BOT_CAPTURE" || true
  echo "--- memory ---"; host_run "free -h 2>/dev/null || true; grep -E 'MemAvailable|SwapTotal|SwapFree' /proc/meminfo 2>/dev/null || true" || true
}
fatal(){ printf '[FAIL] %-26s %s\n' "$1" "${2:-}" >&2; snapshot_all || true; echo "ATRI_V1674_MAIN_FINAL_CANARY=FAIL"; echo "REPORT=$REPORT"; exit 1; }

wait_session(){ local name="$1" deadline=$((SECONDS+$2)); while ((SECONDS<deadline)); do tmux_has "$name" && return 0; sleep 2; done; return 1; }
wait_bot_ready(){
  local deadline=$((SECONDS+$1)) last_notice=0 pane flood
  while ((SECONDS<deadline)); do
    if tmux_has prixok-bot; then
      pane="$(capture_bot)"; printf '%s\n' "$pane" >"$BOT_CAPTURE"
      grep -q 'Bot Started!' <<<"$pane" && grep -q 'ATRI_PRODUCTION_WORKER_V133_ONLINE' <<<"$pane" && return 0
      flood="$(grep -E 'TELEGRAM_BOT_START_FLOOD_WAIT|FloodWait|FLOOD_WAIT|ImportBotAuthorization' <<<"$pane" | tail -n1 || true)"
      if ((SECONDS-last_notice>=15)); then
        [[ -n "$flood" ]] && info "waiting READY; Telegram rate-limit held in-process: $flood" || info "waiting READY; elapsed=${SECONDS}s"
        last_notice=$SECONDS
      fi
    fi
    sleep 2
  done
  return 1
}

bot_lock_state(){
  local p="/app/.atri-prixok-bot-v133.lock"
  [[ -e "$p" ]] || { echo MISSING; return 0; }
  exec 9<>"$p"
  if flock -n 9; then flock -u 9; echo FREE; else echo HELD; fi
}
wait_lock_released(){ local deadline=$((SECONDS+$1)) s; while ((SECONDS<deadline)); do s="$(bot_lock_state || true)"; [[ "$s" == FREE || "$s" == MISSING ]] && return 0; sleep 1; done; return 1; }
legacy_watchdog_pids(){ host_run "pgrep -af '[a]tri-production-watchdog\.sh' 2>/dev/null || true" | awk 'NF{print $1}' | sort -n -u; }
v150_wrapper_pids(){ host_run "pgrep -af '[a]tri-v150-production-watchdog\.sh' 2>/dev/null || true" | awk 'NF{print $1}' | sort -n -u; }
stop_existing_v150(){
  local pid pids
  host_run "tmux kill-session -t atri-v150-watchdog 2>/dev/null || true"
  pids="$(v150_wrapper_pids | tr '\n' ' ')"; for pid in $pids; do [[ "$pid" =~ ^[0-9]+$ ]] && host_run "kill -TERM '$pid' 2>/dev/null || true"; done
  for _ in $(seq 1 20); do [[ -z "$(v150_wrapper_pids)" ]] && return 0; sleep 1; done; return 1
}
watchdog_child_pid(){
  local wrapper child args; wrapper="$(tmux_pane_pid atri-v150-watchdog || true)"; [[ "$wrapper" =~ ^[0-9]+$ ]] || return 1
  while read -r child; do
    [[ "$child" =~ ^[0-9]+$ ]] || continue; args="$(host_run "ps -o args= -p '$child' 2>/dev/null || true")"
    grep -q 'atri-supervisor' <<<"$args" && { printf '%s\n' "$child"; return 0; }
  done < <(host_run "pgrep -P '$wrapper' 2>/dev/null || true"); return 1
}
wait_new_supervisor(){ local old="$1" deadline=$((SECONDS+$2)) child; while ((SECONDS<deadline)); do child="$(watchdog_child_pid || true)"; [[ "$child" =~ ^[0-9]+$ && "$child" != "$old" ]] && { echo "$child"; return; }; sleep 1; done; return 1; }
count_numeric_bot_sessions(){ find /app -maxdepth 1 -type f -name '[0-9]*.session' 2>/dev/null | wc -l | tr -d ' '; }
local_health_ok(){ host_run "test -x '$HOST_HOME/atri-production-local-health.sh' && '$HOST_HOME/atri-production-local-health.sh' --quiet >/dev/null 2>&1"; }
restore_if_not_live(){ ((LIVE_STAGE_STARTED==0)) || return 0; [[ -d "$BACKUP_DIR" ]] || return 0; [[ -f "$BACKUP_DIR/atri-v150-production-watchdog.sh" ]] && { cp -f "$BACKUP_DIR/atri-v150-production-watchdog.sh" "$HOST_WATCHDOG"; chmod 700 "$HOST_WATCHDOG"; }; [[ -f "$BACKUP_DIR/prixok-bot-v150.sh" ]] && { cp -f "$BACKUP_DIR/prixok-bot-v150.sh" "$HOST_BOT_WRAPPER"; chmod 700 "$HOST_BOT_WRAPPER"; }; }
trap 'rc=$?; ((rc==0)) || restore_if_not_live || true' EXIT

section "ATRI V167.4 MAIN FINAL CANARY"
echo "START=$(date)"; echo "REPORT=$REPORT"; echo "MODE=real-production-canary"; echo "FAIL_POLICY=keep-current-main-runtime-on-live-test-failure"; echo "NOTE=live source is read-only; bot lock is never deleted"

section "1. EXACT MAIN"
[[ -f /etc/debian_version ]] || fatal MAIN_SYNC "must run inside Debian PRoot"
[[ "$ROOT_DIR" == "/app" ]] || fatal MAIN_SYNC "expected repository root=/app got=$ROOT_DIR"
cd "$ROOT_DIR"; git fetch --quiet origin main || fatal MAIN_SYNC "git fetch origin main failed"
branch="$(git branch --show-current 2>/dev/null || true)"; head="$(git rev-parse HEAD 2>/dev/null || true)"; origin_main="$(git rev-parse origin/main 2>/dev/null || true)"
[[ "$branch" == "$EXPECTED_BRANCH" ]] || fatal MAIN_SYNC "expected main got=${branch:-unknown}"
[[ "$head" =~ ^[0-9a-f]{40}$ && "$head" == "$origin_main" ]] || fatal MAIN_SYNC "local=$head origin/main=$origin_main"
git diff --quiet && git diff --cached --quiet || fatal MAIN_SYNC "tracked working tree is dirty"
pass MAIN_SYNC "branch=main sha=$head"; echo MAIN_SYNC=PASS

section "2. V167.4 SOURCE CONTRACT"
bot_block="$(python3 - <<'PY'
from pathlib import Path
s=Path('bot/core/telegram_manager.py').read_text(); a=s.index('async def start_bot'); b=s.index('async def start_user',a); print(s[a:b])
PY
)"
grep -q 'workdir="/app"' <<<"$bot_block" || fatal SOURCE_CONTRACT "workdir"
grep -q 'in_memory=False' <<<"$bot_block" || fatal SOURCE_CONTRACT "persistent session missing"
! grep -q 'in_memory=True' <<<"$bot_block" || fatal SOURCE_CONTRACT "in-memory session present"
grep -q 'await start_bot_client(cls.bot, LOGGER)' <<<"$bot_block" || fatal SOURCE_CONTRACT "guarded startup missing"
grep -q 'except FloodWait as exc' bot/core/telegram_startup.py || fatal SOURCE_CONTRACT "FloodWait handler missing"
grep -q 'SUPERVISOR_RESTART_BACKOFF' "$MAIN_WATCHDOG" || fatal SOURCE_CONTRACT "outer restart missing"
grep -q 'ATRI_V150_WRAPPER_ALREADY_RUNNING' "$MAIN_WATCHDOG" || fatal SOURCE_CONTRACT "wrapper singleton missing"
pass SOURCE_CONTRACT "persistent-session + FloodWait + outer-watchdog"; echo SOURCE_CONTRACT=PASS

section "3. LOCAL MAIN TESTS"
PYTHON=/home/prix/PrixOk/mltbenv/bin/python; [[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
"$PYTHON" -m py_compile bot/core/telegram_manager.py bot/core/telegram_startup.py bot/modules/atri_runtime_hardening_v1671.py || fatal PY_COMPILE "failed"
if "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then "$PYTHON" -m pytest -q tests/test_atri_telegram_lifecycle_v1674.py tests/test_atri_runtime_hardening_v1671.py || fatal LOCAL_REGRESSION "failed"; else warn "pytest absent in production venv; CI owns full pytest"; fi
bash -n "$MAIN_WATCHDOG" && bash "$MAIN_WATCHDOG" --self-test || fatal WATCHDOG_SELF_TEST "failed"
pass WATCHDOG_SELF_TEST "PASS"; echo WATCHDOG_SELF_TEST=PASS

section "4. HOST PREFLIGHT"
[[ -x "$HOST_BASH" && -x "$HOST_CANONICAL_LAUNCHER" && -x "$HOST_SUPERVISOR" ]] || fatal HOST_PREFLIGHT "missing host runtime"
host_run "command -v tmux >/dev/null && command -v proot-distro >/dev/null && bash -n '$HOST_CANONICAL_LAUNCHER'" || fatal HOST_PREFLIGHT "host commands/launcher invalid"
for helper in atri-production-local-health.sh atri-production-browser-ensure.sh atri-production-network-state.sh; do host_run "test -x '$HOST_HOME/$helper' && bash -n '$HOST_HOME/$helper'" || fatal HOST_PREFLIGHT "invalid helper=$helper"; done
mapfile -t legacy < <(legacy_watchdog_pids); ((${#legacy[@]}==0)) || fatal HOST_PREFLIGHT "legacy watchdog active pids=${legacy[*]}"
pass HOST_PREFLIGHT "PASS"

section "5. STAGE MAIN WRAPPERS"
mkdir -p "$BACKUP_DIR" "$(dirname "$HOST_BOT_WRAPPER")"; [[ -f "$HOST_WATCHDOG" ]] && cp -f "$HOST_WATCHDOG" "$BACKUP_DIR/atri-v150-production-watchdog.sh"; [[ -f "$HOST_BOT_WRAPPER" ]] && cp -f "$HOST_BOT_WRAPPER" "$BACKUP_DIR/prixok-bot-v150.sh"
cp -f "$MAIN_WATCHDOG" "$HOST_WATCHDOG"; cp -f "$MAIN_BOT_WRAPPER" "$HOST_BOT_WRAPPER"; chmod 700 "$HOST_WATCHDOG" "$HOST_BOT_WRAPPER"
host_run "bash -n '$HOST_WATCHDOG' && bash -n '$HOST_BOT_WRAPPER' && '$HOST_WATCHDOG' --self-test" || fatal HOST_STAGE "failed"
pass HOST_STAGE "backup=$BACKUP_DIR"

section "6. CLEAN CUTOVER"
stop_existing_v150 || fatal CUTOVER "previous V150 wrapper would not stop"; host_run "tmux kill-session -t prixok-bot 2>/dev/null || true"; wait_lock_released 45 || fatal CUTOVER "singleton lock remained held; NOT deleted"
host_run "tmux new-session -d -s atri-v150-watchdog \"$HOST_BASH $HOST_WATCHDOG\"" || fatal CUTOVER "watchdog tmux start failed"; LIVE_STAGE_STARTED=1; wait_session atri-v150-watchdog 15 || fatal CUTOVER "watchdog disappeared"; pass CUTOVER "PASS"; echo CUTOVER=PASS

section "7. REAL BOT BOOT"
wait_session prixok-bot "$RECOVERY_TIMEOUT" || fatal BOOT "watchdog did not create bot"; boot_pane="$(tmux_pane_pid prixok-bot || true)"; [[ "$boot_pane" =~ ^[0-9]+$ ]] || fatal BOOT "pane pid missing"
wait_bot_ready "$STARTUP_TIMEOUT" || fatal BOOT "Bot Started!/ONLINE timeout; runtime left on main so FloodWait is not restart-looped"
pass BOOT "pane=$boot_pane"; echo BOOT=PASS
session_count="$(count_numeric_bot_sessions)"; [[ "$session_count" =~ ^[0-9]+$ && "$session_count" -ge 1 ]] || fatal PERSISTENT_SESSION "no numeric /app/*.session"; pass PERSISTENT_SESSION "files=$session_count"; echo PERSISTENT_SESSION=PASS
local_health_ok || fatal PROD_HEALTH "health failed"; pass PROD_HEALTH "PASS"

section "8. NO RESTART STORM"
for ((i=1;i<=STABILITY_ROUNDS;i++)); do sleep "$STABILITY_INTERVAL"; tmux_has prixok-bot || fatal NO_RESTART_STORM "bot vanished round $i"; now="$(tmux_pane_pid prixok-bot || true)"; [[ "$now" == "$boot_pane" ]] || fatal NO_RESTART_STORM "pane $boot_pane -> $now"; printf '[CHECK %02d/%02d] bot=%s\n' "$i" "$STABILITY_ROUNDS" "$now"; done
pass NO_RESTART_STORM "$STABILITY_ROUNDS/$STABILITY_ROUNDS"; echo NO_RESTART_STORM=PASS

section "9. BOT SESSION RECOVERY"
before="$(count_numeric_bot_sessions)"; old_bot="$(tmux_pane_pid prixok-bot)"; host_run "tmux kill-session -t prixok-bot"; wait_session prixok-bot "$RECOVERY_TIMEOUT" || fatal BOT_SESSION_RECOVERY "not recreated"; new_bot="$(tmux_pane_pid prixok-bot || true)"; [[ "$new_bot" =~ ^[0-9]+$ && "$new_bot" != "$old_bot" ]] || fatal BOT_SESSION_RECOVERY "pane not rotated"; wait_bot_ready "$STARTUP_TIMEOUT" || fatal BOT_SESSION_RECOVERY "recovered bot not ready"; recovered="$(capture_bot)"; ! grep -q TELEGRAM_BOT_START_FLOOD_WAIT <<<"$recovered" || fatal BOT_SESSION_RECOVERY "recovery hit FloodWait despite persistent session"; after="$(count_numeric_bot_sessions)"; [[ "$after" -ge "$before" ]] || fatal BOT_SESSION_RECOVERY "session count decreased"; local_health_ok || fatal BOT_SESSION_RECOVERY "health failed"; pass BOT_SESSION_RECOVERY "$old_bot -> $new_bot sessions=$after"; echo BOT_SESSION_RECOVERY=PASS

section "10. SUPERVISOR RECOVERY"
wrapper="$(tmux_pane_pid atri-v150-watchdog || true)"; old_sup="$(watchdog_child_pid || true)"; [[ "$wrapper" =~ ^[0-9]+$ && "$old_sup" =~ ^[0-9]+$ ]] || fatal SUPERVISOR_RECOVERY "pid identification failed"; bot_before="$(tmux_pane_pid prixok-bot)"; host_run "kill -TERM '$old_sup'" || fatal SUPERVISOR_RECOVERY "kill failed"; new_sup="$(wait_new_supervisor "$old_sup" 90 || true)"; [[ "$new_sup" =~ ^[0-9]+$ && "$new_sup" != "$old_sup" ]] || fatal SUPERVISOR_RECOVERY "not respawned"; [[ "$(tmux_pane_pid atri-v150-watchdog || true)" == "$wrapper" ]] || fatal SUPERVISOR_RECOVERY "wrapper changed"; bot_after="$(tmux_pane_pid prixok-bot || true)"; [[ "$bot_after" == "$bot_before" ]] || fatal SUPERVISOR_RECOVERY "supervisor crash unnecessarily restarted healthy bot"; pass SUPERVISOR_RECOVERY "$old_sup -> $new_sup bot=$bot_after"; echo SUPERVISOR_RECOVERY=PASS

section "11. FINAL 10-ROUND STABILITY"
for ((i=1;i<=STABILITY_ROUNDS;i++)); do sleep "$STABILITY_INTERVAL"; tmux_has atri-v150-watchdog && tmux_has prixok-bot || fatal POST_STABILITY "session missing round $i"; [[ "$(tmux_pane_pid prixok-bot || true)" == "$bot_after" ]] || fatal POST_STABILITY "bot changed round $i"; [[ "$(watchdog_child_pid || true)" == "$new_sup" ]] || fatal POST_STABILITY "supervisor changed round $i"; local_health_ok || fatal POST_STABILITY "health failed round $i"; printf '[CHECK %02d/%02d] bot=%s supervisor=%s health=PASS\n' "$i" "$STABILITY_ROUNDS" "$bot_after" "$new_sup"; done
pass POST_STABILITY "$STABILITY_ROUNDS/$STABILITY_ROUNDS"; echo POST_STABILITY=PASS

section "12. FINAL AUDIT"
mapfile -t wrappers < <(v150_wrapper_pids); ((${#wrappers[@]}==1)) || fatal FINAL_AUDIT "wrapper count=${#wrappers[@]} pids=${wrappers[*]:-none}"; [[ "$(bot_lock_state)" == HELD ]] || fatal FINAL_AUDIT "singleton lock not held"; local_health_ok || fatal FINAL_AUDIT "health failed"; pane="$(capture_bot)"; ! grep -Eq 'Traceback \(most recent call last\)|DUPLICATE_BLOCKED' <<<"$pane" || fatal FINAL_AUDIT "traceback/duplicate marker"; pass FINAL_AUDIT "single wrapper + bot + held lock + healthy"
snapshot_all
echo "MAIN_SHA=$head"; echo ATRI_V1674_MAIN_FINAL_CANARY=PASS; echo "REPORT=$REPORT"
