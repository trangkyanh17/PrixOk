from __future__ import annotations

import ast
from pathlib import Path


BOT_COMMANDS = Path("bot/helper/telegram_helper/bot_commands.py")
V2_ROUTES = Path("bot_v2/routes.py")
UNIFIED_MENU = Path("bot/modules/atri_unified_menu.py")


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _defined_bot_command_attributes() -> set[str]:
    tree = _tree(BOT_COMMANDS)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "BotCommands":
            continue
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id.endswith("Command"):
                        found.add(target.id)
            elif isinstance(item, ast.AnnAssign):
                target = item.target
                if isinstance(target, ast.Name) and target.id.endswith("Command"):
                    found.add(target.id)
    return found


def _referenced_route_command_attributes() -> set[str]:
    tree = _tree(V2_ROUTES)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "BotCommands":
            found.add(node.attr)
    return found


def test_every_legacy_bot_command_has_an_explicit_v2_owner():
    defined = _defined_bot_command_attributes()
    referenced = _referenced_route_command_attributes()

    # Help is deliberately migrated to the unified Atri command center rather
    # than the legacy bot_help callback.
    assert defined - referenced == {"HelpCommand"}
    assert referenced - defined == set()


def test_help_external_owner_is_present_in_unified_menu():
    text = UNIFIED_MENU.read_text(encoding="utf-8")
    assert '_cmd("help")' in text
    assert "unified_menu_command" in text


def test_transfer_surface_is_not_accidentally_dropped():
    referenced = _referenced_route_command_attributes()
    required = {
        "MirrorCommand",
        "QbMirrorCommand",
        "JdMirrorCommand",
        "NzbMirrorCommand",
        "LeechCommand",
        "QbLeechCommand",
        "JdLeechCommand",
        "NzbLeechCommand",
        "YtdlCommand",
        "YtdlLeechCommand",
        "GallerydlCommand",
        "GallerydlLeechCommand",
        "MediaDirectCommand",
    }
    assert required <= referenced
