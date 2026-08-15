from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "rewrite/v156_performance_probe.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("v156_performance_probe_proc_test", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_v156_probe_samples_current_linux_process():
    probe = _load_probe()
    sample = probe.sample_process(os.getpid())
    assert sample["pid"] == os.getpid()
    assert sample["start_ticks"] > 0
    assert sample["rss_kib"] > 0
    assert sample["threads"] >= 1
    assert sample["fd_count"] >= 0
    assert sample["mem_available_kib"] > 0


def test_v156_probe_detects_process_identity_change():
    probe = _load_probe()
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
    changed = dict(base, start_ticks=101)
    result = probe._evaluate_samples(
        [base, changed],
        max_rss_growth_mib=64,
        max_swap_growth_mib=16,
        max_threads=64,
        max_fds=256,
    )
    assert result["ok"] is False
    assert result["checks"]["pid_stable"] is False
