from __future__ import annotations

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from bot.modules import atri_command_ui as command_ui

from .registry import HandlerRegistry


def register_atri_command_ui_routes(registry: HandlerRegistry) -> None:
    """Register the non-overlapping Atri Command UI routes for v2.

    The legacy ``add_atri_command_ui_handlers`` registrar also owns ``/menu``
    and ``/amenu``.  In v2 those commands are owned exclusively by the unified
    command center (group -21), so registering the legacy command-center entry
    point would create a second command owner in group -20.

    The remaining command UI routes are still useful and are registered here
    explicitly so every route has one deterministic owner.
    """

    command_ui._init_notes_sync()

    routes = (
        (
            "atri.command_ui.search",
            command_ui.command_search,
            command_ui.SEARCH_COMMAND,
        ),
        (
            "atri.command_ui.detail",
            command_ui.command_detail,
            command_ui.DETAIL_COMMAND,
        ),
        (
            "atri.command_ui.note.add",
            command_ui.add_note_command,
            command_ui.ADD_NOTE_COMMAND,
        ),
        (
            "atri.command_ui.note.list",
            command_ui.list_notes_command,
            command_ui.LIST_NOTES_COMMAND,
        ),
        (
            "atri.command_ui.note.clear",
            command_ui.clear_notes_command,
            command_ui.CLEAR_NOTES_COMMAND,
        ),
    )

    for route_id, callback, command_name in routes:
        registry.add(
            MessageHandler(
                callback,
                filters=filters.command(command_name),
            ),
            group=-20,
            route_id=route_id,
        )

    registry.add(
        CallbackQueryHandler(
            command_ui.command_center_callback,
            filters=filters.regex(r"^acui:"),
        ),
        group=-20,
        route_id="atri.command_ui.callback",
    )
