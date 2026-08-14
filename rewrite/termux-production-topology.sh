#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_BRANCH="rewrite/rust-go-ts-v150"
HOST_PREFIX="${ATRI_TERMUX_PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${ATRI_TERMUX_HOME:-/data/data/com.termux/files/home}"
HOST_BASH="$HOST_PREFIX/bin/bash"
HOST_PATH="$HOST_PREFIX/bin:/system/bin:/system/xbin"
TEST_ID="$(date +%Y%m%d-%H%M%S)-$$"
STATE_DIR="${TMPDIR:-/tmp}/atri-v150-production-topology-${TEST_ID}"

SELF_TEST=0
if [[ "${1:-}" == "--self-test" ]]; then
  SELF_TEST=1
  shift
fi
if (($#)); then
  echo "Usage: ./termux-production-topology.sh [--self-test]" >&2
  exit 2
fi

if ((SELF_TEST == 1)); then
  [[ "$EXPECTED_BRANCH" == "rewrite/rust-go-ts-v150" ]]
  [[ "$HOST_PREFIX" == /* ]]
  [[ "$HOST_HOME" == /* ]]
  if grep -Eq \
    'tmux[[:space:]]+(new-session|kill-session)|kill[[:space:]]+-(TERM|KILL)|browser-ensure\.sh[^[:cntrl:]]*--from-watchdog|prixok-bot\.sh[^[:cntrl:]]*(exec|bash)' \
    "$0"; then
    echo "production topology self-test: FAIL (mutating pattern found)" >&2
    exit 1
  fi
  echo "production topology self-test: PASS"
  exit 0
fi

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
REPORT="$REPORT_DIR/atri-v150-production-topology-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

host_run() {
  HOME="$HOST_HOME" \
  PREFIX="$HOST_PREFIX" \
  TMPDIR="$HOST_PREFIX/tmp" \
  PATH="$HOST_PATH" \
  LD_LIBRARY_PATH="$HOST_PREFIX/lib" \
    "$HOST_BASH" --noprofile --norc -c "$1"
}

section() {
  printf '\n===== %s =====\n' "$1"
}

kv() {
  printf '%-28s %s\n' "$1" "$2"
}

repo_ok=0
bridge_ok=0
helpers_state="UNKNOWN"
legacy_count="UNKNOWN"
bot_session="UNKNOWN"
bot_pane_pid="UNKNOWN"
lock_state="UNKNOWN"
health_state="UNKNOWN"
network_state="UNKNOWN"
app_root="UNKNOWN"
app_head="UNKNOWN"
app_branch="UNKNOWN"
topology_hint="MIXED_OR_UNKNOWN"

echo "===== ATRI V150 PRODUCTION TOPOLOGY DIAGNOSTIC ====="
echo "START: $(date)"
echo "REPORT: $REPORT"
echo "STATE: $STATE_DIR"
echo "MODE: READ-ONLY; no restart, repair, kill, tmux creation, build, or source update"

section "REWRITE CLONE"
cd "$ROOT_DIR"
branch="$(git branch --show-current 2>/dev/null || true)"
head_sha="$(git rev-parse HEAD 2>/dev/null || true)"
dirty="clean"
if ! git diff --quiet || ! git diff --cached --quiet; then
  dirty="dirty"
fi
kv "branch" "${branch:-unknown}"
kv "head" "${head_sha:-unknown}"
kv "tracked_tree" "$dirty"
if [[ "$branch" == "$EXPECTED_BRANCH" ]]; then
  repo_ok=1
fi

section "TERMUX HOST BRIDGE"
if [[ -x "$HOST_BASH" && -d "$HOST_HOME" ]] && \
   host_run 'command -v tmux >/dev/null && command -v proot-distro >/dev/null && test -d "$HOME"' >/dev/null 2>&1; then
  bridge_ok=1
  kv "bridge" "PASS"
  host_run 'printf "host_date="; date; printf "host_home=%s\n" "$HOME"; printf "prefix=%s\n" "$PREFIX"; uname -a'
else
  kv "bridge" "FAIL"
  kv "host_bash" "$HOST_BASH"
  kv "host_home" "$HOST_HOME"
fi

if ((bridge_ok == 1)); then
  section "LIVE HELPER METADATA"
  host_run '
for f in \
  prixok-bot.sh \
  atri-production-watchdog.sh \
  atri-production-local-health.sh \
  atri-production-browser-ensure.sh \
  atri-production-network-state.sh
do
  p="$HOME/$f"
  echo "--- $p"
  if [ -e "$p" ]; then
    ls -l "$p" 2>/dev/null || true
    sha256sum "$p" 2>/dev/null || true
    if [ -x "$p" ]; then echo "executable=yes"; else echo "executable=no"; fi
    if bash -n "$p" >/dev/null 2>&1; then echo "syntax=ok"; else echo "syntax=fail"; fi
  else
    echo "missing=yes"
  fi
done
'
  if host_run '
for f in \
  prixok-bot.sh \
  atri-production-watchdog.sh \
  atri-production-local-health.sh \
  atri-production-browser-ensure.sh \
  atri-production-network-state.sh
do
  p="$HOME/$f"
  test -x "$p" || exit 1
  bash -n "$p" || exit 1
done
' >/dev/null 2>&1; then
    helpers_state="PASS"
  else
    helpers_state="FAIL"
  fi

  section "HOST TMUX TOPOLOGY"
  host_run '
echo "--- tmux ls"
tmux ls 2>&1 || true
echo
echo "--- all panes"
tmux list-panes -a -F "session=#{session_name} window=#{window_index} pane=#{pane_index} pid=#{pane_pid} dead=#{pane_dead} cmd=#{pane_current_command} path=#{pane_current_path}" 2>&1 || true
'
  if host_run 'tmux has-session -t prixok-bot 2>/dev/null'; then
    bot_session="PRESENT"
    bot_pane_pid="$(host_run 'tmux list-panes -t prixok-bot -F "#{pane_pid}" 2>/dev/null | head -n1' | tr -d '\r' || true)"
    [[ "$bot_pane_pid" =~ ^[0-9]+$ ]] || bot_pane_pid="UNKNOWN"
  else
    bot_session="MISSING"
  fi

  section "LEGACY WATCHDOG"
  legacy_lines="$(host_run "pgrep -af '[a]tri-production-watchdog.sh' 2>/dev/null || true")"
  printf '%s\n' "${legacy_lines:-<none>}"
  legacy_count="$(printf '%s\n' "$legacy_lines" | awk 'NF{n++} END{print n+0}')"

  section "HOST RELEVANT PROCESSES"
  host_run '
ps -ef 2>/dev/null | grep -E \
"[a]tri-production-watchdog|[a]tri-supervisor|[p]rixok-bot|[p]root-distro|[s]tart\.sh|[b]ot\.py|[p]ython|[c]hrom(e|ium)|[X]vfb|[a]ria2|[j]downloader" \
|| true
'

  section "PRODUCTION SINGLETON LOCK"
  lock_state="$(host_run "proot-distro login debian -- bash -lc '
set -u
p=/app/.atri-prixok-bot-v133.lock
if [ ! -e \"\$p\" ]; then
  echo MISSING
  exit 0
fi
exec 9<\"\$p\"
if flock -n 9; then
  flock -u 9
  echo FREE
else
  echo HELD
fi
' 2>/dev/null" | tail -n1 | tr -d '\r' || true)"
  [[ "$lock_state" =~ ^(MISSING|FREE|HELD)$ ]] || lock_state="UNKNOWN"
  host_run "proot-distro login debian -- bash -lc '
p=/app/.atri-prixok-bot-v133.lock
if [ -e \"\$p\" ]; then
  stat \"\$p\" 2>/dev/null || ls -l \"\$p\" 2>/dev/null || true
  if command -v fuser >/dev/null 2>&1; then fuser -v \"\$p\" 2>&1 || true; fi
  if command -v lsof >/dev/null 2>&1; then lsof \"\$p\" 2>&1 || true; fi
else
  echo lock_file_missing
fi
'" || true

  section "LIVE HEALTH PROBES"
  if host_run '"$HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1'; then
    health_state="HEALTHY"
  else
    health_state="UNHEALTHY"
  fi
  if host_run 'ATRI_NETWORK_PROBE_TIMEOUT=8 "$HOME/atri-production-network-state.sh" --via-socks >/dev/null 2>&1'; then
    network_state="ONLINE"
  else
    network_state="PENDING_NONBLOCKING"
  fi
  kv "local_health" "$health_state"
  kv "network" "$network_state"
  kv "browser_ensure_executed" "NO"

  section "KNOWN PRODUCTION LOGS"
  host_run '
for f in \
  "$HOME/.atri-production-watchdog.log" \
  "$HOME/.atri-production-launcher.log"
do
  echo "--- $f"
  if [ -f "$f" ]; then
    ls -lh "$f" 2>/dev/null || true
    tail -n 160 "$f" 2>/dev/null || true
  else
    echo "<missing>"
  fi
done
echo
echo "--- other .atri*.log files (metadata only)"
find "$HOME" -maxdepth 1 -type f -name ".atri*.log" -exec ls -lh {} \; 2>/dev/null || true
'

  section "HOST MEMORY"
  host_run 'free -h 2>/dev/null || head -40 /proc/meminfo' || true
fi

section "DEBIAN /APP"
app_root="$(readlink -f /app 2>/dev/null || true)"
[[ -n "$app_root" ]] || app_root="UNKNOWN"
app_head="$(git -C /app rev-parse HEAD 2>/dev/null || true)"
[[ -n "$app_head" ]] || app_head="UNKNOWN"
app_branch="$(git -C /app branch --show-current 2>/dev/null || true)"
[[ -n "$app_branch" ]] || app_branch="UNKNOWN"
kv "app_root" "$app_root"
kv "app_branch" "$app_branch"
kv "app_head" "$app_head"
echo "--- git status"
git -C /app status --short --branch 2>&1 | head -100 || true

section "DEBIAN RELEVANT PROCESSES"
ps -eo pid,ppid,user,etime,%cpu,%mem,rss,comm,args 2>/dev/null | \
  grep -E '[a]tri-supervisor|[s]tart\.sh|[b]ot\.py|[p]ython|[u]vx|[s]erena|[s]emgrep|[p]yright|[c]hrom(e|ium)|[X]vfb|[a]ria2|[j]downloader' || true

section "DEBIAN TOP RSS"
ps -eo pid,ppid,%cpu,%mem,rss,etime,comm,args --sort=-rss 2>/dev/null | head -40 || true

section "DEBIAN RECENT LOG FILES"
find /app -maxdepth 3 -type f -name '*.log' -printf '%TY-%Tm-%Td %TH:%TM:%TS %10s %p\n' 2>/dev/null | \
  sort | tail -60 || true

section "DEBIAN MEMORY"
free -h 2>/dev/null || head -40 /proc/meminfo || true

if ((bridge_ok == 1)); then
  if [[ "$legacy_count" == "0" && "$bot_session" == "MISSING" && "$lock_state" != "HELD" && "$health_state" == "UNHEALTHY" ]]; then
    topology_hint="PRODUCTION_RUNTIME_APPEARS_STOPPED"
  elif [[ "$bot_session" == "MISSING" && "$lock_state" == "HELD" ]]; then
    topology_hint="WORKER_ACTIVE_OUTSIDE_TMUX"
  elif [[ "$bot_session" == "PRESENT" && "$lock_state" != "HELD" ]]; then
    topology_hint="TMUX_PRESENT_WITHOUT_SINGLETON_OWNER"
  elif [[ "$lock_state" == "HELD" && "$health_state" == "UNHEALTHY" ]]; then
    topology_hint="WORKER_ACTIVE_SHARED_HEALTH_UNHEALTHY"
  elif [[ "$legacy_count" == "1" && "$bot_session" == "PRESENT" && "$lock_state" == "HELD" && "$health_state" == "HEALTHY" ]]; then
    topology_hint="EXPECTED_LEGACY_PRODUCTION_TOPOLOGY"
  else
    topology_hint="MIXED_OR_UNKNOWN"
  fi
fi

section "DIAGNOSTIC SUMMARY"
kv "REPO_BRANCH_OK" "$repo_ok"
kv "HOST_BRIDGE_OK" "$bridge_ok"
kv "HOST_HELPERS" "$helpers_state"
kv "LEGACY_WATCHDOG_COUNT" "$legacy_count"
kv "BOT_SESSION" "$bot_session"
kv "BOT_PANE_PID" "$bot_pane_pid"
kv "BOT_LOCK" "$lock_state"
kv "LOCAL_HEALTH" "$health_state"
kv "NETWORK" "$network_state"
kv "APP_ROOT" "$app_root"
kv "APP_BRANCH" "$app_branch"
kv "APP_HEAD" "$app_head"
kv "TOPOLOGY_HINT" "$topology_hint"

if ((repo_ok == 1 && bridge_ok == 1)); then
  kv "DIAGNOSTIC_COMPLETE" "PASS"
  exit_code=0
else
  kv "DIAGNOSTIC_COMPLETE" "FAIL"
  exit_code=1
fi

echo
echo "END: $(date)"
echo "REPORT: $REPORT"
exit "$exit_code"
