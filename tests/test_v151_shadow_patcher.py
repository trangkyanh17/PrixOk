from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATCHER_PATH = ROOT / "rewrite" / "v151_shadow_patch.py"

spec = importlib.util.spec_from_file_location("v151_shadow_patch", PATCHER_PATH)
assert spec is not None and spec.loader is not None
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


BASE_MAIN = """from .core.handlers import add_handlers\n\nadd_handlers()\nprint('keep-custom-live-code')\n"""
VALID_MODULE = """def add_v150_shadow_handlers(client):\n    return False\n"""


def make_trees(tmp_path: Path, module: str = VALID_MODULE):
    source = tmp_path / "source"
    live = tmp_path / "live"
    (source / "bot" / "modules").mkdir(parents=True)
    (live / "bot" / "modules").mkdir(parents=True)
    (source / "bot" / "modules" / "atri_v150_shadow.py").write_text(
        module, encoding="utf-8"
    )
    (live / "bot" / "__main__.py").write_text(BASE_MAIN, encoding="utf-8")
    return source, live


def test_apply_preserves_custom_live_content_and_rollback_restores(tmp_path: Path):
    source, live = make_trees(tmp_path)
    backup = tmp_path / "backup"

    result = patcher.apply(source, live, backup)
    assert result["applied"] is True
    main = (live / "bot" / "__main__.py").read_text(encoding="utf-8")
    assert main.count(patcher.IMPORT_LINE) == 1
    assert main.count(patcher.CALL_LINE) == 1
    assert "keep-custom-live-code" in main
    assert (backup / patcher.MANIFEST_NAME).is_file()

    rolled_back = patcher.rollback(live, backup)
    assert rolled_back["rolled_back"] is True
    assert (live / "bot" / "__main__.py").read_text(encoding="utf-8") == BASE_MAIN
    assert not (live / "bot" / "modules" / "atri_v150_shadow.py").exists()


def test_apply_refuses_ambiguous_anchor_without_mutation(tmp_path: Path):
    source, live = make_trees(tmp_path)
    main_path = live / "bot" / "__main__.py"
    original = BASE_MAIN.replace(
        "from .core.handlers import add_handlers",
        "from .core.handlers import add_handlers\nfrom .core.handlers import add_handlers",
    )
    main_path.write_text(original, encoding="utf-8")

    with pytest.raises(RuntimeError, match="exactly one core handler import anchor"):
        patcher.apply(source, live, tmp_path / "backup")

    assert main_path.read_text(encoding="utf-8") == original
    assert not (live / "bot" / "modules" / "atri_v150_shadow.py").exists()


def test_apply_self_restores_when_new_module_fails_compile(tmp_path: Path):
    source, live = make_trees(tmp_path, module="def broken(:\n")
    main_path = live / "bot" / "__main__.py"

    with pytest.raises(Exception):
        patcher.apply(source, live, tmp_path / "backup")

    assert main_path.read_text(encoding="utf-8") == BASE_MAIN
    assert not (live / "bot" / "modules" / "atri_v150_shadow.py").exists()


def test_rollback_refuses_to_overwrite_post_canary_live_change(tmp_path: Path):
    source, live = make_trees(tmp_path)
    backup = tmp_path / "backup"
    patcher.apply(source, live, backup)
    main_path = live / "bot" / "__main__.py"
    main_path.write_text(main_path.read_text(encoding="utf-8") + "# later change\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after V151 apply"):
        patcher.rollback(live, backup)
