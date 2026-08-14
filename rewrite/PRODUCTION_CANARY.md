# V150 production integration canary

This canary validates the rewrite beside the live PrixOk production bot without taking watchdog ownership or changing production state.

## Architecture

The rewrite has two runtime locations during the canary:

- MCP lifecycle stays inside Debian/PROot and uses `target/release/atri-supervisor`.
- The watchdog canary is cross-built as a native Android/arm64 Go binary and runs on the Termux host. This is required so it sees the real host `tmux`, `proot-distro`, `$HOME` helpers and production watchdog process rather than Debian-local substitutes.

The host watchdog is started with:

```text
ATRI_REWRITE_WATCHDOG_OBSERVE_ONLY=true
```

Observe-only mode still checks the bot session, local health and network state, but it never creates a missing bot tmux session and never invokes shared-component repair. It logs `WATCHDOG_OBSERVE_ONLY=ACTIVE` on its first tick.

## Run

From the isolated Debian clone:

```bash
cd /opt/prixok-v150/rewrite
./termux-production-canary.sh
```

The script performs one combined run:

1. verifies the rewrite branch and clean tracked tree;
2. validates the Termux-host bridge and the live production helper scripts;
3. requires exactly one legacy `atri-production-watchdog.sh` process;
4. verifies the live `prixok-bot` tmux session, worker lock and local health;
5. rebuilds the Debian supervisor and cross-builds `atri-supervisor-android-arm64`;
6. executes the Android binary natively on the Termux host;
7. runs the new watchdog in observe-only mode beside the legacy watchdog;
8. runs Serena + Context7 + Semgrep inside Debian at the same time;
9. verifies the production tmux pane PID, worker lock, legacy-watchdog PID and local health remain unchanged;
10. checks RSS and confirms the observe-only watchdog emitted no restart/repair action;
11. stops only the canary processes and re-checks that production stayed unchanged.

The canary intentionally does **not** stop the legacy production watchdog, restart the bot, or call the production browser repair helper. A later handoff must be designed from the live watchdog topology captured in this report rather than assuming how the legacy watchdog was launched.

The report is written to the phone Download directory when writable:

```text
atri-v150-production-canary-YYYYMMDD-HHMMSS.txt
```

Useful tuning variables:

```bash
ATRI_BUILD_JOBS=2
ATRI_PRODUCTION_CANARY_SECONDS=45
ATRI_PRODUCTION_CANARY_STARTUP_TIMEOUT=300
ATRI_PRODUCTION_CANARY_HEALTH_INTERVAL=15
ATRI_LOG_TIMEZONE=Asia/Ho_Chi_Minh
```

Termux paths can be overridden if the device uses a non-standard layout:

```bash
ATRI_TERMUX_PREFIX=/data/data/com.termux/files/usr
ATRI_TERMUX_HOME=/data/data/com.termux/files/home
```

A PASS report is evidence that the new host-native watchdog can observe real production correctly while the Debian MCP stack coexists with the live bot. It is not itself a production watchdog cutover.
