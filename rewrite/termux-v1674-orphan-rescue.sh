#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
BOT_SESSION="prixok-bot"
WATCH_SESSION="atri-v150-watchdog"
LOCK_REL="home/prix/PrixOk/.atri-prixok-bot-v133.lock"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
REPORT="$HOST_HOME/storage/downloads/atri-v1674-orphan-rescue-$RUN_ID.txt"
PROBE="$HOST_HOME/.local/state/atri-v1674-orphan-rescue/v156_bot_pid_probe.py"
mkdir -p "$(dirname "$REPORT")" "$(dirname "$PROBE")"
exec > >(tee -a "$REPORT") 2>&1

log(){ printf '%s %s\n' "$(date '+%F %T')" "$*"; }
fail(){ log "[FAIL] $*"; echo "REPORT=$REPORT"; exit 1; }
has(){ tmux has-session -t "$1" 2>/dev/null; }
capture(){ tmux capture-pane -p -S -2000 -t "$1" 2>/dev/null || true; }

find_lock_path(){
  local root candidate
  for root in \
    "$PREFIX/var/lib/proot-distro/containers/debian/rootfs" \
    "$PREFIX/var/lib/proot-distro/installed-rootfs/debian"; do
    [[ -d "$root" ]] || continue
    candidate="$root/$LOCK_REL"
    if [[ -e "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    candidate="$(find "$root" -maxdepth 7 -type f -name '.atri-prixok-bot-v133.lock' -path '*/PrixOk/*' -print -quit 2>/dev/null || true)"
    if [[ -n "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

lock_state_guest(){
  proot-distro login debian -- bash -lc '
set -Eeuo pipefail
p=/app/.atri-prixok-bot-v133.lock
[[ -e "$p" ]] || { echo MISSING; exit 0; }
exec 9<>"$p"
if flock -n 9; then flock -u 9; echo FREE; else echo HELD; fi
' 2>/dev/null | tail -n1 | tr -d '\r'
}

wait_guest_lock_not_held(){
  local timeout="$1"
  local deadline=$((SECONDS + timeout))
  local state
  while ((SECONDS < deadline)); do
    state="$(lock_state_guest || true)"
    [[ "$state" != HELD ]] && return 0
    sleep 1
  done
  return 1
}

wait_session(){
  local name="$1" timeout="$2"
  local deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    has "$name" && return 0
    sleep 2
  done
  return 1
}

wait_ready(){
  local timeout="$1"
  local deadline=$((SECONDS + timeout))
  local pane flood last=0
  while ((SECONDS < deadline)); do
    if has "$BOT_SESSION"; then
      pane="$(capture "$BOT_SESSION")"
      if grep -q 'Bot Started!' <<<"$pane" && grep -q 'ATRI_PRODUCTION_WORKER_V133_ONLINE' <<<"$pane"; then
        return 0
      fi
      flood="$(grep -E 'TELEGRAM_BOT_START_FLOOD_WAIT|FloodWait|FLOOD_WAIT|ImportBotAuthorization' <<<"$pane" | tail -n1 || true)"
      if ((SECONDS - last >= 15)); then
        [[ -n "$flood" ]] && log "[WAIT] $flood" || log '[WAIT] bot not ONLINE yet'
        last=$SECONDS
      fi
    fi
    sleep 2
  done
  return 1
}

if [[ "${1:-}" == "--self-test" ]]; then
  bash -n "$0"
  grep -q 'containers/debian/rootfs' "$0"
  grep -q 'installed-rootfs/debian' "$0"
  grep -q -- '--strategy lock-owner' "$0"
  grep -q 'find -L /app' "$0"
  ! grep -Eq 'rm[[:space:]].*\.atri-prixok-bot.*lock|git[[:space:]]+(reset|clean)|stash[[:space:]]+pop' "$0"
  echo 'atri v1674 orphan rescue self-test: PASS'
  exit 0
fi
(($# == 0)) || { echo "Usage: bash $0 [--self-test]" >&2; exit 2; }

log '===== ATRI V167.4 ORPHAN RESCUE ====='
command -v proot-distro >/dev/null || fail 'proot-distro missing'
command -v tmux >/dev/null || fail 'tmux missing'
command -v python >/dev/null || fail 'host python missing'
has "$WATCH_SESSION" || fail 'V150 watchdog tmux missing'

state="$(lock_state_guest || true)"
if has "$BOT_SESSION"; then
  log "[PASS] bot tmux already present lock=$state; refusing unnecessary rescue"
  wait_ready 180 || fail 'existing bot tmux is not ONLINE'
  echo 'ATRI_V1674_ORPHAN_RESCUE=PASS'
  echo "REPORT=$REPORT"
  exit 0
fi

[[ "$state" == HELD ]] || fail "expected orphan topology tmux=MISSING lock=HELD; got lock=$state"
lock_path="$(find_lock_path || true)"
[[ -n "$lock_path" && -e "$lock_path" ]] || fail 'cannot resolve physical Debian lock path from installed rootfs'
log "PHYSICAL_LOCK=$lock_path"

proot-distro login debian -- bash -lc 'cat /app/rewrite/v156_bot_pid_probe.py' >"$PROBE" || fail 'cannot copy kernel lock probe'
python -m py_compile "$PROBE" || fail 'kernel lock probe compile failed'

set +e
owner_out="$(python "$PROBE" --strategy lock-owner --proc-root /proc --lock-file "$lock_path" 2>&1)"
owner_rc=$?
set -e
printf '%s\n' "$owner_out"
((owner_rc == 0)) || fail 'host kernel lock owner could not be resolved safely'
owner="$(tail -n1 <<<"$owner_out" | tr -d '\r')"
[[ "$owner" =~ ^[0-9]+$ ]] || fail "invalid host lock owner=$owner"
kill -0 "$owner" 2>/dev/null || fail "resolved host owner not alive pid=$owner"
log "LOCK_OWNER=$owner CMD=$(tr '\0' ' ' <"/proc/$owner/cmdline" 2>/dev/null || true)"

kill -TERM "$owner" || fail "cannot TERM verified lock owner pid=$owner"
if ! wait_guest_lock_not_held 20; then
  set +e
  owner2_out="$(python "$PROBE" --strategy lock-owner --proc-root /proc --lock-file "$lock_path" 2>&1)"
  owner2_rc=$?
  set -e
  printf '%s\n' "$owner2_out"
  ((owner2_rc == 0)) || fail 'lock still HELD and owner cannot be re-resolved; refusing KILL'
  owner2="$(tail -n1 <<<"$owner2_out" | tr -d '\r')"
  [[ "$owner2" == "$owner" ]] || fail "lock owner changed $owner->$owner2; refusing KILL"
  kill -KILL "$owner2" || fail "cannot KILL verified owner pid=$owner2"
  wait_guest_lock_not_held 10 || fail 'lock still HELD after verified owner termination'
fi
log "[PASS] orphan lock released"

wait_session "$BOT_SESSION" 120 || fail 'watchdog did not recreate prixok-bot tmux'
wait_ready 300 || fail 'recreated bot did not reach Bot Started!/ONLINE'
[[ "$(lock_state_guest)" == HELD ]] || fail 'recovered bot does not hold singleton lock'
sessions="$(proot-distro login debian -- bash -lc 'find -L /app -maxdepth 1 -type f -name "[0-9]*.session" -printf "%p|%s\\n" 2>/dev/null' || true)"
[[ -n "$sessions" ]] || fail 'persistent Telegram session missing after rescue'
printf '%s\n' "$sessions"
log '[PASS] bot tmux recreated + ONLINE + persistent session + singleton lock'
echo 'ATRI_V1674_ORPHAN_RESCUE=PASS'
echo "REPORT=$REPORT"
