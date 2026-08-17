from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATCHER_PATH = ROOT / "rewrite" / "v152_parity_patch.py"

spec = importlib.util.spec_from_file_location("v152_parity_patch", PATCHER_PATH)
assert spec is not None and spec.loader is not None
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


@pytest.fixture(autouse=True)
def parity_enable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    enable_file = tmp_path / "debian-state" / "v152" / "enabled"
    monkeypatch.setenv("ATRI_V152_DEBIAN_ENABLE_FILE", str(enable_file))
    return enable_file


def make_live_tree(tmp_path: Path) -> Path:
    live = tmp_path / "live"
    modules = live / "bot" / "modules"
    modules.mkdir(parents=True)
    shutil.copy2(ROOT / "bot" / "modules" / "atri_ai.py", modules / "atri_ai.py")
    return live


def test_apply_integrated_atri_ai_then_rollback_exactly(
    tmp_path: Path, parity_enable_file: Path
):
    live = make_live_tree(tmp_path)
    ai = live / "bot" / "modules" / "atri_ai.py"
    original = ai.read_bytes()
    backup = tmp_path / "backup"

    # V152 parity hooks are now part of the repository baseline. The patcher
    # must recognize that state instead of trying to inject the hooks again.
    result = patcher.apply(ROOT, live, backup)
    assert result["applied"] is True
    assert result["already_hooked"] is True
    assert parity_enable_file.is_file()
    patched = ai.read_text(encoding="utf-8")
    assert patcher.marker_state(patched) == (1, 1, 1, 1)
    assert "_v152_publish_route_decision(" in patched
    assert "_v152_publish_vertex_plan(" in patched
    assert "_v152_publish_tool_observation(" in patched
    assert "generate_free_chat(" in patched  # existing production path preserved

    verified = patcher.verify(ROOT, live)
    assert verified["applied"] is True

    rolled = patcher.rollback(live, backup)
    assert rolled["rolled_back"] is True
    assert ai.read_bytes() == original
    assert not (live / "bot" / "modules" / "atri_v152_parity.py").exists()
    assert not parity_enable_file.exists()


def test_apply_refuses_partial_integrated_hooks_without_mutation(
    tmp_path: Path, parity_enable_file: Path
):
    live = make_live_tree(tmp_path)
    ai = live / "bot" / "modules" / "atri_ai.py"
    original = ai.read_text(encoding="utf-8").replace(
        "# ATRI_V152_DECISION_PARITY_TOOL_BOUNDARY\n",
        "",
        1,
    )
    ai.write_text(original, encoding="utf-8")
    assert patcher.marker_state(original) == (1, 1, 1, 0)

    with pytest.raises(RuntimeError, match="partial V152 parity hooks"):
        patcher.apply(ROOT, live, tmp_path / "backup")

    assert ai.read_text(encoding="utf-8") == original
    assert not parity_enable_file.exists()
    assert not (live / "bot" / "modules" / "atri_v152_parity.py").exists()


def test_rollback_refuses_post_canary_live_change(
    tmp_path: Path, parity_enable_file: Path
):
    live = make_live_tree(tmp_path)
    backup = tmp_path / "backup"
    patcher.apply(ROOT, live, backup)
    ai = live / "bot" / "modules" / "atri_ai.py"
    ai.write_text(ai.read_text(encoding="utf-8") + "# later live edit\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after V152 apply"):
        patcher.rollback(live, backup)

    assert parity_enable_file.is_file()


def test_parity_module_is_loopback_only_and_contains_no_ai_or_tool_executor():
    source = (ROOT / "bot" / "modules" / "atri_v152_parity.py").read_text(
        encoding="utf-8"
    )
    assert "ipaddress.ip_address(host).is_loopback" in source
    assert "/v1/atri/parity" in source
    forbidden = (
        "generate_free_chat(",
        "_vertex_generate(",
        "execute_code_plugin_tool(",
        "execute_google_tool(",
        "execute_weather_tool(",
        ".reply_text(",
        ".send_message(",
    )
    for marker in forbidden:
        assert marker not in source
