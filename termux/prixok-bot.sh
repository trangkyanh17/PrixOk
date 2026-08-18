#!/data/data/com.termux/files/usr/bin/bash
# Canonical Termux production launcher for the V150-owned runtime.
# Watchdog/repair ownership belongs to V150; this launcher only enters Debian
# and starts the production worker. Machine-specific credentials stay outside Git.
set -Eeuo pipefail

HOST_STATE_DIR="${ATRI_V150_HOST_STATE_DIR:-$HOME/.local/state/atri-v150}"
HOST_LOCK_PATH="$HOST_STATE_DIR/prixok-bot-host.lock"
HOST_LOCK_FD=9

host_singleton_self_test() {
  local tmp holder
  tmp="$(mktemp -d)"
  trap '[[ -n "${holder:-}" ]] && kill "$holder" 2>/dev/null || true; rm -rf "$tmp"' RETURN

  bash -c '
    set -Eeuo pipefail
    lock="$1"
    exec 9<>"$lock"
    flock -n 9
    printf "holder=%s\n" "$$" >&9
    exec sleep 2
  ' _ "$tmp/lock" &
  holder=$!

  # Give the first process a bounded moment to acquire the lock.
  for _ in {1..40}; do
    if ! (exec 8<>"$tmp/lock"; flock -n 8); then
      break
    fi
    sleep 0.025
  done

  if (exec 8<>"$tmp/lock"; flock -n 8); then
    echo "prixok host singleton self-test: FAIL (second owner acquired lock)" >&2
    return 1
  fi

  wait "$holder"
  holder=""
  if ! (exec 8<>"$tmp/lock"; flock -n 8); then
    echo "prixok host singleton self-test: FAIL (lock not released after owner exit)" >&2
    return 1
  fi
  echo "prixok host singleton self-test: PASS"
}

if [[ "${1:-}" == "--self-test" ]]; then
  (($# == 1)) || exit 2
  command -v flock >/dev/null 2>&1
  host_singleton_self_test
  exit 0
fi
(($# == 0)) || { echo "Usage: $0 [--self-test]" >&2; exit 2; }

command -v flock >/dev/null 2>&1 || {
  echo "missing required host command: flock" >&2
  exit 127
}
mkdir -p "$HOST_STATE_DIR"

# V168.6 HOST SINGLETON
# Hold one lock in the Termux host namespace for the entire PRoot/Python lifetime.
# The Python /app lock remains a second, guest-side guard, but this host lock also
# protects against stale workers living in another/replaced Debian rootfs where
# /app/.atri-prixok-bot-v133.lock could be a different inode.
exec 9<>"$HOST_LOCK_PATH"
if ! flock -n "$HOST_LOCK_FD"; then
  echo "ATRI_PRODUCTION_HOST_SINGLETON_V1686_DUPLICATE_BLOCKED host_pid=$$ lock=$HOST_LOCK_PATH" >&2
  exit 73
fi
printf '%s\n' "$$" >"$HOST_LOCK_PATH"
chmod 600 "$HOST_LOCK_PATH" 2>/dev/null || true

SHADOW_ENABLED="${ATRI_V150_TELEGRAM_SHADOW:-false}"
SHADOW_ADDR="${ATRI_TELEGRAM_SHADOW_ADDR:-127.0.0.1:18750}"
SHADOW_SECRET="${ATRI_TELEGRAM_SHADOW_SECRET:-}"

# FD 9 intentionally remains open across exec. The advisory lock is released only
# when the complete proot-distro -> start.sh -> Python worker chain exits.
exec proot-distro login debian -- env \
  ATRI_V150_TELEGRAM_SHADOW="$SHADOW_ENABLED" \
  ATRI_TELEGRAM_SHADOW_ADDR="$SHADOW_ADDR" \
  ATRI_TELEGRAM_SHADOW_SECRET="$SHADOW_SECRET" \
  bash -lc '
set -Eeuo pipefail
cd /app
export TZ=Asia/Ho_Chi_Minh
export PRIXOK_EXTERNAL_ENGINES=1
export RUN_SOURCE_UPDATE=0
exec ./start.sh
'