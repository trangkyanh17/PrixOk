from __future__ import annotations

import ast
from pathlib import Path


ROUTES = Path("bot_v2/routes.py")
RUNTIME = Path("bot_v2/runtime.py")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_legacy_add_handlers_is_never_called_by_v2():
    text = _source(RUNTIME)
    assert "bot.core.handlers import add_handlers" not in text
    assert "add_handlers()" not in text


def test_help_has_only_unified_menu_owner_in_v2_core_routes():
    text = _source(ROUTES)
    assert "bot_help" not in text
    assert "core.help" not in text
    assert "The unified command center" in text


def test_ping_has_one_explicit_core_owner():
    tree = ast.parse(_source(ROUTES), filename=str(ROUTES))
    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "_message":
            continue
        if len(node.args) < 4:
            continue
        route = node.args[1]
        callback = node.args[2]
        command = node.args[3]
        if (
            isinstance(route, ast.Constant)
            and route.value == "core.ping"
            and isinstance(callback, ast.Name)
            and callback.id == "ping"
            and isinstance(command, ast.Attribute)
            and command.attr == "PingCommand"
        ):
            matches.append(node)

    assert len(matches) == 1


def test_generic_atri_routes_exclude_slash_commands():
    text = _source(ROUTES)
    assert "NOT_SLASH_COMMAND" in text
    assert text.count("& NOT_SLASH_COMMAND") >= 3
