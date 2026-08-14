#!/data/data/com.termux/files/usr/bin/bash
# Canonical Termux production launcher for the V150-owned runtime.
# Watchdog/repair ownership belongs to V150; this launcher only enters Debian
# and starts the production worker. Machine-specific credentials stay outside Git.
set -Eeuo pipefail

SHADOW_ENABLED="${ATRI_V150_TELEGRAM_SHADOW:-false}"
SHADOW_ADDR="${ATRI_TELEGRAM_SHADOW_ADDR:-127.0.0.1:18750}"
SHADOW_SECRET="${ATRI_TELEGRAM_SHADOW_SECRET:-}"

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