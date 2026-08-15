from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PATCHER = REPO_ROOT / "rewrite" / "v154_production_patch.py"
PROBE = REPO_ROOT / "rewrite" / "v154_production_probe.py"
MODULE_RELS = (
    "bot/modules/atri_system_guard.py",
    "bot/modules/atri_sticker_privacy_guard.py",
    "bot/modules/atri_webapp_safety_guard.py",
    "bot/modules/atri_xlsx_formula_guard.py",
    "bot/modules/atri_artifact_relevance_guard.py",
)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        text=True,
        capture_output=True,
        check=check,
    )


def _strip_v154_init(text: str) -> str:
    start_marker = "# ATRI_SYSTEM_CONTRACT_GUARD_V154_BOOT"
    end_marker = 'LOGGER.exception("ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALL_FAILED")'
    start = text.index(start_marker)
    end_start = text.index(end_marker, start)
    end = text.find("\n", end_start)
    if end < 0:
        end = len(text)
    else:
        end += 1
    return text[:start] + text[end:]


def _make_trees(tmp_path: Path) -> tuple[Path, Path, str, str]:
    source = tmp_path / "source"
    live = tmp_path / "live"
    (source / "bot/modules").mkdir(parents=True)
    (live / "bot/modules").mkdir(parents=True)

    source_init = (REPO_ROOT / "bot/__init__.py").read_text(encoding="utf-8")
    source_main = (REPO_ROOT / "bot/__main__.py").read_text(encoding="utf-8")
    (source / "bot/__init__.py").write_text(source_init, encoding="utf-8")
    (source / "bot/__main__.py").write_text(source_main, encoding="utf-8")
    for rel in MODULE_RELS:
        target = source / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)

    baseline_init = _strip_v154_init(source_init) + "\n# LIVE_CUSTOM_INIT_PRESERVED\n"
    baseline_main = source_main.replace(
        "from .modules.atri_system_guard import install_atri_system_post_import_guard\n",
        "",
    ).replace(
        "install_atri_system_post_import_guard()\n",
        "",
    ) + "\n# LIVE_CUSTOM_MAIN_PRESERVED\n"
    (live / "bot/__init__.py").write_text(baseline_init, encoding="utf-8")
    (live / "bot/__main__.py").write_text(baseline_main, encoding="utf-8")
    return source, live, baseline_init, baseline_main


def test_v154_patch_preserves_custom_live_source_and_rolls_back(tmp_path: Path):
    source, live, baseline_init, baseline_main = _make_trees(tmp_path)
    backup = tmp_path / "backup"

    applied = _run(
        str(PATCHER),
        "apply",
        "--source-root",
        str(source),
        "--live-root",
        str(live),
        "--backup-dir",
        str(backup),
    )
    payload = json.loads(applied.stdout)
    assert payload["ok"] is True
    assert "LIVE_CUSTOM_INIT_PRESERVED" in (live / "bot/__init__.py").read_text()
    assert "LIVE_CUSTOM_MAIN_PRESERVED" in (live / "bot/__main__.py").read_text()

    verified = _run(
        str(PATCHER),
        "verify",
        "--source-root",
        str(source),
        "--live-root",
        str(live),
    )
    assert json.loads(verified.stdout)["ok"] is True

    rolled = _run(
        str(PATCHER),
        "rollback",
        "--source-root",
        str(source),
        "--live-root",
        str(live),
        "--backup-dir",
        str(backup),
    )
    assert json.loads(rolled.stdout)["ok"] is True
    assert (live / "bot/__init__.py").read_text(encoding="utf-8") == baseline_init
    assert (live / "bot/__main__.py").read_text(encoding="utf-8") == baseline_main
    assert all(not (live / rel).exists() for rel in MODULE_RELS)


def test_v154_patch_refuses_stale_destructive_rollback(tmp_path: Path):
    source, live, _baseline_init, _baseline_main = _make_trees(tmp_path)
    backup = tmp_path / "backup"
    _run(
        str(PATCHER),
        "apply",
        "--source-root",
        str(source),
        "--live-root",
        str(live),
        "--backup-dir",
        str(backup),
    )
    init_path = live / "bot/__init__.py"
    init_path.write_text(
        init_path.read_text(encoding="utf-8") + "\n# LATER_PRODUCTION_EDIT\n",
        encoding="utf-8",
    )

    result = _run(
        str(PATCHER),
        "rollback",
        "--source-root",
        str(source),
        "--live-root",
        str(live),
        "--backup-dir",
        str(backup),
        check=False,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "refusing destructive rollback" in payload["error"]
    assert "LATER_PRODUCTION_EDIT" in init_path.read_text(encoding="utf-8")


def test_v154_isolated_smoke_probe_runs_without_bot_import():
    deps = _run(str(PROBE), "deps", "--live-root", str(REPO_ROOT))
    assert json.loads(deps.stdout)["ok"] is True

    smoke = _run(str(PROBE), "smoke", "--live-root", str(REPO_ROOT))
    payload = json.loads(smoke.stdout)
    assert payload["ok"] is True
    assert payload["archive"]["stream_limit"] is True
    assert payload["audio_tool_round"]["oversize_preflight"] is True
    assert payload["artifact_rag"]["unrelated_block"] is True
    assert payload["artifact_rag"]["inactive_history_block"] is True
    assert payload["sticker"]["chat_scope"] is True
    assert payload["xlsx"]["network_block"] is True
    assert payload["xlsx"]["prefixed_function_block"] is True
    assert payload["webapp"]["loopback_block"] is True
