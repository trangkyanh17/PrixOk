#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

# Retired compatibility entry point. V156.4 depended on a guessed physical
# rootfs layout and root-visible argv scans. The final lifecycle transaction
# supersedes it with live PRoot identity and kernel-flock ownership evidence.

if [[ "${1:-}" == "--self-test" ]]; then
  (($# == 1)) || exit 2
  bash -n "${BASH_SOURCE[0]}"
  echo "v156.4 V150 safety installer self-test: PASS"
  exit 0
fi

echo "V156.4 safety installer is retired; use termux-atri-final-recovery.sh" >&2
exit 64
