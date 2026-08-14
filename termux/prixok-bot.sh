#!/data/data/com.termux/files/usr/bin/bash
# Canonical Termux production launcher for the V150-owned runtime.
# Watchdog/repair ownership belongs to V150; this launcher only enters Debian
# and starts the production worker. Machine-specific credentials stay outside Git.
set -Eeuo pipefail

exec proot-distro login debian -- bash -lc '
set -Eeuo pipefail
cd /app
export TZ=Asia/Ho_Chi_Minh
export PRIXOK_EXTERNAL_ENGINES=1
export RUN_SOURCE_UPDATE=0
exec ./start.sh
'
