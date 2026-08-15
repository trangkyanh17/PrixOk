from __future__ import annotations

# ATRI_CAPABILITY_BOOTSTRAP_V157

from typing import Any

from bot import LOGGER

from . import atri_capability_engine as engine

_INSTALLED = False


def install_capability_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    # core.handlers has already imported atri_message by value. Patch that local
    # binding before add_handlers() registers it, while the engine itself patches
    # the skill/model aliases inside atri_ai.
    from bot.core import handlers as core_handlers

    engine.install_capability_engine()
    core_handlers.atri_message = engine.wrap_atri_message(core_handlers.atri_message)
    _INSTALLED = True
    LOGGER.info("ATRI_CAPABILITY_BOOTSTRAP_V157_INSTALLED")


def add_capability_runtime_handlers(client: Any) -> None:
    engine.add_capability_handlers(client)
