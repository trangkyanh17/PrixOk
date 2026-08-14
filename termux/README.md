# Termux production helpers

These files are source-controlled reference copies of host-side production helpers used with PRoot-Distro Debian.

## Current ownership

V150 owns watchdog and repair lifecycle. The managed deployment path is documented in `rewrite/PRODUCTION_DEPLOY.md` and implemented by `rewrite/termux-v150-deploy.sh`.

- `prixok-bot.sh`: canonical worker launcher. It enters Debian `/app`, disables source self-updates with `RUN_SOURCE_UPDATE=0`, and executes `start.sh`. It does not invoke the legacy ensure/watchdog path.
- `atri-production-watchdog.sh`: **deprecated rollback/audit reference only**. Do not run it concurrently with the V150 watchdog.

Device-specific helpers such as local health, browser ensure, network state, credentials, and runtime sessions remain provisioned separately in Termux `$HOME` and are not overwritten by the V150 deploy manager.

## Production rules

- V150 and the legacy watchdog must never be active at the same time.
- Do not `git pull`, reset, checkout, or clean the live customized `/app` tree as part of V150 deployment.
- Use the isolated `/opt/prixok-v150` clone to build V150 artifacts.
- Use `termux-v150-deploy.sh cleanup-legacy` to archive obsolete live legacy helpers rather than deleting them. The archive can be restored with `restore-legacy` without starting a second watchdog.
- Use `termux-v150-deploy.sh upgrade` for later supervisor revisions so the current runtime is snapshotted and automatically rolled back if invariants fail.
