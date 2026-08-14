# PrixOk rewrite V150 porting status

This branch is experimental and must not be merged into `main` or `dev` until parity and production validation are complete.

## Implemented and CI-tested

- Rust `atri-core`: environment configuration, provider configuration types, chat message types, basic bot command parsing.
- Rust `atri-native`: SHA-256 file hashing, bounded directory listing, ZIP/TAR/TAR.GZ archive inspection, traversal rejection, archive size/ratio limits, SQLite artifact storage/search and content redaction.
- Rust long-memory core: WAL-backed `chat_archive` and `memory_cards`, user-only durable archive, explicit memory auto-pin markers, automatic-card dedupe, bounded relevance retrieval, recent-history suppression, manual-card priority, stats/forget operations, long-memory prompt context formatting and the V148 repetition guard.
- Go runtime control: Gemini model specs and aliases, model/thinking validation, atomic persisted `config.py` updates, duplicate-assignment collapse, file-mode preservation and deterministic model-before-thinking writes.
- Go provider runtime: provider/model choices, provider-specific thinking levels and healing, request payloads and headers, model availability filtering, status icons, task/model metadata, task-specific model ordering, provider env-file parsing/cache/overlay and API-key extraction.
- Go free-pool policy: task chains/fixed models, OpenRouter shared 429 cooldown semantics, dynamic token budgets, attempts/timeouts/error cooldowns, Cerebras multi-window quota recovery, Groq reset-aware quota ratios, latency EWMA and weighted smart-provider ordering/status telemetry.
- Go provider capability/control state: persisted capability/audit records, atomic 0600 state files, alert snapshots/events, provider/model self-healing primitives, persisted provider control state and automatic fallback from dead manual providers to smart mode.
- Go supervisor: production repair-backoff policy and configurable runtime paths/intervals.
- TypeScript web layer: typed torrent tree model, tree flattening, folder sizing, selection statistics, recursive selection helpers, request types, torrent tree fetch, selection submit and rename requests.
- Dedicated GitHub Actions workflow: Rust format/check/test/release/smoke, Go format/vet/test/build, TypeScript typecheck.

## Source parity still required

- `bot/__main__.py`: worker startup lifecycle, singleton ownership, startup fan-out and warm services.
- `bot/modules/atri_ai.py`: AI orchestration, request scheduling, Vertex/provider routing, tool calls, continuation handling and Telegram response flow.
- `bot/modules/atri_memory.py`: recent chat normalization/persistence API parity and retention behavior.
- `bot/modules/atri_long_memory.py`: exact FTS5/LIKE ranking parity, NFKC/SequenceMatcher-equivalent similarity, legacy-memory migration and assistant-history cleanup, plus production AI-runtime integration. Core durable storage/retrieval/context behavior is now ported.
- `bot/modules/atri_runtime.py`: production command/UI bridge remains; model aliases, thinking rules and durable config-file writes are ported.
- `bot/modules/atri_provider_config.py`: core env-file loading/cache, environment overlay and provider API-key extraction are ported; production process/environment integration remains.
- `bot/modules/atri_provider_capabilities.py`: live provider/key/model HTTP probing, provider model discovery, Vertex credential probing and audit execution remain; persisted audit state, alert snapshots/events, classification helpers, static model/task metadata and availability filtering are ported.
- `bot/modules/atri_provider_control.py`: Telegram command/callback UI and live audit presentation remain; persisted control-state file, normalization, thinking/model healing and dead-provider fallback are ported.
- `bot/modules/atri_provider_request.py`: request payload and header construction are ported.
- `bot/modules/atri_free_pool.py`: actual async HTTP client/request execution, production logging and final Vertex fallback integration remain; plain-text request conversion, response parsing primitives, task routing, cooldown policy, dynamic token policy and smart-router telemetry are ported.
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
