from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATCHER_PATH = ROOT / "rewrite" / "v153_ai_guard_patch.py"

spec = importlib.util.spec_from_file_location("v153_ai_guard_patch", PATCHER_PATH)
assert spec is not None and spec.loader is not None
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def make_pre_v153_live_tree(tmp_path: Path) -> Path:
    live = tmp_path / "live"
    modules = live / "bot" / "modules"
    modules.mkdir(parents=True)

    canonical = (ROOT / "bot" / "__init__.py").read_text(encoding="utf-8")
    assert patcher.marker_state(canonical) == (1, 1, 1)
    pre_v153 = canonical.replace(patcher.HOOK_BLOCK, "", 1)
    assert patcher.marker_state(pre_v153) == (0, 0, 0)
    (live / "bot" / "__init__.py").write_text(pre_v153, encoding="utf-8")
    return live


def test_apply_real_init_then_verify_and_rollback_exactly(tmp_path: Path):
    live = make_pre_v153_live_tree(tmp_path)
    init_file = live / "bot" / "__init__.py"
    original = init_file.read_bytes()
    backup = tmp_path / "backup"

    result = patcher.apply(ROOT, live, backup)
    assert result["applied"] is True
    assert result["already_hooked"] is False
    assert patcher.marker_state(init_file.read_text(encoding="utf-8")) == (1, 1, 1)

    live_guard = live / "bot" / "modules" / "atri_ai_runtime_guard.py"
    assert live_guard.is_file()
    assert live_guard.read_bytes() == (
        ROOT / "bot" / "modules" / "atri_ai_runtime_guard.py"
    ).read_bytes()

    verified = patcher.verify(ROOT, live)
    assert verified["applied"] is True

    rolled = patcher.rollback(live, backup)
    assert rolled["rolled_back"] is True
    assert init_file.read_bytes() == original
    assert not live_guard.exists()


def test_apply_refuses_missing_anchor_without_mutation(tmp_path: Path):
    live = make_pre_v153_live_tree(tmp_path)
    init_file = live / "bot" / "__init__.py"
    broken = init_file.read_text(encoding="utf-8").replace(
        patcher.HOOK_ANCHOR,
        "# scheduler anchor intentionally changed",
        1,
    )
    init_file.write_text(broken, encoding="utf-8")

    with pytest.raises(RuntimeError, match="AsyncIOScheduler startup anchor"):
        patcher.apply(ROOT, live, tmp_path / "backup")

    assert init_file.read_text(encoding="utf-8") == broken
    assert not (live / "bot" / "modules" / "atri_ai_runtime_guard.py").exists()


def test_apply_refuses_partial_hook_state(tmp_path: Path):
    live = make_pre_v153_live_tree(tmp_path)
    init_file = live / "bot" / "__init__.py"
    partial = init_file.read_text(encoding="utf-8") + "\n" + patcher.HOOK_MARKER + "\n"
    init_file.write_text(partial, encoding="utf-8")

    with pytest.raises(RuntimeError, match="partial V153 startup hook"):
        patcher.apply(ROOT, live, tmp_path / "backup")


def test_rollback_refuses_post_canary_init_change(tmp_path: Path):
    live = make_pre_v153_live_tree(tmp_path)
    backup = tmp_path / "backup"
    patcher.apply(ROOT, live, backup)
    init_file = live / "bot" / "__init__.py"
    init_file.write_text(
        init_file.read_text(encoding="utf-8") + "# later live edit\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="bot/__init__.py changed after V153 apply"):
        patcher.rollback(live, backup)


def test_rollback_refuses_post_canary_guard_change(tmp_path: Path):
    live = make_pre_v153_live_tree(tmp_path)
    backup = tmp_path / "backup"
    patcher.apply(ROOT, live, backup)
    guard = live / "bot" / "modules" / "atri_ai_runtime_guard.py"
    guard.write_text(
        guard.read_text(encoding="utf-8") + "# later guard edit\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="atri_ai_runtime_guard.py changed after V153 apply"):
        patcher.rollback(live, backup)


def test_guard_fallback_is_get_only_and_bounded():
    source = (ROOT / "bot" / "modules" / "atri_ai_runtime_guard.py").read_text(
        encoding="utf-8"
    )
    assert "client.get(" in source
    for forbidden in ("client.post(", "client.put(", "client.patch(", "client.delete("):
        assert forbidden not in source
    assert "max(1, min(value, 20))" in source
    assert "_MAX_FILE_TEXT" in source
    assert '"terminal": True' in source
    assert '"data_ok": False' in source


def test_probe_does_not_import_bot_package_or_start_worker():
    source = (ROOT / "rewrite" / "v153_ai_probe.py").read_text(encoding="utf-8")
    assert "spec_from_file_location" in source
    assert "github_rest_readonly_call" in source
    assert "list_commits" in source
    forbidden = (
        "from bot import",
        "import bot",
        "Client(",
        ".send_message(",
        "exec python3 -m bot",
    )
    for marker in forbidden:
        assert marker not in source


def test_canary_contract_preserves_v151_v152_and_forbids_source_git_mutation():
    source = (ROOT / "rewrite" / "termux-v153-ai-canary.sh").read_text(
        encoding="utf-8"
    )
    assert 'EXPECTED_BRANCH="main"' in source
    assert "require_v151_gate_a" in source
    assert "require_v152_gate_b1" in source
    assert "v152_parity_patch.py verify" in source
    assert "v153_ai_guard_patch.py" in source
    assert "v153_ai_probe.py" in source
    assert "env -u GITHUB_PERSONAL_ACCESS_TOKEN -u GITHUB_TOKEN" in source
    assert "ATRI_AI_RUNTIME_GUARD_V153_INSTALLED" in source
    assert "AUTO ROLLBACK" in source
    assert "tmux send-keys -t prixok-bot C-c" in source

    forbidden = re.compile(
        r"^[ \t]*git[ \t]+(pull|reset|checkout|clean)\b|"
        r"^[ \t]*rm[ \t]+-rf[ \t]+/app(?:[/ \t]|$)",
        re.MULTILINE,
    )
    assert forbidden.search(source) is None
