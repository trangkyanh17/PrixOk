# PrixOk Python v2 rewrite

Branch: `rewrite/python-v2`

This branch is a clean Python runtime rewrite. It does **not** replace `main` and it does not run beside the current production worker.

## What is rewritten first

The first cut replaces the parts most likely to make Telegram behavior non-deterministic:

1. process/bootstrap ownership;
2. Telegram handler registration;
3. command ownership;
4. duplicate handler prevention;
5. command-vs-conversation routing.

Existing mirror/leech/Atri business functions are reused as Python callables while they are migrated. The legacy `bot.core.handlers.add_handlers()` entrypoint is never invoked by v2.

## Hard invariants

- One process owns the production singleton lock.
- One Telegram bot client is created by the runtime.
- One `HandlerRegistry` owns all v2 Telegram registrations.
- Exact duplicate callback/group registrations are ignored.
- One logical `route_id` cannot be rebound to another callback.
- `/ping` has exactly one explicit owner.
- `/help` is owned by the unified command center; legacy `bot_help` is not registered.
- Generic Atri text/media handlers do not receive slash commands.
- Every registered handler is exported to `/app/atri_data/prixok_v2_handler_inventory.tsv` at boot.

## Entry point

From the repository root, using the existing virtualenv and production configuration:

```bash
mltbenv/bin/python -m bot_v2
```

The v2 process intentionally uses the same singleton lock file as the current production worker. If v1 is running, v2 exits with code `73` instead of starting a second Telegram worker.

## Current migration boundary

The runtime and route graph are new. Proven business modules are still imported from `bot.modules` so functionality can be migrated without rewriting download/cloud/AI behavior all at once.

The next migration stage is to move business modules behind explicit service interfaces and remove remaining runtime monkey-patch layers. That stage should happen only after the v2 route/runtime tests are green.
