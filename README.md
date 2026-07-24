# PrixOk

A customized deployment of
[anasty17/mirror-leech-telegram-bot](https://github.com/anasty17/mirror-leech-telegram-bot).

## Custom changes

- Increased HTTP download concurrency with aria2.
- Added JDownloader fallback for unsupported links.
- Added JDownloader startup-loop protection.
- Configured for Termux proot Ubuntu and Google Drive uploads.

## Security

Private credentials such as `config.py`, `config.env`, `rclone.conf`,
cookies and service-account files must never be committed.

## License

This project remains licensed under GNU GPL v3.0.
