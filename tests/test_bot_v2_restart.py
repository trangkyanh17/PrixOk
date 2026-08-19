from __future__ import annotations

import ast
from pathlib import Path


RESTART = Path("bot_v2/commands/restart.py")
ROUTES = Path("bot_v2/routes.py")


def test_v2_restart_never_execs_legacy_bot_entrypoint():
    text = RESTART.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(RESTART))

    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "osexecl":
            calls.append(node)

    assert len(calls) == 1
    rendered = ast.unparse(calls[0])
    assert '"-m", "bot_v2"' in rendered
    assert '"-m", "bot"' not in rendered


def test_restart_callbacks_are_native_v2_and_not_new_task_wrapped():
    text = RESTART.read_text(encoding="utf-8")
    assert "@new_task" not in text
    assert "bot_v2.tasks import SUPERVISOR" in text
    assert "SUPERVISOR.shutdown" in text

    routes = ROUTES.read_text(encoding="utf-8")
    assert "from .commands.restart import confirm_restart, restart_bot" in routes
    assert "from bot.modules import (" in routes
