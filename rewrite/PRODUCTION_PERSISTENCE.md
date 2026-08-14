# V150 production persistence and reboot proof

This stage wires the already-passed V150 production watchdog to Android boot without touching the production `/app` Git tree.

## Safety model

- The persistent watchdog binary remains `$HOME/.local/lib/atri-v150/atri-supervisor`.
- The production watchdog launcher is `$HOME/atri-v150-production-watchdog.sh`.
- V150 uses `$HOME/.local/lib/atri-v150/prixok-bot-v150.sh` as a wrapper around the canonical `$HOME/prixok-bot.sh`. The wrapper sets `ATRI_PRODUCTION_LAUNCHER_GUARD=1` inside the tmux session, so a V150-driven bot recovery cannot invoke the legacy ensure/watchdog path.
- The Termux:Boot hook is `$HOME/.termux/boot/20-atri-v150-production.sh`.
- The boot hook refuses to start V150 if any legacy `atri-production-watchdog.sh` owner is present and refuses duplicate V150 owners.
- The persistence harness does not run `git pull`, `git reset`, `git checkout`, `git clean`, `update.py`, or any source updater against `/app`.

## Phase 1: install + soft failover

Update the isolated Debian clone, copy the one host harness into Termux home, exit Debian, and run it from the Termux host:

```bash
cd /opt/prixok-v150
git pull --ff-only
cp rewrite/termux-v150-persistence-host.sh /data/data/com.termux/files/home/
chmod 700 /data/data/com.termux/files/home/termux-v150-persistence-host.sh
exit
bash "$HOME/termux-v150-persistence-host.sh"
```

The harness detects the `com.termux.boot` provider, scans `$HOME/.termux/boot` for legacy watchdog references, stages the V150 launchers/hook, fingerprints `/app`, gracefully stops only the current V150 watchdog, invokes the boot hook as a soft failover, and verifies that:

- exactly one V150 watchdog returns;
- zero legacy watchdogs appear;
- the production bot keeps the same tmux pane PID;
- the singleton worker lock remains held;
- local production health stays healthy;
- invoking the boot hook a second time does not create a duplicate;
- production source fingerprints remain unchanged.

A successful first phase ends with `OVERALL SOFT_PASS_REBOOT_PENDING`. That is not yet proof that Android delivered the boot broadcast.

## Phase 2: real reboot proof

After phase 1 passes, reboot Android normally. Do not manually start the watchdog. After the device is back and Termux:Boot has had time to run, open Termux and execute:

```bash
bash "$HOME/termux-v150-persistence-host.sh" --post-reboot-verify
```

The verifier compares `/proc/sys/kernel/random/boot_id` with the pre-reboot baseline and requires the boot hook marker to contain the new boot ID. It also requires exactly one V150 watchdog, zero legacy watchdogs, a live `prixok-bot` tmux session, held singleton lock, healthy local production state, and unchanged source fingerprints.

Only this second phase can produce the final reboot-persistence `OVERALL PASS`.

## Reports

Reports are written to Android Download when writable:

```text
/storage/emulated/0/Download/atri-v150-production-persistence-YYYYMMDD-HHMMSS.txt
```

Persistence state and boot markers are kept under:

```text
$HOME/.cache/atri-v150-persistence/
```
