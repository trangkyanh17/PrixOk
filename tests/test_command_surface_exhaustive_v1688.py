from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_COMMANDS = ROOT / "bot/helper/telegram_helper/bot_commands.py"
CORE_HANDLERS = ROOT / "bot/core/handlers.py"
COMMAND_UI = ROOT / "bot/modules/atri_command_ui.py"


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _fstring_base(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    raise AssertionError(f"unsupported command literal: {ast.dump(node)}")


def _command_values(node: ast.AST) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [_fstring_base(item) for item in node.elts]
    return [_fstring_base(node)]


def _bot_command_attrs() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    tree = _tree(BOT_COMMANDS)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BotCommands"
    )
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if isinstance(target, ast.Name):
            result[target.id] = _command_values(stmt.value)
    return result


def _find_botcommand_attrs(node: ast.AST) -> list[str]:
    out: list[str] = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "BotCommands"
        ):
            out.append(child.attr)
    return out


def _handler_routes() -> list[tuple[str, str, str, int]]:
    """Return (BotCommands attr, handler class, callback, group)."""
    routes: list[tuple[str, str, str, int]] = []
    for node in ast.walk(_tree(CORE_HANDLERS)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "add_handler"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Call):
            continue
        handler = node.args[0]
        handler_fn = handler.func
        handler_cls = (
            handler_fn.id
            if isinstance(handler_fn, ast.Name)
            else handler_fn.attr
            if isinstance(handler_fn, ast.Attribute)
            else ""
        )
        if handler_cls not in {"MessageHandler", "EditedMessageHandler"}:
            continue
        callback_node = handler.args[0] if handler.args else None
        callback = (
            callback_node.id
            if isinstance(callback_node, ast.Name)
            else callback_node.attr
            if isinstance(callback_node, ast.Attribute)
            else "<dynamic>"
        )
        attrs = _find_botcommand_attrs(handler)
        group = 0
        for kw in node.keywords:
            if kw.arg == "group" and isinstance(kw.value, ast.Constant):
                group = int(kw.value.value)
        for attr in attrs:
            routes.append((attr, handler_cls, callback, group))
    return routes


def _literal_named_set(name: str) -> set[str]:
    tree = _tree(COMMAND_UI)
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
                value = ast.literal_eval(stmt.value)
                return set(value)
    raise AssertionError(f"missing literal set {name}")


def test_every_botcommand_has_exactly_one_message_route():
    commands = _bot_command_attrs()
    routes = _handler_routes()
    message_routes: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for attr, handler_cls, callback, group in routes:
        if handler_cls == "MessageHandler":
            message_routes[attr].append((handler_cls, callback, group))

    missing = sorted(set(commands) - set(message_routes))
    assert not missing, f"BotCommands with no MessageHandler route: {missing}"

    duplicates = {
        attr: values
        for attr, values in message_routes.items()
        if len(values) != 1
    }
    assert not duplicates, f"BotCommands with duplicate MessageHandler routes: {duplicates}"


def test_command_aliases_do_not_collide_between_botcommands():
    commands = _bot_command_attrs()
    owners: dict[str, list[str]] = defaultdict(list)
    for attr, values in commands.items():
        for value in values:
            owners[value].append(attr)
    collisions = {k: v for k, v in owners.items() if len(v) > 1}
    assert not collisions, f"command alias collisions: {collisions}"


def test_mirror_and_leech_alias_catalog_matches_authoritative_botcommands():
    commands = _bot_command_attrs()
    mirror_attrs = {
        "MirrorCommand",
        "QbMirrorCommand",
        "JdMirrorCommand",
        "YtdlCommand",
        "GallerydlCommand",
        "NzbMirrorCommand",
    }
    leech_attrs = {
        "LeechCommand",
        "QbLeechCommand",
        "JdLeechCommand",
        "YtdlLeechCommand",
        "GallerydlLeechCommand",
        "NzbLeechCommand",
    }
    expected_mirror = {v for attr in mirror_attrs for v in commands[attr]}
    expected_leech = {v for attr in leech_attrs for v in commands[attr]}
    assert _literal_named_set("MIRROR_COMMANDS") == expected_mirror
    assert _literal_named_set("LEECH_COMMANDS") == expected_leech


def test_mirror_botcommands_are_wired_to_expected_callbacks_once():
    expected = {
        "MirrorCommand": "mirror",
        "QbMirrorCommand": "qb_mirror",
        "JdMirrorCommand": "jd_mirror",
        "NzbMirrorCommand": "nzb_mirror",
        "LeechCommand": "leech",
        "QbLeechCommand": "qb_leech",
        "JdLeechCommand": "jd_leech",
        "NzbLeechCommand": "nzb_leech",
        "YtdlCommand": "ytdl",
        "YtdlLeechCommand": "ytdl_leech",
        "GallerydlCommand": "gallery_dl",
        "GallerydlLeechCommand": "gallery_dl_leech",
    }
    routes = _handler_routes()
    found: dict[str, list[str]] = defaultdict(list)
    for attr, handler_cls, callback, _group in routes:
        if handler_cls == "MessageHandler" and attr in expected:
            found[attr].append(callback)
    assert {k: v for k, v in found.items()} == {
        attr: [callback] for attr, callback in expected.items()
    }


def test_all_python_command_registrations_parse_and_have_callbacks():
    failures: list[str] = []
    command_calls = 0
    handler_calls = 0
    for path in sorted((ROOT / "bot").rglob("*.py")):
        try:
            tree = _tree(path)
        except SyntaxError as exc:
            failures.append(f"{path}: syntax: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else fn.attr
                if isinstance(fn, ast.Attribute)
                else ""
            )
            if name == "command":
                command_calls += 1
                if not node.args:
                    failures.append(f"{path}:{node.lineno}: command() without argument")
            if name == "add_handler":
                handler_calls += 1
                if not node.args:
                    failures.append(f"{path}:{node.lineno}: add_handler() without handler")
                elif isinstance(node.args[0], ast.Call) and not node.args[0].args:
                    failures.append(f"{path}:{node.lineno}: handler without callback")
    assert command_calls >= 30, f"unexpectedly small command surface: {command_calls}"
    assert handler_calls >= 30, f"unexpectedly small handler surface: {handler_calls}"
    assert not failures, "\n".join(failures)


def test_no_exact_duplicate_core_command_callback_tuples():
    routes = _handler_routes()
    counts = Counter(routes)
    duplicates = {route: count for route, count in counts.items() if count > 1}
    assert not duplicates, f"exact duplicate core routes: {duplicates}"


def test_every_core_command_route_is_stable_across_three_dispatch_lookups():
    commands = _bot_command_attrs()
    routes = _handler_routes()
    by_attr: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for attr, handler_cls, callback, group in routes:
        if handler_cls == "MessageHandler":
            by_attr[attr].append((handler_cls, callback, group))

    for attr, aliases in commands.items():
        expected = tuple(by_attr[attr])
        assert len(expected) == 1, (attr, expected)
        for alias in aliases:
            observed = []
            for _ in range(3):
                observed.append(tuple(by_attr[attr]))
            assert observed == [expected, expected, expected], (alias, observed)
