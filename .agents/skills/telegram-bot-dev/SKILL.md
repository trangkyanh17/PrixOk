---
name: telegram-bot-dev
description: "Develop and debug Telegram bot features, commands, Pyrogram handlers, callbacks, command menus, permissions, and Telegram-specific runtime behavior. Use when working on Telegram bot commands or handlers."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-triggers: "telegram bot; pyrogram; messagehandler; callbackqueryhandler; callback query; botfather; bot command; telegram handler; lệnh telegram; lenh telegram"
---

# Telegram Bot Development

Implement Telegram behavior without breaking existing handler ordering.

## Workflow

1. Identify framework/version and existing handler registration pattern.
2. Reuse the project's command helper and authorization model when possible.
3. Check handler groups, filter overlap, edited-message handling, and callback prefixes.
4. Keep callbacks short and deterministic; acknowledge callback queries promptly.
5. Avoid duplicate command-menu registration races.
6. Preserve thread/chat/user scoping in state.
7. Test both command routing and normal-chat fallthrough after changes.
8. Never print or embed the bot token.

## Pyrogram gotchas

A valid callback handler can still be unreachable because another filter/group consumes the update. Unknown slash commands may not reach a generic text handler, so register explicit commands when a feature depends on them.
