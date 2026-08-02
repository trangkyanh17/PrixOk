from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "bot"
    / "helper"
    / "ext_utils"
    / "parsing.py"
)
spec = importlib.util.spec_from_file_location("safe_parsing", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def test_parse_literal_accepts_json_and_python_literals():
    assert module.parse_literal('{"enabled": true}', dict) == {"enabled": True}
    assert module.parse_literal("{'items': (1, 2)}", dict) == {"items": (1, 2)}


def test_parse_literal_rejects_executable_expression(tmp_path):
    marker = tmp_path / "executed"
    payload = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    with pytest.raises(ValueError):
        module.parse_literal(payload)
    assert not marker.exists()


def test_parse_literal_enforces_expected_type():
    with pytest.raises(ValueError):
        module.parse_literal("[1, 2]", dict)


def test_parse_bool_is_strict():
    assert module.parse_bool("true") is True
    assert module.parse_bool("0") is False
    with pytest.raises(ValueError):
        module.parse_bool("maybe")
