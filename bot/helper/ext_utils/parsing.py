"""Safe parsers for values received from Telegram, config, and subprocesses."""

from __future__ import annotations

import ast
import json
from typing import Any, TypeVar

T = TypeVar("T")


def parse_literal(value: Any, expected_type: type[T] | tuple[type, ...] | None = None) -> T | Any:
    """Parse JSON/Python literal syntax without executing code.

    JSON is attempted first for predictable booleans/null; ``ast.literal_eval``
    remains accepted for backwards-compatible config syntax such as tuples and
    single-quoted dictionaries.
    """
    if not isinstance(value, str):
        parsed = value
    else:
        text = value.strip()
        if not text:
            raise ValueError("Empty literal value")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError) as exc:
                raise ValueError("Invalid literal value") from exc

    if expected_type is not None and not isinstance(parsed, expected_type):
        if isinstance(expected_type, tuple):
            names = ", ".join(item.__name__ for item in expected_type)
        else:
            names = expected_type.__name__
        raise ValueError(f"Expected {names}, got {type(parsed).__name__}")
    return parsed


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError("Invalid boolean value")


def parse_json_object(value: bytes | str | bytearray) -> dict[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object")
    return parsed
