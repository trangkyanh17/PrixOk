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
  raw="$(debian_run "cd '$DEBIAN_CLONE' && python3 rewrite/v156_bot_pid_probe.py --strategy lock-owner --proc-root /proc --lock-file /app/.atri-prixok-bot-v133.lock" | tr -d '\r')" || return 1
  [[ "$raw" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$raw"
}
EOF
      continue
    fi

    printf '%s\n' "$line" >>"$patched_file"
  done <"$source"

  if (( patched != 1 || skipping != 0 )); then
    echo "V156.3: expected exactly one production_bot_pid() function in base canary" >&2
    return 1
  fi
  if grep -q "pgrep -f '\[p\]ython3 -m bot'" "$patched_file"; then
    echo "V156.3: legacy strict PID matcher survived patch" >&2
    return 1
  fi
  if ! grep -q -- '--strategy lock-owner' "$patched_file"; then
    echo "V156.3: kernel lock-owner strategy missing after patch" >&2
    return 1
  fi
  if ! grep -q '/app/.atri-prixok-bot-v133.lock' "$patched_file"; then
    echo "V156.3: production singleton lock path missing after patch" >&2
    return 1
  fi
}

if [[ "${1:-}" == "--self-test" ]]; then
  script_dir="$(cd "$(dirname "$0")" && pwd)"
  source_file="$script_dir/termux-v156-performance-canary.sh"
  probe_file="$script_dir/v156_bot_pid_probe.py"
  [[ -f "$source_file" && -f "$probe_file" ]]
  temp_file="$(mktemp "${TMPDIR:-/tmp}/atri-v1563-selftest.XXXXXX.sh")"
  trap 'rm -f "$temp_file"' EXIT
  patch_canary "$source_file" "$temp_file"
  bash -n "$temp_file"
  grep -q -- '--strategy lock-owner' "$temp_file"
  grep -q '/app/.atri-prixok-bot-v133.lock' "$temp_file"
  python3 "$probe_file" --help >/dev/null
  echo "v156.3 performance canary kernel-lock PID self-test: PASS"
  exit 0
fi

ROOTFS_DIR="$(find_rootfs || true)"
[[ -n "$ROOTFS_DIR" ]] || { echo "V156.3: Debian clone/rootfs not found" >&2; exit 1; }

SOURCE="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/termux-v156-performance-canary.sh"
PROBE="$ROOTFS_DIR$DEBIAN_CLONE/rewrite/v156_bot_pid_probe.py"
[[ -f "$SOURCE" ]] || { echo "V156.3: base V156 canary missing" >&2; exit 1; }
[[ -f "$PROBE" ]] || { echo "V156.3: kernel-lock PID probe missing" >&2; exit 1; }

mkdir -p "$TMP_ROOT"
PATCHED="$(mktemp "$TMP_ROOT/atri-v1563-canary.XXXXXX.sh")"
cleanup() { rm -f "$PATCHED"; }
trap cleanup EXIT INT TERM

patch_canary "$SOURCE" "$PATCHED"
chmod 700 "$PATCHED"
bash "$PATCHED" "$@"
