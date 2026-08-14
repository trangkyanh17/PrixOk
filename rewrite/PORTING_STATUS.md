# PrixOk rewrite V150 porting status

This branch is experimental. Do not merge it into `main` or `dev`, and do not point production at it, until the parity, live-integration, migration and Termux validation gates below are complete.

## Implemented with automated tests

### Rust `atri-core` / `atri-native`

- Environment/runtime configuration primitives, provider configuration types, chat message types and bot command parsing.
- SHA-256 hashing, bounded directory listing and archive inspection for ZIP/TAR/TAR.GZ.
- Archive traversal rejection, size limits and compression-ratio limits.
- SQLite artifact storage/search with content redaction.
- Python-compatible recent-history normalization, bounded retention and WAL SQLite persistence.
- Long-memory SQLite storage for chat archive and memory cards, user-only durable archive, explicit auto-pin markers, dedupe, relevance retrieval, recent-history suppression, manual-card priority, stats/forget operations and repetition guard.
- Delta Force China native knowledge-base search for S1-S10:
  - NFKC-normalized entity/document lookup.
  - category/mode/platform filters.
  - SQLite FTS when available with deterministic LIKE fallback.
  - current entity lookup separated from historical evidence so current values are never backfilled into S1-S9.
  - season history grouping and two-season evidence comparison.
  - JSON CLI bridge through `atri-native delta-search`, `delta-history` and `delta-compare`.

### Go runtime control and provider layer

- Gemini model specs/aliases and model/thinking validation.
- Atomic persisted runtime configuration updates with duplicate-assignment collapse and file-mode preservation.
- Provider/model choices, provider-specific thinking levels, healing and availability filtering.
- Provider request payload/header generation and task/model metadata.
- Provider env-file parsing/cache/overlay and API-key extraction.
- Provider capability state, audit snapshots/events and human/compact reporting.
- Free-provider HTTP execution for Cerebras/Groq/OpenRouter with provider-specific payloads/headers.
- Smart-provider ordering using quota, cooldown and latency signals.
- OpenRouter shared 429 cooldown handling, Cerebras multi-window quota recovery and Groq reset-aware quota ratios.
- Persisted provider control state and automatic fallback from dead manual providers to smart mode.

### Vertex AI runtime

- Service-account JSON parsing, RS256 JWT assertion, access-token acquisition, expiry-aware cache and forced refresh.
- Vertex generation URL construction from service-account project, location and resolved model.
- Vertex request/retry/error protocol with request-ID propagation.
- Text generation runtime with empty-text retry and continuation handling.
- Function/tool generation runtime with ordered tool responses, bounded parallel execution, tool timeout and Vertex-safe tool-result sanitization.
- Function declarations, tool configuration and payload assembly with privacy/mode filtering.
- Registry-backed tool runtime plus per-request progressive callback override.

### Tool registry and orchestration

- Registered tool declarations, execution bridge, mode filtering and public/private privacy gates.
- Unified Atri orchestration from free-pool worker through Vertex verifier/supervisor into final Vertex text/tool execution.
- Worker retry policy, supervisor context assembly, response cleaning and Telegram-safe reply chunking.
- Configured builtin-runtime factory wiring shared HTTP clients, Google credentials, Workspace auth and native Delta Force paths.
- Optional MCP backend wiring into the builtin registry without exposing coding tools in chat mode.

### Coding MCP policy/runtime layer

- Plugin routing for Serena, Context7, GitHub, Semgrep, Sentry and Chrome DevTools.
- Query-hint plugin selection plus explicit-plugin/direct fast-path detection.
- Recursive MCP JSON-schema sanitization with local `$ref` resolution and `$schema`/`$defs` removal.
- Read-only tool policy, Sentry deny-list and sensitive credential/config path blocking.
- TTL tool-discovery cache, safe-tool scoring, deterministic filtering and sanitized tool/result envelopes.
- `code_plugin_search` and `code_plugin_call` registry bridges restricted to coding mode.
- Optional plugin availability probing and `code_plugin_status`.
- Sequential `code_plugin_batch` validation, stop-on-error handling and policy checks.
- Context7 fast path that resolves a library ID, caches it and queries docs through the MCP backend.
- Builtin runtime can inject an MCP backend and register the complete five-tool coding MCP surface.
- Concrete stdio transports for Serena, Semgrep, Sentry and Chrome DevTools with persistent subprocess sessions.
- Concrete Streamable HTTP-style transports for Context7 and GitHub MCP with session ID and negotiated protocol headers.
- MCP initialization, initialized notifications, JSON-RPC request-ID matching, server-notification skipping and bounded unsupported client-request responses on stdio.
- Tool-list cursor pagination with repeated-cursor and page-count guards.
- Persistent per-plugin backend sessions with timeout invalidation, one safe discovery reconnect, idle pruning and bounded-concurrency prewarm helpers.
- HTTP SSE response selection ignores notification/nonmatching events and waits for the matching JSON-RPC response.
- HTTP session shutdown uses best-effort MCP session DELETE; stdio shutdown terminates the entire uvx/npx process group to avoid orphan descendants.
- Stdio stderr capture is bounded to prevent long-lived MCP processes from growing memory indefinitely.
- Stdio server-request replies use a captured writer so timeout/abort cannot race on the transport's mutable stdin field.
- Automated transport tests cover reconnect/reuse, prewarm, header/session propagation, SSE notifications, pagination, HTTP session close, stdio server requests and real subprocess round-trips.
- Experimental Go supervisor lifecycle wiring supports startup MCP prewarm, periodic health refresh/reconnect, idle-session pruning and graceful close on SIGINT/SIGTERM.
- MCP supervisor lifecycle is disabled by default behind `ATRI_REWRITE_MCP_LIFECYCLE`; timeout, intervals, idle TTL, request timeout, plugin set and prewarm concurrency are configurable through environment variables.
- Lifecycle status logging is deterministic, single-line and bounded so remote/plugin errors cannot inject unbounded multiline log output.
- Rewrite CI runs the Go race detector in addition to formatting, vet, normal tests and build.

### Ported builtin tools

Public tools:

- Open-Meteo weather.
- YouTube Data API search.
- Google Safe Browsing.
- Google Books.
- Google Places API (New).
- Google Routes.
- Google Geocoding.
- Google Cloud Translation v3.
- Google capability reporting.
- Google Cloud Text-to-Speech synthesis with an injected Telegram voice sender.
- Google Cloud Vision OCR for attached images.
- Google Document AI for attached PDF/image data.
- Delta Force CN current search, history and two-season evidence comparison through the Rust native runtime.

Owner/private tools:

- Google Drive search and text read/export.
- Google Calendar event read.
- Gmail search and full-message read.
- Google Sheets range read.
- Private tools remain hidden unless the caller explicitly enables the private-tool route.

Google authentication/runtime helpers:

- OAuth refresh-token cache for Workspace APIs.
- Optional delegated Workspace service-account JWT flow with Drive/Calendar/Gmail/Sheets readonly scopes.
- Cloud service-account sharing for Vertex, Translation, Speech, TTS, Vision and Document AI.
- Google Speech-to-Text v2 helper and Gemini inline-audio part builder.

### Telegram-facing bridge

- Telegram-agnostic gateway interface for reply/edit/voice operations.
- Per-request progressive response callback without mutating global orchestrator state.
- Final progressive edit with fallback reply when edit fails.
- Multi-chunk final replies.
- Tool-context propagation for user/chat/thread IDs.
- Attachment bytes/MIME propagation for Vision/Document AI.
- Injected voice sender for Google TTS.

### TypeScript web helper

- Typed API/client helpers for the rewrite web surface and corresponding TypeScript checks.

## Still missing before production parity

### Concrete Telegram runtime

- Real Telegram/Pyrogram-equivalent transport implementation behind the new gateway interface.
- Command/callback dispatch parity with the current Python bot.
- Sticker learning/reply behavior, moderation/admin flows and all Telegram media edge cases.
- Production progressive-message timing/rate-limit behavior and flood-wait handling.

### Code-agent/MCP ecosystem

The policy, schema, discovery, registry, concrete stdio/HTTP transport and opt-in Go supervisor lifecycle are now ported. Remaining MCP work is live production parity:

- Live-test the opt-in MCP supervisor lifecycle under Termux/Debian, including startup prewarm, periodic health refresh, idle pruning, SIGTERM shutdown and CPU/RAM/temperature behavior.
- Live-test Serena semantic-code behavior against the production `/app` project and verify warm-session memory/CPU characteristics.
- Live-test Context7 and GitHub MCP authentication/session behavior against their real endpoints, including the forced-GitHub coding path.
- Validate Semgrep long-lived process behavior and reconnect under real scan failures.
- Live-test Sentry authentication and verify the read-only deny-list end to end.
- Validate Chrome DevTools stateful multi-step batches against the production Chrome/Xvfb/proxy stack.
- Add support for an independent server-initiated Streamable HTTP channel only if a production MCP server actually requires it; the current client advertises no client capabilities and handles response-stream notifications.
- Differential live tool-schema fixtures against the Python MCP SDK path.

### Atri feature parity

- `atri_skills` activation/context behavior.
- Attachment/document runtime finalizers and generated artifact delivery parity.
- Web/browser research path and production browser/proxy integration.
- Sticker subsystem parity.
- Admin/moderation and natural-control command parity.
- Remaining provider/model audit edge cases that only occur against live services.

### Production/runtime parity

- Complete Termux/Debian bot/process supervisor entrypoint; the current Go `main.go` only exposes the opt-in MCP lifecycle and remains inert by default.
- Existing singleton lock/watchdog/autostart semantics.
- Browser/Xvfb/proxy/aria2/qBittorrent/SAB/Serena warm-worker lifecycle.
- Production configuration loading and secret handling without exposing credentials to logs or tools.
- Extend graceful shutdown, process supervision and restart behavior from the MCP lifecycle to the complete Android/proot process tree.

## Required validation gates before merge

1. `cargo fmt --check`, `cargo test --workspace` and release build all pass.
2. `gofmt`, `go test ./...`, `go test -race ./...`, `go vet ./...` and Go build all pass.
3. TypeScript typecheck/build passes.
4. Differential fixtures compare the rewrite against the current Python behavior for memory, provider routing, Vertex payloads, tools and Telegram-facing responses.
5. Live tests verify Vertex, Google public APIs, Workspace OAuth/service-account routes and the real Delta Force CN database.
6. Telegram end-to-end tests cover text, replies, topics, attachments, voice/TTS, long responses, tool calls and failure recovery.
7. Security review verifies owner/private tool isolation, attachment limits, command execution boundaries and secret redaction.
8. Termux/Debian soak test verifies CPU/RAM/heat, no duplicate workers, clean restart and screen-off operation.
9. Controlled side-by-side/canary deployment passes before any production replacement.

## Merge policy

Until every required gate is complete, keep `rewrite/rust-go-ts-v150` isolated. `main` and `dev` remain the Python production source of truth.
