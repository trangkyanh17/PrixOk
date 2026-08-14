# V150 production recovery and watchdog handoff

This is the final source-preserving production integration harness for the Termux/PROot deployment.

## Why it runs on the Termux host

The production tmux server, legacy watchdog, launcher and helper scripts live on the Termux host. The recovery harness therefore must be run from a Termux host shell, not from inside Debian/PROot. It enters Debian only for `/app` lock/source inspection, builds, and the temporary MCP coexistence test.

The harness never runs `git pull`, `git reset`, `git checkout`, `git clean`, or `update.py` against `/app`. It fingerprints the production branch, HEAD, `start.sh`, `bot/__main__.py`, and `bot/modules/atri_ai.py` before and after the run.

## Flow

1. Verify Termux-host context and the isolated `rewrite/rust-go-ts-v150` clone.
2. Snapshot production source and validate the live launcher/watchdog/health/network helpers.
3. Build the Debian supervisor and Android/arm64 host watchdog binary.
4. Classify the live bot state using tmux + singleton flock + local health.
5. If production is stopped, create only the `prixok-bot` tmux session through the existing `$HOME/prixok-bot.sh` launcher. Recovery sets `ATRI_PRODUCTION_LAUNCHER_GUARD=1` so the opaque host ensure hook is bypassed, while the launcher still keeps `RUN_SOURCE_UPDATE=0`.
6. Require tmux + held singleton lock. If local shared-component health is still unhealthy, call the existing browser/shared-component ensure helper exactly once, then require healthy local health before continuing.
7. Run Serena + Context7 + Semgrep temporarily inside Debian and verify coexistence/RSS while the production bot stays healthy.
8. Re-check the production source fingerprint.
9. Perform a controlled watchdog handoff: stop one legacy watchdog if present, then start the host-native V150 watchdog.
10. Verify exactly one V150 watchdog owner, zero legacy owners, the same bot pane PID, held lock, and healthy production state.
11. If handoff verification fails, stop V150 and restore the legacy watchdog as a fallback. A successfully recovered bot is deliberately left running.
12. Stop the temporary MCP supervisor and write one report to Download.

Interrupting the script during the handoff window also triggers watchdog-owner rollback. If the harness created a tmux session but no singleton worker ever acquired the lock, cleanup removes only that incomplete session.

## Run

First update the isolated clone from inside Debian, then return to the Termux host:

```bash
cd /opt/prixok-v150
git pull --ff-only
cp rewrite/termux-production-recovery-host.sh /data/data/com.termux/files/home/
cp rewrite/termux-production-recovery-common.sh /data/data/com.termux/files/home/
cp rewrite/termux-production-recovery-run.sh /data/data/com.termux/files/home/
chmod 700 /data/data/com.termux/files/home/termux-production-recovery-*.sh
exit
```

Then, from the Termux host shell:

```bash
bash "$HOME/termux-production-recovery-host.sh"
```

The report is written as:

```text
/storage/emulated/0/Download/atri-v150-production-recovery-YYYYMMDD-HHMMSS.txt
```

If Download is not writable, it falls back under `$HOME/.cache/atri-v150-production-recovery/`.

## Tuning

```bash
ATRI_BUILD_JOBS=2
ATRI_PRODUCTION_RECOVERY_BOT_TIMEOUT=240
ATRI_PRODUCTION_RECOVERY_MCP_TIMEOUT=300
ATRI_PRODUCTION_RECOVERY_MCP_HEALTH_INTERVAL=15
ATRI_PRODUCTION_RECOVERY_MCP_SOAK_SECONDS=45
ATRI_PRODUCTION_RECOVERY_HANDOFF_VERIFY_SECONDS=45
ATRI_LOG_TIMEZONE=Asia/Ho_Chi_Minh
```
