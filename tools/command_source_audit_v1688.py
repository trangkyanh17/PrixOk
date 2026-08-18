#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "bot"
CORE = BOT / "core/handlers.py"
BOT_COMMANDS = BOT / "helper/telegram_helper/bot_commands.py"


def parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return "<dynamic>"


def botcommand_attrs(node: ast.AST) -> list[str]:
    attrs = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "BotCommands"
        ):
            attrs.append(child.attr)
    return attrs


def canonical_commands() -> dict[str, list[str]]:
    tree = parse(BOT_COMMANDS)
    cls = next(
        x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "BotCommands"
    )
    out = {}
    for stmt in cls.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        values = stmt.value.elts if isinstance(stmt.value, (ast.List, ast.Tuple)) else [stmt.value]
        aliases = []
        for value in values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                aliases.append(value.value)
            elif isinstance(value, ast.JoinedStr):
                aliases.append("".join(
                    part.value for part in value.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                ))
        out[target.id] = aliases
    return out


def core_routes() -> list[dict]:
    routes = []
    for node in ast.walk(parse(CORE)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "add_handler"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Call):
            continue
        handler = node.args[0]
        attrs = botcommand_attrs(handler)
        if not attrs:
            continue
        callback = name_of(handler.args[0]) if handler.args else "<missing>"
        handler_type = name_of(handler.func)
        group = 0
        for kw in node.keywords:
            if kw.arg == "group" and isinstance(kw.value, ast.Constant):
                group = kw.value.value
        for attr in attrs:
            routes.append({
                "attr": attr,
                "callback": callback,
                "handler": handler_type,
                "group": group,
                "registration_file": str(CORE.relative_to(ROOT)),
                "registration_line": node.lineno,
            })
    return routes


def definitions() -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(BOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = node.lineno
            end = getattr(node, "end_lineno", node.lineno)
            found[node.name].append({
                "file": str(path.relative_to(ROOT)),
                "start": start,
                "end": end,
                "source": "\n".join(
                    f"{i}: {lines[i-1]}" for i in range(start, end + 1)
                ),
            })
    return found


def atri_decorated_commands() -> list[dict]:
    result = []
    for path in sorted(BOT.rglob("atri*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and name_of(dec.func) == "_cmd" and dec.args):
                    continue
                arg = dec.args[0]
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                end = getattr(node, "end_lineno", node.lineno)
                result.append({
                    "command": arg.value,
                    "callback": node.name,
                    "file": str(path.relative_to(ROOT)),
                    "start": node.lineno,
                    "end": end,
                    "source": "\n".join(
                        f"{i}: {lines[i-1]}" for i in range(node.lineno, end + 1)
                    ),
                })
    return result


def main() -> None:
    commands = canonical_commands()
    routes = core_routes()
    defs = definitions()
    unresolved = []
    report = []

    for route in routes:
        matches = defs.get(route["callback"], [])
        if not matches:
            unresolved.append(route)
        item = dict(route)
        item["aliases"] = commands.get(route["attr"], [])
        item["definitions"] = matches
        report.append(item)

    atri = atri_decorated_commands()
    payload = {
        "botcommands": commands,
        "core_routes": report,
        "atri_decorated_commands": atri,
        "counts": {
            "botcommand_attributes": len(commands),
            "botcommand_aliases": sum(len(x) for x in commands.values()),
            "core_command_routes": len(routes),
            "atri_decorated_commands": len(atri),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if unresolved:
        raise SystemExit(f"unresolved command callbacks: {unresolved}")
    if len(commands) < 20 or len(routes) < 20 or len(atri) < 5:
        raise SystemExit(f"unexpectedly small audit surface: {payload['counts']}")


if __name__ == "__main__":
    main()
