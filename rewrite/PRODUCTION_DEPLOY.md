# V150 production deploy / upgrade / rollback

This is the managed host-side deployment path for the V150 production supervisor after the canary, recovery, handoff, persistence, and real reboot proof have passed.

The live production source tree at `/app` is not updated, reset, checked out, cleaned, or otherwise mutated by this deploy manager.

## Runtime ownership

V150 owns watchdog lifecycle through:

- `$HOME/.local/lib/atri-v150/atri-supervisor`
- `$HOME/atri-v150-production-watchdog.sh`
- `$HOME/.local/lib/atri-v150/prixok-bot-v150.sh`
- `$HOME/.termux/boot/20-atri-v150-production.sh`

The production worker still runs from the canonical `$HOME/prixok-bot.sh` launcher into Debian `/app`, with `RUN_SOURCE_UPDATE=0`.

`termux/prixok-bot.sh` no longer calls the legacy `atri-production-ensure.sh` path. V150 is the only watchdog/repair owner.

## Deploy manager

Run `rewrite/termux-v150-deploy.sh` from the Termux host. It expects the isolated clone at `/opt/prixok-v150` inside Debian and requires branch `rewrite/rust-go-ts-v150` with a clean tracked tree.

Copy the manager from the isolated clone to Termux home after pulling the desired branch revision:

```bash
# Inside Debian
cd /opt/prixok-v150
git pull --ff-only
cp rewrite/termux-v150-deploy.sh /data/data/com.termux/files/home/
chmod 700 /data/data/com.termux/files/home/termux-v150-deploy.sh
exit
```

The manager itself never performs a Git update.

### Status

Read-only runtime status:

```bash
bash "$HOME/termux-v150-deploy.sh" status
```

### First managed install

Use only when production is already healthy, there is no legacy watchdog owner, and there are at most one V150 owner:

```bash
bash "$HOME/termux-v150-deploy.sh" install
```

It builds the Android/arm64 supervisor from the isolated clone, snapshots the currently installed V150 runtime files, atomically installs the new runtime, restarts only the V150 watchdog, verifies the production bot pane/lock/health, and verifies that the `/app` source fingerprint did not change.

### Upgrade

For later V150 revisions:

```bash
bash "$HOME/termux-v150-deploy.sh" upgrade
```

Upgrade requires exactly one healthy V150 watchdog before replacement. The bot worker is not restarted as part of a normal watchdog upgrade; its tmux pane PID and singleton lock must remain stable.

### Automatic rollback

If a new install/upgrade fails startup, singleton, bot, health, or source-integrity checks after the snapshot is taken, the manager restores the previous V150 runtime snapshot automatically and starts it again.

Manual rollback to the snapshot recorded before the most recent successful install/upgrade:

```bash
bash "$HOME/termux-v150-deploy.sh" rollback
```

Deployment backups are stored under:

```text
$HOME/.local/state/atri-v150-deploy/backups/
```

### Legacy cleanup

After V150 is healthy and persistent, archive the obsolete host watchdog/ensure artifacts and any legacy Termux boot hook instead of deleting them:

```bash
bash "$HOME/termux-v150-deploy.sh" cleanup-legacy
```

The command refuses cleanup unless exactly one V150 watchdog owns production, zero legacy watchdog processes exist, the bot singleton lock is held, and local production health is healthy.

Archives are stored under:

```text
$HOME/.local/state/atri-v150-deploy/legacy-archives/
```

Emergency file restore is available without starting the legacy watchdog:

```bash
bash "$HOME/termux-v150-deploy.sh" restore-legacy
```

Keeping restoration separate from process startup prevents accidental dual-watchdog ownership.

## Reports

Every manager invocation writes a report to Android Download when writable:

```text
/storage/emulated/0/Download/atri-v150-deploy-<action>-YYYYMMDD-HHMMSS.txt
```

## Production rules

- Never run a legacy watchdog and V150 watchdog concurrently.
- Never point deploy operations at the live customized `/app` Git tree.
- Do not use broad `pkill`; the manager signals only the exact V150 watchdog PID set it resolves.
- Keep the device-specific local health, browser ensure, and network-state helpers in `$HOME`; V150 uses them but does not overwrite them.
- `termux/atri-production-watchdog.sh` remains in Git only as a deprecated rollback/audit reference. It is not the production owner after V150 cutover.
