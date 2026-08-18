#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Compatibility entry point. The former live harness could detach a PRoot
# worker from tmux while it still owned the production flock. Delegate to the
# sole final lifecycle transaction instead of repeating that unsafe action.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FINAL_RECOVERY="$SCRIPT_DIR/termux-atri-final-recovery.sh"

[[ -f "$FINAL_RECOVERY" ]] || {
  echo "missing final recovery script: $FINAL_RECOVERY" >&2
  exit 127
}

exec bash "$FINAL_RECOVERY" "$@"
