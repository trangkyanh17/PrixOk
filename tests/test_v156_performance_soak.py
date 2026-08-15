from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_UTILS = ROOT / "bot/helper/ext_utils/bot_utils.py"
RUNTIME_TUNING = ROOT / "bot/helper/ext_utils/runtime_tuning.py"
PATCHER = ROOT / "rewrite/v156_performance_patch.py"
PROBE = ROOT / "rewrite/v156_performance_probe.py"
CANARY = ROOT / "rewrite/termux-v156-performance-canary.sh"


def test_v156_baseline_requires_bounded_global_executor():
    source = BOT_UTILS.read_text(encoding="utf-8")
    assert "ThreadPoolExecutor(max_workers=500)" not in source
    assert "ATRI_THREAD_POOL_WORKERS" in source
    assert "ThreadPoolExecutor(max_workers=ATRI_THREAD_POOL_WORKERS)" in source


def test_v156_runtime_tuning_contract_exists():
    assert RUNTIME_TUNING.is_file()
    source = RUNTIME_TUNING.read_text(encoding="utf-8")
    for marker in (
        "ATRI_PERFORMANCE_GUARD_V156",
        "ATRI_THREAD_POOL_WORKERS",
        "ATRI_THREAD_POOL_WORKERS_DEFAULT",
        "ATRI_THREAD_POOL_WORKERS_MIN",
        "ATRI_THREAD_POOL_WORKERS_MAX",
        "ATRI_PERFORMANCE_GUARD_V156_INSTALLED",
    ):
        assert marker in source


def test_v156_transactional_patcher_and_probe_exist():
    assert PATCHER.is_file()
    assert PROBE.is_file()
    patch = PATCHER.read_text(encoding="utf-8")
    probe = PROBE.read_text(encoding="utf-8")
    assert "source-manifest.json" in patch
    assert "changed after V156 apply" in patch
    assert "bot/helper/ext_utils/bot_utils.py" in patch
    assert "bot/helper/ext_utils/runtime_tuning.py" in patch
    assert "rss_growth_kib" in probe
    assert "swap_growth_kib" in probe
    assert "thread_peak" in probe
    assert "fd_peak" in probe


def test_v156_canary_has_before_after_and_preservation_contract():
    assert CANARY.is_file()
    source = CANARY.read_text(encoding="utf-8")
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
