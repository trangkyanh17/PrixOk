"""Regression tests for supported command argument flags."""

from pathlib import Path

import pytest


@pytest.fixture
def arg_parser():
    """Load only arg_parser without importing the full bot stack."""

    file_path = (
        Path(__file__).resolve().parent.parent
        / "bot"
        / "helper"
        / "ext_utils"
        / "bot_utils.py"
    )

    source = file_path.read_text(encoding="utf-8")

    snippet_start = source.find("def arg_parser(")
    if snippet_start == -1:
        raise RuntimeError("Không tìm thấy hàm arg_parser")

    snippet_end = source.find("\ndef ", snippet_start + 1)
    if snippet_end == -1:
        snippet_end = len(source)

    namespace = {}
    exec(source[snippet_start:snippet_end], namespace)

    return namespace["arg_parser"]


def test_ad_bool_flag_set(arg_parser):
    args = {
        "-ad": False,
        "-z": False,
        "link": "",
    }

    arg_parser(
        ["http://x", "-ad"],
        args,
    )

    assert args["-ad"] is True
    assert args["link"] == "http://x"


def test_unknown_flag_left_alone(arg_parser):
    args = {
        "-ad": False,
        "link": "",
    }

    arg_parser(
        ["http://x", "-unknown"],
        args,
    )

    assert args["-ad"] is False
    assert args["link"] == "http://x -unknown"
