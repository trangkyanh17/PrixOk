# Termux production helpers

These files are source-controlled reference copies of the core host-side Atri runtime helpers used with PRoot-Distro Debian 13.

## Files

- `prixok-bot.sh`: canonical host launcher. It enters the Debian proot, switches to `/app`, disables source self-updates for the patched deployment, and executes `start.sh`.
- `atri-production-watchdog.sh`: supervises the bot tmux session, shared browser components, and network state. Before recreating a missing `prixok-bot` tmux session it probes `/app/.atri-prixok-bot-v133.lock` inside Debian. A held lock means the real worker is still alive, so the watchdog logs `BOT_SESSION_MISSING_WORKER_ACTIVE` instead of spawning a duplicate.

## Deployment notes

These files intentionally contain no credentials. Device-specific helpers, browser/Xvfb supervisors, private sessions, and runtime state are provisioned separately on the Termux device.

Compare these reference helpers with the live `$HOME` copies before installing them. Preserve device-specific settings, validate with `bash -n`, and restart only the affected tmux session. Do not blindly overwrite all production helpers or run `git pull` against the live customized `/app` tree.
