#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

ACTION="${1:-status}"
EXPECTED_BRANCH="main"
DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOST_HOME="${HOME:-/data/data/com.termux/files/home}"
V150_BIN="$HOST_HOME/.local/lib/atri-v150/atri-supervisor"
BOOT_HOOK="$HOST_HOME/.termux/boot/20-atri-v150-production.sh"
STATE_DIR="$HOST_HOME/.local/state/atri-v1564-v150-safety"
BACKUP_FILE="$STATE_DIR/boot-hook.before"
BACKUP_SHA_FILE="$STATE_DIR/boot-hook.before.sha256"
INSTALLED_SHA_FILE="$STATE_DIR/boot-hook.installed.sha256"
ROOTFS_DIR=""
CANONICAL_HOOK=""
ROOT_PROBE=""
REPORT=""

usage() {
  echo "Usage: $0 <status|install|rollback|--self-test>"
}

if [[ "$ACTION" == "--self-test" ]]; then
  [[ "$EXPECTED_BRANCH" == main ]]
  grep -q 'ATRI_V150_ROOT_OWNER_GUARD_V1564' "$(dirname "$0")/termux-v150-boot-hook.sh"
  grep -q 'v1564_root_proc_probe.py' "$0"
  grep -q 'ROLLBACK_STALE_TARGET' "$0"
  ! grep -Eq 'tmux[[:space:]]+kill|kill[[:space:]]+-|rm[[:space:]]+-rf[[:space:]]+/app|git[[:space:]]+(pull|reset|checkout|clean)' "$0"
  bash -n "$0" "$(dirname "$0")/termux-v150-boot-hook.sh"
  echo "v156.4 V150 safety installer self-test: PASS"
  exit 0
fi

case "$ACTION" in
  status|install|rollback) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

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

root_probe() {
  local command arg
  command="PATH=$HOST_PREFIX/bin:/system/bin:/system/xbin LD_LIBRARY_PATH=$HOST_PREFIX/lib $HOST_PREFIX/bin/python3 $ROOT_PROBE"
  for arg in "$@"; do
    printf -v arg '%q' "$arg"
    command+=" $arg"
  done
  su -c "$command"
}

choose_report_dir() {
  local d
  for d in /storage/emulated/0/Download /sdcard/Download; do
    [[ -d "$d" && -w "$d" ]] && { printf '%s\n' "$d"; return 0; }
  done
  printf '%s\n' "$STATE_DIR"
}

mkdir -p "$STATE_DIR"
REPORT="$(choose_report_dir)/atri-v1564-v150-safety-${ACTION}-$(date +%Y%m%d-%H%M%S).txt"
touch "$REPORT"
exec > >(tee -a "$REPORT") 2>&1

pass() { printf '[PASS] %-22s %s\n' "$1" "$2"; }
fail() { printf '[FAIL] %-22s %s\n' "$1" "$2" >&2; return 1; }

finish() {
  local rc=$?
  echo "END: $(date)"
  echo "REPORT: $REPORT"
  return "$rc"
}
trap finish EXIT

echo "===== ATRI V156.4 V150 ROOT-SAFETY ====="
echo "ACTION=$ACTION"
echo "START=$(date)"

if [[ "$HOST_PREFIX" != "/data/data/com.termux/files/usr" || -f /etc/debian_version ]]; then
  fail HOST_CONTEXT "must run from Termux host"; exit 1
fi
for cmd in proot-distro tmux su python3 sha256sum install mv; do
  command -v "$cmd" >/dev/null 2>&1 || { fail HOST_CONTEXT "$cmd missing"; exit 1; }
done
ROOTFS_DIR="$(find_rootfs || true)"
[[ -n "$ROOTFS_DIR" ]] || { fail HOST_CONTEXT "Debian clone/rootfs missing"; exit 1; }
CANONICAL_HOOK="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/termux-v150-boot-hook.sh"
ROOT_PROBE="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/v1564_root_proc_probe.py"
[[ -f "$CANONICAL_HOOK" && -f "$ROOT_PROBE" ]] || { fail HOST_CONTEXT "V156.4 canonical files missing"; exit 1; }
[[ "$(su -c id -u 2>/dev/null | tail -n1 | tr -d '\r')" == 0 ]] || { fail HOST_CONTEXT "KernelSU root unavailable"; exit 1; }
pass HOST_CONTEXT "rootfs=$ROOTFS_DIR root=READY"

meta="$(debian_run "cd '$DEBIAN_CLONE' && printf 'branch=%s\\n' \"\$(git branch --show-current)\" && printf 'head=%s\\n' \"\$(git rev-parse HEAD)\" && printf 'origin=%s\\n' \"\$(git rev-parse refs/remotes/origin/main)\" && if [ -z \"\$(git status --porcelain=v1 --untracked-files=all)\" ]; then echo clean=1; else echo clean=0; fi" 2>/dev/null || true)"
printf '%s\n' "$meta"
branch="$(awk -F= '$1=="branch"{print $2}' <<<"$meta")"
head="$(awk -F= '$1=="head"{print $2}' <<<"$meta")"
origin="$(awk -F= '$1=="origin"{print $2}' <<<"$meta")"
clean="$(awk -F= '$1=="clean"{print $2}' <<<"$meta")"
[[ "$branch" == main && "$head" =~ ^[0-9a-f]{40}$ && "$head" == "$origin" && "$clean" == 1 ]] || { fail REPO "branch=$branch head=$head origin=$origin clean=$clean"; exit 1; }
pass REPO "head=$head clean=1"

bash -n "$CANONICAL_HOOK"
grep -q 'ATRI_V150_ROOT_OWNER_GUARD_V1564' "$CANONICAL_HOOK"
canonical_sha="$(sha256sum "$CANONICAL_HOOK" | awk '{print $1}')"
pass SOURCE "canonical_sha=$canonical_sha"

legacy="$(root_probe list-legacy 2>/dev/null || { fail ROOT_PROC "legacy probe failed"; exit 1; })"
v150="$(root_probe list-v150 --v150-bin "$V150_BIN" 2>/dev/null || { fail ROOT_PROC "V150 probe failed"; exit 1; })"
legacy_count="$(awk 'NF{n++} END{print n+0}' <<<"$legacy")"
v150_count="$(awk 'NF{n++} END{print n+0}' <<<"$v150")"
[[ "$legacy_count" == 0 && "$v150_count" == 1 ]] || { fail ROOT_PROC "legacy=${legacy:-none} v150=${v150:-none}"; exit 1; }

session=MISSING
pane=""
if tmux has-session -t prixok-bot 2>/dev/null; then
  session=PRESENT
  pane="$(tmux list-panes -t prixok-bot -F '#{pane_pid}' 2>/dev/null | head -n1)"
fi
recorded="$(debian_run "head -n1 /app/.atri-prixok-bot-v133.lock 2>/dev/null || true" 2>/dev/null | tail -n1 | tr -d '\r')"
botpid=""
if [[ "$pane" =~ ^[0-9]+$ && "$recorded" =~ ^[0-9]+$ ]]; then
  botpid="$(root_probe bot-pid --pane-pid "$pane" --recorded-pid "$recorded" 2>/dev/null || true)"
fi
health=UNHEALTHY
[[ -x "$HOST_HOME/atri-production-local-health.sh" ]] && "$HOST_HOME/atri-production-local-health.sh" --quiet >/dev/null 2>&1 && health=HEALTHY
[[ "$session" == PRESENT && "$botpid" =~ ^[0-9]+$ && "$health" == HEALTHY ]] || { fail PRODUCTION "session=$session pane=${pane:-unknown} bot=${botpid:-unknown} health=$health"; exit 1; }
pass PRODUCTION "v150=$v150 legacy=0 pane=$pane bot=$botpid health=HEALTHY"

if [[ "$ACTION" == status ]]; then
  if [[ -f "$BOOT_HOOK" ]]; then
    live_sha="$(sha256sum "$BOOT_HOOK" | awk '{print $1}')"
    if [[ "$live_sha" == "$canonical_sha" ]]; then
      pass BOOT_HOOK "V156.4 root-owner guard installed"
    else
      fail BOOT_HOOK "upgrade required live_sha=$live_sha canonical_sha=$canonical_sha"
      exit 1
    fi
  else
    fail BOOT_HOOK "missing $BOOT_HOOK"; exit 1
  fi
  exit 0
fi

if [[ "$ACTION" == install ]]; then
  mkdir -p "$(dirname "$BOOT_HOOK")"
  if [[ -f "$BOOT_HOOK" ]]; then
    cp -p "$BOOT_HOOK" "$BACKUP_FILE"
    sha256sum "$BOOT_HOOK" | awk '{print $1}' >"$BACKUP_SHA_FILE"
  else
    : >"$BACKUP_FILE"
    echo MISSING >"$BACKUP_SHA_FILE"
  fi
  tmp="$BOOT_HOOK.v1564.tmp.$$"
  install -m 700 "$CANONICAL_HOOK" "$tmp"
  mv -f "$tmp" "$BOOT_HOOK"
  live_sha="$(sha256sum "$BOOT_HOOK" | awk '{print $1}')"
  [[ "$live_sha" == "$canonical_sha" ]] || { fail INSTALL "hash mismatch"; exit 1; }
  printf '%s\n' "$live_sha" >"$INSTALLED_SHA_FILE"
  bash -n "$BOOT_HOOK"
  grep -q 'ATRI_V150_ROOT_OWNER_GUARD_V1564' "$BOOT_HOOK"
  pass INSTALL "boot hook upgraded without runtime restart sha=$live_sha"
  exit 0
fi

[[ -f "$INSTALLED_SHA_FILE" && -f "$BACKUP_SHA_FILE" && -f "$BACKUP_FILE" ]] || { fail ROLLBACK "backup metadata missing"; exit 1; }
expected_installed="$(cat "$INSTALLED_SHA_FILE")"
current_sha="$(sha256sum "$BOOT_HOOK" 2>/dev/null | awk '{print $1}')"
[[ "$current_sha" == "$expected_installed" ]] || { fail ROLLBACK_STALE_TARGET "current=$current_sha expected=$expected_installed"; exit 1; }
old_sha="$(cat "$BACKUP_SHA_FILE")"
if [[ "$old_sha" == MISSING ]]; then
  rm -f "$BOOT_HOOK"
else
  tmp="$BOOT_HOOK.rollback.tmp.$$"
  install -m 700 "$BACKUP_FILE" "$tmp"
  mv -f "$tmp" "$BOOT_HOOK"
  [[ "$(sha256sum "$BOOT_HOOK" | awk '{print $1}')" == "$old_sha" ]] || { fail ROLLBACK "restored hash mismatch"; exit 1; }
fi
pass ROLLBACK "exact pre-V156.4 boot hook restored; runtime untouched"
