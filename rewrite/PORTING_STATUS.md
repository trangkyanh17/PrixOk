# PrixOk rewrite V150 porting status

This branch is experimental and must not be merged into `main` or `dev` until parity and production validation are complete.

## Implemented and CI-tested

- Rust `atri-core`: environment configuration, provider configuration types, chat message types, basic bot command parsing.
- Rust `atri-native`: SHA-256 file hashing, bounded directory listing, ZIP/TAR/TAR.GZ archive inspection, traversal rejection, archive size/ratio limits, SQLite artifact storage/search and content redaction.
- Rust long-memory core: WAL-backed `chat_archive` and `memory_cards`, user-only durable archive, explicit memory auto-pin markers, automatic-card dedupe, bounded relevance retrieval, recent-history suppression, manual-card priority, stats/forget operations, long-memory prompt context formatting and the V148 repetition guard.
- Go supervisor: production repair-backoff policy and configurable runtime paths/intervals.
- TypeScript web layer: typed torrent tree model, tree flattening, folder sizing, selection statistics, recursive selection helpers, request types, torrent tree fetch, selection submit and rename requests.
- Dedicated GitHub Actions workflow: Rust format/check/test/release/smoke, Go format/vet/test/build, TypeScript typecheck.

## Source parity still required

- `bot/__main__.py`: worker startup lifecycle, singleton ownership, startup fan-out and warm services.
- `bot/modules/atri_ai.py`: AI orchestration, request scheduling, Vertex/provider routing, tool calls, continuation handling and Telegram response flow.
- `bot/modules/atri_memory.py`: recent chat normalization/persistence API parity and retention behavior.
- `bot/modules/atri_long_memory.py`: exact FTS5/LIKE ranking parity, NFKC/SequenceMatcher-equivalent similarity, legacy-memory migration and assistant-history cleanup, plus production AI-runtime integration. Core durable storage/retrieval/context behavior is now ported.
- `bot/modules/atri_runtime.py`: model/thinking runtime control and persisted configuration.
- `bot/modules/atri_provider_*`: provider registry, capabilities, request payload parity, failover and free-provider controls.
- `bot/modules/atri_attachment_runtime.py`: complete attachment extraction and media/text parity beyond native archive primitives.
- `bot/modules/atri_document_runtime.py`: document generation bridge and progressive finalization.
- `bot/modules/atri_command_ui.py`: command and callback UI parity.
- `bot/modules/atri_tools/**`: tool registry and MCP/plugin integration.
- mirror/leech stack: aria2, qBittorrent, SABnzbd, rclone, Google Drive, yt-dlp/gallery-dl and direct-link orchestration.
- web selector: full DOM/UI replacement and browser integration tests.
- Termux supervisor: tmux/session probes, active-worker ownership check, local-health repair, network-state probing, log rotation and production soak tests.

## Merge gates

1. Rewrite CI fully green.
2. Behavioral parity tests against current Python implementation.
3. Termux/Android arm64 build and soak test.
4. No duplicate bot worker under session loss/recovery.
5. Attachment/archive fixtures pass safety and correctness checks.
6. AI/provider parity tests pass for configured providers.
7. Torrent selector browser flow passes get/select/rename cases.
8. Production rollback path remains available.
