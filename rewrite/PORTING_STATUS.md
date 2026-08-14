# PrixOk rewrite V150 status

V150 has two distinct readiness targets. They must not be conflated.

1. **Repository integration readiness**: merge the Rust/Go/TypeScript runtime, native helpers, MCP lifecycle, Termux supervisor/watchdog, validation harnesses and managed deploy tooling into `main` while the existing Python/Pyrogram bot remains the production Telegram worker.
2. **Full bot replacement readiness**: replace the Python Telegram/Atri runtime with the new Go/Rust runtime end to end.

The first target is the scope of the V150 `main` integration. The second target is intentionally **not** part of this merge.

## Production contract for the `main` integration

The merge is additive with one production ownership change already validated on the real Termux device:

- Python remains the Telegram worker and continues to start through `start.sh` with `exec python3 -m bot`.
- The canonical Termux bot launcher continues to enter Debian `/app` with `RUN_SOURCE_UPDATE=0`.
- V150 owns the production watchdog/supervisor lifecycle after the completed handoff; the deprecated shell watchdog remains only as rollback/audit reference.
- The managed V150 deploy path builds from the isolated `/opt/prixok-v150` clone and must not update, reset, clean or check out the live customized `/app` source tree.
- The worker singleton lock remains `/app/.atri-prixok-bot-v133.lock`.
- No merge of V150 code into `main` authorizes switching production Telegram message handling from Python to the experimental Go adapter.

CI enforces the Python-worker invariant and the source-mutation guards.

## Implemented and automated

### Rust native/runtime layer

- Runtime/configuration primitives and bot command parsing.
- SHA-256 hashing and bounded file/archive inspection.
- ZIP/TAR/TAR.GZ traversal, size and compression-ratio protections.
- SQLite artifact storage/search with content redaction.
- Recent-history normalization and WAL persistence.
- Long-memory storage, retrieval, pinning, dedupe, suppression, stats and forget operations.
- Delta Force China S1-S10 native search/history/comparison runtime.
- Release-optimized native binaries.

### Go runtime/provider/tool layer

- Provider/model configuration, availability, healing and persisted control state.
- Free-provider routing and quota/cooldown/latency-aware smart routing.
- Vertex service-account authentication, token cache, request protocol, text/tool generation and retries.
- Tool registry, privacy gates, orchestration, progressive output processing and reply chunking.
- Google public, Cloud and Workspace helper/tool implementations.
- Telegram-facing gateway abstraction and orchestration adapter.

### MCP and supervisor layer

- Serena, Context7, GitHub, Semgrep, Sentry and Chrome DevTools routing/policy.
- Persistent stdio/HTTP MCP sessions, pagination guards, reconnect, prewarm, idle pruning and bounded shutdown.
- Read-only/sensitive-path policy controls.
- Coordinated Go supervisor for MCP lifecycle and production watchdog components.
- Termux-host Android/arm64 watchdog binary.
- Observe-only production canary mode.
- Exact-child/process-group shutdown boundaries and bounded TERM/KILL escalation.

### Production/Termux tooling

- Isolated all-in-one validation.
- Production canary and topology capture.
- Controlled recovery/handoff tooling.
- V150 persistence/boot integration.
- Pre-reboot validation and real reboot proof workflow.
- Managed install/upgrade/rollback with production source fingerprint guard.
- Legacy watchdog cleanup/archive and restore tooling.
- Rollback metadata restoration: rolling back a pre-managed install now clears stale deployed-SHA state instead of reporting the failed candidate as active.

### Build reproducibility

- `rewrite/Cargo.lock` is committed and Rust CI/builds run with `--locked`.
- `rewrite/web/package-lock.json` is committed and web CI/builds use `npm ci`.
- Rewrite CI runs on the development branch, on `main` pushes that touch the rewrite integration, and on pull requests targeting `main`.

## Validation completed for repository integration

The real-device validation sequence completed the supervisor/watchdog production gates before the merge decision:

- isolated Rust/Go/MCP validation;
- production observe-only coexistence;
- host topology and single-owner checks;
- controlled watchdog handoff/recovery;
- MCP coexistence and bounded resource checks;
- persistence/boot installation;
- real reboot verification;
- managed production deploy path;
- legacy-owner cleanup without restarting or replacing the Python worker.

GitHub CI covers Rust formatting/check/Clippy/tests/release build, Go formatting/vet/tests/race/build/cross-build, shell helper self-tests and TypeScript typecheck/build.

## Still missing for full bot replacement

These items remain future work and are **not blockers for the additive `main` integration**, because production continues to use the Python implementation for them.

### Concrete Telegram runtime

- A real Telegram/Pyrogram-equivalent transport behind the Go `TelegramGateway` interface.
- Command/callback dispatch parity.
- Sticker learning/reply behavior.
- Moderation/admin flows.
- Telegram media edge cases.
- Flood-wait/rate-limit behavior and production progressive-message timing.

### Remaining Atri parity

- `atri_skills` activation/context parity.
- Attachment/document finalizers and generated-artifact delivery parity.
- Full web/browser research path parity.
- Sticker subsystem parity.
- Admin/moderation and natural-control command parity.
- Differential live fixtures for all provider/model edge cases.

### Full-replacement validation gates

Before production message handling can move away from Python, all of the following are still required:

1. Concrete Telegram transport and command/callback parity.
2. End-to-end Telegram tests for text, replies, topics, attachments, voice/TTS, long replies, tool calls and failure recovery.
3. Differential fixtures against the current Python behavior for memory, routing, payloads, tools and Telegram-facing responses.
4. Live Vertex/Google/Workspace/MCP integration tests for routes used by the replacement runtime.
5. Security review of owner/private tools, attachment limits, command boundaries and secret redaction.
6. A separate side-by-side canary for the **Telegram worker replacement**, followed by explicit cutover approval.

## Merge policy

`rewrite/rust-go-ts-v150` may be integrated into `main` when all repository-integration checks are green and the integration commit satisfies `main` commit-signature policy.

Merging the code does **not** switch the production Telegram worker. `start.sh` remains Python-backed and CI must continue to assert that invariant until the full-replacement gates above are completed in a later change.
