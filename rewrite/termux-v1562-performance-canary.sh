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

patch_canary() {
  local source="$1" patched_file="$2" line
  local patched=0 skipping=0
  : >"$patched_file"

  while IFS= read -r line || [[ -n "$line" ]]; do
    if (( skipping )); then
      if [[ "$line" == "}" ]]; then
        skipping=0
      fi
      continue
    fi

    if [[ "$line" == "production_bot_pid() {" ]]; then
      ((patched+=1))
      skipping=1
      cat >>"$patched_file" <<'EOF'
production_bot_pid() {
  local raw
  raw="$(debian_run '
set -Eeuo pipefail
lock=/app/.atri-prixok-bot-v133.lock
[[ -r "$lock" ]]
IFS= read -r pid <"$lock"
[[ "$pid" =~ ^[0-9]+$ ]]
kill -0 "$pid" 2>/dev/null
printf "%s\n" "$pid"
' 2>/dev/null | tr -d '\r')"
  [[ "$raw" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$raw"
}
EOF
      continue
    fi

    printf '%s\n' "$line" >>"$patched_file"
  done <"$source"

  if (( patched != 1 || skipping != 0 )); then
    echo "V156.2: expected exactly one production_bot_pid() function in base canary" >&2
    return 1
  fi
  if grep -q "pgrep -f '\[p\]ython3 -m bot'" "$patched_file"; then
    echo "V156.2: legacy strict PID matcher survived patch" >&2
    return 1
  fi
  if grep -q "v156_bot_pid_probe.py" "$patched_file"; then
    echo "V156.2: argv-based PID probe survived patch" >&2
    return 1
  fi
  if ! grep -q '/app/.atri-prixok-bot-v133.lock' "$patched_file"; then
    echo "V156.2: singleton lock PID source missing after patch" >&2
    return 1
  fi
  if ! grep -q 'kill -0 "$pid"' "$patched_file"; then
    echo "V156.2: PID liveness check missing after patch" >&2
    return 1
  fi
}

if [[ "${1:-}" == "--self-test" ]]; then
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  source_file="$script_dir/termux-v156-performance-canary.sh"
  [[ -f "$source_file" ]]
  temp_file="$(mktemp "${TMPDIR:-/tmp}/atri-v1562-selftest.XXXXXX.sh")"
  trap 'rm -f "$temp_file"' EXIT
  patch_canary "$source_file" "$temp_file"
  bash -n "$temp_file"
  grep -q '/app/.atri-prixok-bot-v133.lock' "$temp_file"
  grep -q 'kill -0 "$pid"' "$temp_file"
  echo "v156.2 performance canary lock-PID hotfix self-test: PASS"
  exit 0
fi

ROOTFS_DIR="$(find_rootfs || true)"
[[ -n "$ROOTFS_DIR" ]] || { echo "V156.2: Debian clone/rootfs not found" >&2; exit 1; }

SOURCE="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/termux-v156-performance-canary.sh"
[[ -f "$SOURCE" ]] || { echo "V156.2: base V156 canary missing" >&2; exit 1; }

mkdir -p "$TMP_ROOT"
PATCHED="$(mktemp "$TMP_ROOT/atri-v1562-canary.XXXXXX.sh")"
cleanup() { rm -f "$PATCHED"; }
trap cleanup EXIT INT TERM

patch_canary "$SOURCE" "$PATCHED"
chmod 700 "$PATCHED"
bash "$PATCHED" "$@"
