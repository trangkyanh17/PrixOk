# V151 Telegram parity and cutover plan

This document defines the migration boundary between the existing Python/Pyrogram production worker and the V150+ Go/Rust runtime. It is intentionally conservative: production Telegram ownership stays with Python until every relevant gate below is proven on the real Termux/PROot device.

## Current production ownership

`start.sh` still executes `python3 -m bot`. `bot/__main__.py` acquires the production worker singleton lock, starts the bot/user Pyrogram clients, initializes torrent/download services, installs handlers, schedules MCP prewarm tasks, and then owns the event loop forever.

V150 currently owns watchdog/persistence/deploy lifecycle around that worker. `runtimecfg/telegram_adapter.go` contains reply/progress abstractions, but the Go supervisor does not yet own Telegram polling/transport.

## Python Telegram surface inventory

### Update classes

Production dispatch currently includes:

- normal incoming messages;
- edited command messages;
- callback queries;
- Atri catch-all text and attachment traffic.

The Atri catch-all accepts these media classes in addition to text:

- photo;
- sticker;
- animation;
- video;
- video note;
- document;
- audio;
- voice.

V151 shadow observation mirrors normal messages, edited messages and callback queries before production handler groups run. It records attachment metadata only; it does not download attachment bytes.

### Authorization semantics

Python routing is not just Telegram transport. Handler access depends on `CustomFilters` and mutable runtime state:

- owner-only;
- sudo;
- authorized user/chat/thread;
- unrestricted commands such as start where explicitly configured.

A full cutover must reproduce the exact owner/sudo/authorized/chat/thread semantics before any command ownership moves to Go.

### Core command families

`BotCommands` plus `bot/core/handlers.py` currently cover, at minimum:

- start/help/ping/stats/status/log/restart/speedtest;
- authorize/unauthorize/addsudo/rmsudo/users/bot settings/user settings;
- mirror/qBittorrent mirror/JDownloader mirror/NZB mirror;
- leech/qBittorrent leech/JDownloader leech/NZB leech;
- ytdl/gallery-dl and leech variants;
- clone/count/delete/list/search/NZB search;
- cancel/cancel-all/force-start/select;
- RSS;
- owner shell/exec/aexec/clear-locals.

These commands are only one part of parity. Several modules install additional message/callback handlers independently, including Atri command UI, skills, thinking control, provider control, Rose/Rose-natural, settings, game/RSS and download-related flows.

### Callback families

The central handler table includes callback prefixes such as:

- `botset`;
- `canall`;
- `stopm`;
- `sel`;
- `list_types`;
- `help`;
- `rss`;
- `botrestart`;
- `status`;
- `torser`;
- `userset`.

Additional Atri modules have their own callback families. Callback parity therefore requires a route inventory generated from all handler-installing modules, not only `core/handlers.py`.

### Output and side-effect surface

Python can perform more than text replies. Existing modules include text/message editing, document delivery, attachment processing, voice/audio/media behavior, progress/finalization, settings mutation, task lifecycle operations and download/upload side effects.

The current Go `TelegramGateway` abstraction only exposes text reply, text edit and voice reply. It is not sufficient for a full production replacement.

## V151 phase 1: observe-only shadow bridge

V151 introduces a local shadow path with the following hard invariants:

1. `ATRI_V150_TELEGRAM_SHADOW` defaults to `false`.
2. The Go ingress only binds to loopback. `0.0.0.0`, LAN addresses and malformed addresses are rejected.
3. The Python observer is installed at dispatcher group `-1000`, so it observes updates before production handlers without becoming their owner.
4. Shadow events are queued in a bounded queue. A slow or unavailable V150 endpoint can drop shadow observations but cannot block normal Telegram handling.
5. Transport failures are rate-limited in logs and never propagate into production handlers.
6. The V151 ingress has no Telegram send/edit gateway and returns HTTP 202 only after validating the event envelope.
7. Message text and callback payload are never copied into supervisor logs. Logs contain route metadata only.
8. The Go ingress retries independently if the local listener becomes unavailable instead of taking down the watchdog component.

Default endpoint:

```text
http://127.0.0.1:18750/v1/telegram/shadow
```

Health endpoint:

```text
http://127.0.0.1:18750/healthz
```

The shadow schema currently carries update kind, chat/message/thread/user identifiers, chat type, text/caption, parsed command, callback data and non-downloaded media metadata.

## Cutover gates

### Gate A — transport observation

Required before any model shadowing:

- real phone receives normal text, commands, callbacks and each Atri media class;
- V151 counts the update without duplicate production replies;
- bot pane PID and singleton lock remain unchanged;
- queue remains bounded under bursts;
- disabling the flag restores the exact pre-V151 runtime behavior.

### Gate B — pure routing parity

V150 classifies each observed event without calling Telegram send/edit methods. Compare:

- command family;
- authorization class;
- Atri-vs-system route;
- media type;
- callback family;
- intended provider/tool route.

No production reply ownership moves in this gate.

### Gate C — AI shadow execution

For Atri-only messages, V150 may execute provider/router/memory/tool logic in shadow and persist a bounded parity result. Python still produces the user-visible answer.

Compare at least:

- selected provider/model;
- worker usage/task type;
- tool/plugin route;
- final answer presence/chunk count;
- latency/error class.

Conversation content must not be duplicated into long-lived logs.

### Gate D — allowlisted Atri canary

Only after Gates A-C pass. A small allowlist of user/chat IDs can give V150 reply ownership for plain text Atri traffic. Python remains an immediate fallback if V150 returns an error or exceeds the deadline.

Do not include commands, callbacks, attachments or admin/settings flows in the first ownership canary.

### Gate E — media and callback parity

Port and prove Telegram gateway operations needed by production:

- document/photo/video/audio/sticker/animation delivery as applicable;
- callback answer/edit behavior;
- reply threading/topic IDs;
- progressive edit/finalization;
- attachment acquisition and MIME/file metadata;
- Telegram file-size/error/retry behavior.

### Gate F — command/admin/task parity

Only after Atri traffic is stable. Port command families and reproduce owner/sudo/authorized filters, mutable settings, task lifecycle, downloads/uploads and restart semantics.

### Gate G — worker ownership cutover

The final gate may replace `start.sh -> python3 -m bot` only when:

- all production Telegram routes have explicit parity status;
- the real-device canary has no duplicate polling/handlers;
- graceful restart and rollback are proven;
- watchdog, boot persistence, health and singleton checks recognize the new worker;
- Python fallback remains deployable from a known-good snapshot.

## Parity status matrix

| Surface | Python production | V150/V151 status | Ownership now |
|---|---|---|---|
| Telegram polling/client | Pyrogram bot + optional user client | no concrete production polling client | Python |
| Normal messages | yes | V151 observe envelope | Python |
| Edited messages | command handlers | V151 observe envelope | Python |
| Callback queries | many callback families | V151 observe envelope | Python |
| Text Atri | yes | adapter/orchestrator pieces exist; no live ownership | Python |
| Photo/sticker/animation/video/video-note/document/audio/voice input | yes | metadata shadow only | Python |
| Text reply/edit | yes | Go gateway abstraction exists | Python |
| Voice reply | yes where used | Go gateway abstraction exists | Python |
| Document/media send | yes in Python modules | incomplete in Go gateway | Python |
| Auth owner/sudo/authorized/thread | yes | not yet production-parity wired | Python |
| Settings/admin callbacks | yes | not ported | Python |
| Download/task lifecycle | yes | not ported | Python |
| Provider/router/tools | Python implementation in production; V150 has ported runtime pieces | shadow execution not yet connected | Python |
| Memory/context | Python production + V150 runtime pieces | parity not yet measured live | Python |
| Watchdog/boot/deploy | V150 supervisor | production-proven | V150 |

## Non-goals of V151 phase 1

V151 does **not**:

- create a second Telegram polling client;
- send or edit Telegram messages from Go;
- replace `python3 -m bot`;
- move command ownership;
- download media twice;
- mutate `/app` during V150 deploy operations;
- claim full Telegram parity.
