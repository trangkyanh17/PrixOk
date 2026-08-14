#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

HOST_BASH="/data/data/com.termux/files/usr/bin/bash"
CANONICAL_LAUNCHER="$HOME/prixok-bot.sh"

if [[ ! -x "$CANONICAL_LAUNCHER" ]]; then
  echo "missing executable: $CANONICAL_LAUNCHER" >&2
  exit 127
fi

# V150 owns watchdog lifecycle. Bypass the legacy ensure hook when V150 has to
# recreate the production tmux session, while preserving the canonical launcher
# and its RUN_SOURCE_UPDATE=0 behavior.
exec env ATRI_PRODUCTION_LAUNCHER_GUARD=1 \
  "$HOST_BASH" "$CANONICAL_LAUNCHER"
