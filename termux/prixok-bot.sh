#!/data/data/com.termux/files/usr/bin/bash
# Canonical Termux launcher. Machine-specific credentials stay outside Git.
if [ "${ATRI_PRODUCTION_LAUNCHER_GUARD:-0}" != "1" ] && \
   [ -x "$HOME/atri-production-ensure.sh" ]; then
  ATRI_PRODUCTION_LAUNCHER_GUARD=1 \
    "$HOME/atri-production-ensure.sh" \
    --from-launcher \
    >>"$HOME/.atri-production-launcher.log" 2>&1 || true
fi

set -Eeuo pipefail
exec proot-distro login debian -- bash -lc '
set -Eeuo pipefail
cd /app
export TZ=Asia/Ho_Chi_Minh
export PRIXOK_EXTERNAL_ENGINES=1
export RUN_SOURCE_UPDATE=0
exec ./start.sh
'
