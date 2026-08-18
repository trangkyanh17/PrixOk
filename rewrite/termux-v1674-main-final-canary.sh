#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility entry point. The former V167.4 harness used tmux teardown as
# a bot crash test and is intentionally retired. All lifecycle work is routed
# through the host-side, kernel-owner-verified final recovery transaction.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FINAL_RECOVERY="$SCRIPT_DIR/termux-atri-final-recovery.sh"

[[ -f "$FINAL_RECOVERY" ]] || {
  echo "missing final recovery script: $FINAL_RECOVERY" >&2
  exit 127
}

exec bash "$FINAL_RECOVERY" "$@"
