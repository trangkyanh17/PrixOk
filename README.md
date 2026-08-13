# PrixOk

A customized deployment of
[anasty17/mirror-leech-telegram-bot](https://github.com/anasty17/mirror-leech-telegram-bot).

## Custom changes

- Increased HTTP download concurrency with aria2.
- Added JDownloader fallback for unsupported links.
- Added JDownloader startup-loop protection.
- Configured for Termux with PRoot-Distro Debian 13 and Google Drive uploads.
- Added Atri assistant integrations, persistent memory, code/MCP tooling, and production runtime guards.
- Source-controlled the core Termux launcher, production ensure helper, and singleton-aware watchdog under `termux/`.

## Termux production layout

The production bot runs inside the Debian proot at `/app` and is launched from the Termux host through `prixok-bot.sh`.

The files under `termux/` are reference copies of the production host helpers. The watchdog checks the Atri singleton lock before rebuilding a missing `prixok-bot` tmux session, preventing duplicate workers when the real `python3 -m bot` process is still alive outside tmux.

Runtime credentials and machine-specific state remain outside Git. In particular, the Vertex service-account file referenced by the launcher must be provisioned privately on the production device.

## Security

Private credentials such as `config.py`, `config.env`, `rclone.conf`,
cookies, service-account files, tokens, sessions, and runtime databases must never be committed.

## License

This project remains licensed under GNU GPL v3.0.
