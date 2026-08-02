#!/usr/bin/env bash
set -Eeuo pipefail

source mltbenv/bin/activate

PERSISTENT_CONFIG="${ATRI_CONFIG_PATH:-/app/config.py}"

if [[ "$PERSISTENT_CONFIG" != "/app/config.py" ]]; then
    if [[ ! -s "$PERSISTENT_CONFIG" ]]; then
        echo "Persistent config không tồn tại: $PERSISTENT_CONFIG"
        exit 1
    fi

    cat "$PERSISTENT_CONFIG" > /app/config.py
    chmod 600 /app/config.py
fi

if [[ "${RUN_SOURCE_UPDATE:-0}" == "1" ]]; then
    python3 update.py
else
    echo "Source updater disabled for patched deployment."
fi

exec python3 -m bot
