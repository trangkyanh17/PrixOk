from __future__ import annotations

# ATRI_CAPABILITY_BOOTSTRAP_V157

import inspect
from typing import Any

from bot import LOGGER

from . import atri_capability_engine as engine

_INSTALLED = False


def _extend_vertex_skill_context(activation: dict[str, Any]) -> str:
    base = engine.skill_vertex_context_v2(activation)
    records = list(activation.get("records", []) or [])
    if len(records) <= 2:
        return base

    from bot.modules import atri_skills

    chunks = ["[ATRI ACTIVE SKILLS V2 EXTENDED]"]
    total = 0
    for record in records[2:4]:
        content = atri_skills._skill_content(record)
        remaining = 14000 - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n[SKILL_CONTENT_TRUNCATED_V157]\n"
        chunks.append(content)
        total += len(content)
    chunks.append("[END ATRI ACTIVE SKILLS V2 EXTENDED]")
    return "\n\n".join(part for part in (base, "\n".join(chunks)) if part)


def _extend_worker_skill_context(activation: dict[str, Any]) -> str:
    base = engine.skill_worker_context_v2(activation)
    records = [
        record
        for record in list(activation.get("records", []) or [])
        if bool(getattr(record, "worker_eligible", False))
        and str(getattr(record, "privacy", "auto") or "auto").lower() != "private"
    ]
    if len(records) <= 2:
        return base

    from bot.modules import atri_skills

    chunks = ["[ATRI PUBLIC WORKER SKILLS V2 EXTENDED]"]
    total = 0
    for record in records[2:4]:
        content = atri_skills._skill_content(record)
        remaining = 7000 - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n[WORKER_SKILL_TRUNCATED_V157]\n"
        chunks.append(content)
        total += len(content)
    chunks.append("[END ATRI PUBLIC WORKER SKILLS V2 EXTENDED]")
    return "\n\n".join(part for part in (base, "\n".join(chunks)) if part)


async def _skills_command_with_permission(client: Any, message: Any) -> None:
    from bot.modules import atri_skills

    parts = list(getattr(message, "command", None) or [])
    if not parts:
        parts = str(getattr(message, "text", "") or "").strip().split()
    cmd = str(parts[0] if parts else "skills").split("@", 1)[0].lstrip("/").casefold()

    if cmd == "skill" and len(parts) >= 2:
        name = str(parts[1]).strip()
        if name.casefold() not in {"off", "auto", "clear"}:
            record = atri_skills.get_skills(include_disabled=True).get(name)
            if record is not None and engine._permission(record) == "owner":
                uid = engine._message_user_id(message)
                if not engine._is_owner(uid):
                    await engine._reply(message, "Skill này chỉ dành cho Owner.")
                    return

    result = engine._skills_command_v2(client, message)
    if inspect.isawaitable(result):
        await result


def install_capability_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # core.handlers has already imported atri_message by value. Patch that local
    # binding before add_handlers() registers it, while the engine itself patches
    # the skill/model aliases inside atri_ai.
    from bot.core import handlers as core_handlers
    from bot.modules import atri_ai, atri_skills

    engine.install_capability_engine()

    # V157 refines task classification but does not replace the proven V156
    # worker classifier for signals it still considers ordinary chat.
    legacy_task_type = engine._ORIGINALS.get("free_task")

    def task_type_with_legacy_fallback(text: str) -> str:
        refined = engine.classify_task(text)
        if refined != "chat":
            return refined
        if callable(legacy_task_type):
            return str(legacy_task_type(text))
        return "chat"

    atri_ai._atri_free_task_type = task_type_with_legacy_fallback
    atri_ai._atri_skill_vertex_context = _extend_vertex_skill_context
    atri_ai._atri_skill_worker_context = _extend_worker_skill_context
    atri_skills.atri_skills_command = _skills_command_with_permission
    core_handlers.atri_message = engine.wrap_atri_message(core_handlers.atri_message)
    _INSTALLED = True
    LOGGER.info("ATRI_CAPABILITY_BOOTSTRAP_V157_INSTALLED")


def add_capability_runtime_handlers(client: Any) -> None:
    engine.add_capability_handlers(client)
