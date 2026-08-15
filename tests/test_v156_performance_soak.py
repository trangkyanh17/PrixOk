from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BOT_UTILS = ROOT / "bot/helper/ext_utils/bot_utils.py"
RUNTIME_TUNING = ROOT / "bot/helper/ext_utils/runtime_tuning.py"
PATCHER_PATH = ROOT / "rewrite/v156_performance_patch.py"
PROBE_PATH = ROOT / "rewrite/v156_performance_probe.py"
CANARY_PATH = ROOT / "rewrite/termux-v156-performance-canary.sh"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v156_global_executor_is_bounded_and_named():
    source = BOT_UTILS.read_text(encoding="utf-8")
    assert "ThreadPoolExecutor(max_workers=500)" not in source
    assert "from .runtime_tuning import ATRI_THREAD_POOL_WORKERS" in source
    assert "max_workers=ATRI_THREAD_POOL_WORKERS" in source
    assert 'thread_name_prefix="atri-global"' in source


def test_v156_runtime_tuning_clamps_bad_and_extreme_values(monkeypatch: pytest.MonkeyPatch):
    tuning = _load(RUNTIME_TUNING, "v156_runtime_tuning_test")
    assert tuning.ATRI_THREAD_POOL_WORKERS_DEFAULT == 64
    assert tuning.ATRI_THREAD_POOL_WORKERS_MIN == 8
    assert tuning.ATRI_THREAD_POOL_WORKERS_MAX == 128
    assert tuning._bounded_env_int("MISSING_V156", 64, 8, 128) == 64

    monkeypatch.setenv("V156_INT", "not-an-int")
    assert tuning._bounded_env_int("V156_INT", 64, 8, 128) == 64
    monkeypatch.setenv("V156_INT", "1")
    assert tuning._bounded_env_int("V156_INT", 64, 8, 128) == 8
    monkeypatch.setenv("V156_INT", "999")
    assert tuning._bounded_env_int("V156_INT", 64, 8, 128) == 128


def _fixture_tree(tmp_path: Path):
    patcher = _load(PATCHER_PATH, "v156_performance_patch_test")
    source_root = tmp_path / "source"
    live_root = tmp_path / "live"
    backup = tmp_path / "backup"

    tuning_dst = source_root / patcher.TUNING_REL
    tuning_dst.parent.mkdir(parents=True, exist_ok=True)
    tuning_dst.write_bytes(RUNTIME_TUNING.read_bytes())

    live_bot = live_root / patcher.BOT_UTILS_REL
    live_bot.parent.mkdir(parents=True, exist_ok=True)
    live_bot.write_text(
        "from concurrent.futures import ThreadPoolExecutor\n"
        + patcher.BOT_IMPORT_ANCHOR
        + "\nTHREAD_POOL = ThreadPoolExecutor(max_workers=500)\n",
        encoding="utf-8",
    )
    before = live_bot.read_bytes()
    return patcher, source_root, live_root, backup, before


def test_v156_patcher_apply_verify_and_exact_rollback(tmp_path: Path):
    patcher, source_root, live_root, backup, before = _fixture_tree(tmp_path)
    result = patcher.apply(source_root, live_root, backup)
    assert result["ok"] is True
    assert result["changed"] is True
    assert (backup / patcher.MANIFEST_NAME).is_file()
    assert patcher.verify(source_root, live_root)["applied"] is True

    live_text = (live_root / patcher.BOT_UTILS_REL).read_text(encoding="utf-8")
    assert patcher.BOT_OLD_POOL not in live_text
    assert patcher.BOT_NEW_IMPORT in live_text
    assert patcher.BOT_NEW_POOL in live_text
    assert (live_root / patcher.TUNING_REL).read_bytes() == RUNTIME_TUNING.read_bytes()

    manifest = json.loads((backup / patcher.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == set(patcher.MANAGED_RELS)
    assert all(item.get("after_sha256") for item in manifest["files"].values())

    rolled = patcher.rollback(live_root, backup)
    assert rolled["rolled_back"] is True
    assert (live_root / patcher.BOT_UTILS_REL).read_bytes() == before
    assert not (live_root / patcher.TUNING_REL).exists()


def test_v156_rollback_stale_gate_is_all_or_nothing(tmp_path: Path):
    patcher, source_root, live_root, backup, before = _fixture_tree(tmp_path)
    patcher.apply(source_root, live_root, backup)
    tuning = live_root / patcher.TUNING_REL
    bot = live_root / patcher.BOT_UTILS_REL
    bot_after = bot.read_bytes()
    tuning.write_text(tuning.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed after V156 apply"):
        patcher.rollback(live_root, backup)

    assert bot.read_bytes() == bot_after
    assert bot.read_bytes() != before


def test_v156_patcher_refuses_partial_custom_state(tmp_path: Path):
    patcher, source_root, live_root, backup, _ = _fixture_tree(tmp_path)
    bot = live_root / patcher.BOT_UTILS_REL
    bot.write_text(
        bot.read_text(encoding="utf-8").replace(
            patcher.BOT_IMPORT_ANCHOR,
            patcher.BOT_IMPORT_ANCHOR + patcher.BOT_NEW_IMPORT,
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="partial/custom"):
        patcher.apply(source_root, live_root, backup)
    assert not backup.exists()


def test_v156_probe_evaluates_growth_threads_and_fds():
    probe = _load(PROBE_PATH, "v156_performance_probe_test")
    base = {
        "pid": 10,
        "start_ticks": 100,
        "rss_kib": 100_000,
        "swap_kib": 10_000,
        "threads": 20,
        "fd_count": 100,
        "mem_available_kib": 2_000_000,
        "swap_free_kib": 3_000_000,
    }
    good = dict(base, rss_kib=130_000, swap_kib=12_000, threads=40, fd_count=140)
    result = probe._evaluate_samples([base, good], max_rss_growth_mib=64, max_swap_growth_mib=16, max_threads=64, max_fds=256)
    assert result["ok"] is True
    assert result["rss_growth_kib"] == 30_000
    assert result["swap_growth_kib"] == 2_000
    assert result["thread_peak"] == 40
    assert result["fd_peak"] == 140

    bad = dict(good, rss_kib=300_000, threads=200)
    failed = probe._evaluate_samples([base, bad], max_rss_growth_mib=64, max_swap_growth_mib=16, max_threads=64, max_fds=256)
    assert failed["ok"] is False
    assert failed["checks"]["rss_growth_ok"] is False
    assert failed["checks"]["thread_peak_ok"] is False


def test_v156_source_contract_fixture(tmp_path: Path):
    probe = _load(PROBE_PATH, "v156_performance_probe_contract_test")
    root = tmp_path / "live"
    bot = root / "bot/helper/ext_utils/bot_utils.py"
    tuning = root / "bot/helper/ext_utils/runtime_tuning.py"
    bot.parent.mkdir(parents=True, exist_ok=True)
    bot.write_text(BOT_UTILS.read_text(encoding="utf-8"), encoding="utf-8")
    tuning.write_text(RUNTIME_TUNING.read_text(encoding="utf-8"), encoding="utf-8")
    result = probe.source_contract(root)
    assert result["ok"] is True
    assert all(result["checks"].values())


def test_v156_python_sources_compile():
    for path in (RUNTIME_TUNING, PATCHER_PATH, PROBE_PATH):
        py_compile.compile(str(path), doraise=True)


def test_v156_canary_shell_syntax_and_self_test():
    syntax = subprocess.run(["bash", "-n", str(CANARY_PATH)], cwd=ROOT, text=True, capture_output=True, check=False)
    assert syntax.returncode == 0, syntax.stderr
    self_test = subprocess.run(["bash", str(CANARY_PATH), "--self-test"], cwd=ROOT, text=True, capture_output=True, check=False)
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert "v156 performance canary self-test: PASS" in self_test.stdout


def test_v156_canary_before_after_preservation_contract():
    source = CANARY_PATH.read_text(encoding="utf-8")
    for marker in (
        "PRE-V156 RESOURCE BASELINE",
        "PRE_V156_NEGATIVE",
        "PATCH LIVE V156 PERFORMANCE",
        "POST-V156 SHORT SOAK",
        "POST-PRESERVATION GATES",
        "ATRI_PERFORMANCE_GUARD_V156_INSTALLED",
        "AUTO ROLLBACK",
        "V155_BASELINE",
        "Python remains sole Telegram/AI owner",
        "git status --porcelain=v1 --untracked-files=all",
    ):
        assert marker in source
    assert "/app/bot/modules/atri_ai.py" not in source


def test_v156_patcher_scope_is_only_global_executor_files():
    patcher = _load(PATCHER_PATH, "v156_performance_patch_scope_test")
    assert set(patcher.MANAGED_RELS) == {
        "bot/helper/ext_utils/bot_utils.py",
        "bot/helper/ext_utils/runtime_tuning.py",
    }
