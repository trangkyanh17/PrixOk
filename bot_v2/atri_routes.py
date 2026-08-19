from __future__ import annotations

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from bot.modules import atri_command_ui as command_ui
from bot.modules import atri_unified_menu as unified_menu

from .registry import HandlerRegistry


def register_atri_unified_menu_routes(registry: HandlerRegistry) -> None:
    """Give the v2 runtime explicit ownership of unified menu/help routes."""

    registry.add(
        MessageHandler(
            unified_menu.unified_menu_command,
            filters=filters.command(list(unified_menu.HUB_COMMANDS)),
        ),
        group=-21,
        route_id="atri.unified_menu.command",
    )
    registry.add(
        CallbackQueryHandler(
            unified_menu.unified_menu_callback,
            filters=filters.regex(r"^aucm:"),
        ),
        group=-21,
        route_id="atri.unified_menu.callback",
    )


def register_atri_command_ui_routes(registry: HandlerRegistry) -> None:
    """Register the non-overlapping Atri Command UI routes for v2.

    The legacy ``add_atri_command_ui_handlers`` registrar also owns ``/menu``
    and ``/amenu``. In v2 those commands are owned exclusively by the unified
    command center (group -21), so registering the legacy command-center entry
    point would create a second command owner in group -20.
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
