#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

DEBIAN_CLONE="${ATRI_V150_DEBIAN_CLONE:-/opt/prixok-v150}"
HOST_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
TMP_ROOT="${TMPDIR:-${HOME:-/tmp}/.cache}"
ROOTFS_DIR=""

find_rootfs() {
  local d
  for d in \
    "$HOST_PREFIX/var/lib/proot-distro/containers/debian/rootfs" \
    "$HOST_PREFIX/var/lib/proot-distro/installed-rootfs/debian"; do
    if [[ -f "$d$DEBIAN_CLONE/rewrite/termux-v156-performance-canary.sh" ]]; then
      printf '%s\n' "$d"
      return 0
    fi
  done
  return 1
}

resolve_sources() {
  local script_dir
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  if [[ -f "$script_dir/termux-v156-performance-canary.sh" && -f "$script_dir/v1564_canary_patch.py" && -f "$script_dir/v1564_root_proc_probe.py" ]]; then
    SOURCE="$script_dir/termux-v156-performance-canary.sh"
    PATCHER="$script_dir/v1564_canary_patch.py"
    ROOT_PROBE="$script_dir/v1564_root_proc_probe.py"
    return 0
  fi
  ROOTFS_DIR="$(find_rootfs || true)"
  [[ -n "$ROOTFS_DIR" ]] || return 1
  SOURCE="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/termux-v156-performance-canary.sh"
  PATCHER="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/v1564_canary_patch.py"
  ROOT_PROBE="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/v1564_root_proc_probe.py"
  [[ -f "$SOURCE" && -f "$PATCHER" && -f "$ROOT_PROBE" ]]
}

resolve_sources || { echo "V156.4: canonical base/patcher/root probe not found" >&2; exit 1; }

mkdir -p "$TMP_ROOT"
PATCHED="$(mktemp "$TMP_ROOT/atri-v1564-canary.XXXXXX.sh")"
cleanup() { rm -f "$PATCHED"; }
trap cleanup EXIT INT TERM

python3 "$PATCHER" --source "$SOURCE" --output "$PATCHED"
bash -n "$PATCHED"
grep -q 'ATRI_V1564_ROOT_PROC' "$PATCHED"
grep -q 'root_probe list-legacy' "$PATCHED"
grep -q 'root_probe snapshot' "$PATCHED"
grep -q 'root_probe soak' "$PATCHED"
! grep -q "pgrep -f '\[p\]ython3 -m bot'" "$PATCHED"

if [[ "${1:-}" == "--self-test" ]]; then
  python3 - "$PATCHER" "$ROOT_PROBE" <<'PY'
from pathlib import Path
import sys

for raw in sys.argv[1:]:
    path = Path(raw)
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
PY
  python3 "$ROOT_PROBE" --help >/dev/null
  echo "v156.4 performance canary root-proc self-test: PASS"
  exit 0
fi

chmod 700 "$PATCHED"
exec bash "$PATCHED" "$@"
