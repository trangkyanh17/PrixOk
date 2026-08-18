from __future__ import annotations

import ast
from pathlib import Path


RUNTIME = Path("bot_v2/runtime.py")
ATRI_ROUTES = Path("bot_v2/atri_routes.py")
UNIFIED_MENU = Path("bot/modules/atri_unified_menu.py")
COMMAND_UI = Path("bot/modules/atri_command_ui.py")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_v2_does_not_call_legacy_command_ui_registrar():
    runtime = _text(RUNTIME)
    assert "add_atri_command_ui_handlers" not in runtime


def test_v2_never_registers_legacy_command_center_callback():
    text = _text(ATRI_ROUTES)
    tree = ast.parse(text, filename=str(ATRI_ROUTES))

    callbacks = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "MessageHandler":
            continue
        if not node.args:
            continue
        callback = node.args[0]
        if isinstance(callback, ast.Attribute):
            callbacks.append(callback.attr)

    assert "command_center" not in callbacks
    assert {
        "command_search",
        "command_detail",
        "add_note_command",
        "list_notes_command",
        "clear_notes_command",
    }.issubset(set(callbacks))


def test_menu_and_amenu_are_intentionally_single_owner_in_v2():
    unified = _text(UNIFIED_MENU)
    command_ui = _text(COMMAND_UI)
    atri_routes = _text(ATRI_ROUTES)

    # Legacy source contains the historical dual ownership.  v2 fixes it by
    # retaining only the unified-menu registrar for these command names.
    assert '_cmd("menu")' in unified
    assert '_cmd("amenu")' in unified
    assert 'MENU_COMMANDS = [_cmd("menu"), _cmd("amenu")]' in command_ui
    assert "command_center" not in atri_routes


def test_help_remains_owned_by_unified_menu_not_command_ui_v2_adapter():
    unified = _text(UNIFIED_MENU)
    atri_routes = _text(ATRI_ROUTES)

    assert '_cmd("help")' in unified
    assert "bot_help" not in atri_routes
    assert "HelpCommand" not in atri_routes
