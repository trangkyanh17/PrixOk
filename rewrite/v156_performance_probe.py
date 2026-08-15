#!/usr/bin/env python3
"""V156 production resource/soak probe.

The probe is deliberately read-only. It samples one known production Python PID
through /proc and evaluates bounded growth rather than assuming a fixed RSS
number, because Android/Termux memory pressure varies with foreground activity.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

KIB_PER_MIB = 1024
DEFAULT_RSS_GROWTH_MIB = 384
DEFAULT_SWAP_GROWTH_MIB = 256
DEFAULT_MAX_THREADS = 192
DEFAULT_MAX_FDS = 4096


def _read_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _kib_value(value: str | None) -> int:
    if not value:
        return 0
    token = value.split()[0]
    try:
        return max(0, int(token))
    except (TypeError, ValueError):
        return 0


def _proc_start_ticks(pid: int) -> int:
    # /proc/<pid>/stat field 22. The comm field may contain spaces inside (...),
    # so split only after the closing parenthesis.
    text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    tail = text[text.rfind(")") + 2 :].split()
    return int(tail[19])


def sample_process(pid: int) -> dict[str, Any]:
    proc = Path(f"/proc/{pid}")
    if not proc.is_dir():
        raise ProcessLookupError(pid)

    status = _read_kv_file(proc / "status")
    meminfo = _read_kv_file(Path("/proc/meminfo"))
    cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
        "utf-8", errors="replace"
    ).strip()
    try:
        fd_count = sum(1 for _ in (proc / "fd").iterdir())
    except (FileNotFoundError, PermissionError):
        fd_count = -1

    return {
        "pid": pid,
        "start_ticks": _proc_start_ticks(pid),
        "cmdline": cmdline,
        "rss_kib": _kib_value(status.get("VmRSS")),
        "rss_hwm_kib": _kib_value(status.get("VmHWM")),
        "swap_kib": _kib_value(status.get("VmSwap")),
        "threads": int(status.get("Threads", "0").split()[0] or 0),
        "fd_count": fd_count,
        "mem_available_kib": _kib_value(meminfo.get("MemAvailable")),
        "swap_free_kib": _kib_value(meminfo.get("SwapFree")),
        "timestamp_monotonic": time.monotonic(),
    }


def _evaluate_samples(
    samples: list[dict[str, Any]],
    *,
    max_rss_growth_mib: int = DEFAULT_RSS_GROWTH_MIB,
    max_swap_growth_mib: int = DEFAULT_SWAP_GROWTH_MIB,
    max_threads: int = DEFAULT_MAX_THREADS,
    max_fds: int = DEFAULT_MAX_FDS,
) -> dict[str, Any]:
    if not samples:
        raise ValueError("samples required")

    baseline = samples[0]
    start_ticks = int(baseline["start_ticks"])
    pid_stable = all(int(item["start_ticks"]) == start_ticks for item in samples)
    rss_peak = max(int(item["rss_kib"]) for item in samples)
    swap_peak = max(int(item["swap_kib"]) for item in samples)
    thread_peak = max(int(item["threads"]) for item in samples)
    fd_values = [int(item["fd_count"]) for item in samples if int(item["fd_count"]) >= 0]
    fd_peak = max(fd_values) if fd_values else -1
    rss_growth_kib = max(0, rss_peak - int(baseline["rss_kib"]))
    swap_growth_kib = max(0, swap_peak - int(baseline["swap_kib"]))
    mem_available_min = min(int(item["mem_available_kib"]) for item in samples)
    swap_free_min = min(int(item["swap_free_kib"]) for item in samples)

    checks = {
        "pid_stable": pid_stable,
        "rss_growth_ok": rss_growth_kib <= max_rss_growth_mib * KIB_PER_MIB,
        "swap_growth_ok": swap_growth_kib <= max_swap_growth_mib * KIB_PER_MIB,
        "thread_peak_ok": thread_peak <= max_threads,
        "fd_peak_ok": fd_peak < 0 or fd_peak <= max_fds,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "sample_count": len(samples),
        "pid": int(baseline["pid"]),
        "rss_baseline_kib": int(baseline["rss_kib"]),
        "rss_peak_kib": rss_peak,
        "rss_growth_kib": rss_growth_kib,
        "swap_baseline_kib": int(baseline["swap_kib"]),
        "swap_peak_kib": swap_peak,
        "swap_growth_kib": swap_growth_kib,
        "thread_baseline": int(baseline["threads"]),
        "thread_peak": thread_peak,
        "fd_baseline": int(baseline["fd_count"]),
        "fd_peak": fd_peak,
        "mem_available_min_kib": mem_available_min,
        "swap_free_min_kib": swap_free_min,
        "limits": {
            "max_rss_growth_mib": max_rss_growth_mib,
            "max_swap_growth_mib": max_swap_growth_mib,
            "max_threads": max_threads,
            "max_fds": max_fds,
        },
    }


def source_contract(live_root: Path) -> dict[str, Any]:
    bot_utils = live_root / "bot/helper/ext_utils/bot_utils.py"
    tuning = live_root / "bot/helper/ext_utils/runtime_tuning.py"
    if not bot_utils.is_file() or not tuning.is_file():
        return {"ok": False, "error": "V156 source files missing"}

    bot_text = bot_utils.read_text(encoding="utf-8", errors="replace")
    tune_text = tuning.read_text(encoding="utf-8", errors="replace")
    checks = {
        "legacy_500_removed": "ThreadPoolExecutor(max_workers=500)" not in bot_text,
        "bounded_worker_import": "ATRI_THREAD_POOL_WORKERS" in bot_text,
        "executor_prefix": 'thread_name_prefix="atri-global"' in bot_text,
        "tuning_marker": "ATRI_PERFORMANCE_GUARD_V156" in tune_text,
        "worker_default": "ATRI_THREAD_POOL_WORKERS_DEFAULT = 64" in tune_text,
        "worker_max": "ATRI_THREAD_POOL_WORKERS_MAX = 128" in tune_text,
    }
    return {"ok": all(checks.values()), "checks": checks}


def run_soak(args: argparse.Namespace) -> dict[str, Any]:
    seconds = max(0, int(args.seconds))
    interval = max(1, int(args.interval))
    samples = [sample_process(args.pid)]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        if time.monotonic() + 0.01 < deadline or seconds > 0:
            samples.append(sample_process(args.pid))
    return _evaluate_samples(
        samples,
        max_rss_growth_mib=args.max_rss_growth_mib,
        max_swap_growth_mib=args.max_swap_growth_mib,
        max_threads=args.max_threads,
        max_fds=args.max_fds,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--pid", type=int, required=True)

    contract = sub.add_parser("source-contract")
    contract.add_argument("--live-root", default="/app")

    soak = sub.add_parser("soak")
    soak.add_argument("--pid", type=int, required=True)
    soak.add_argument("--seconds", type=int, default=60)
    soak.add_argument("--interval", type=int, default=10)
    soak.add_argument("--max-rss-growth-mib", type=int, default=DEFAULT_RSS_GROWTH_MIB)
    soak.add_argument("--max-swap-growth-mib", type=int, default=DEFAULT_SWAP_GROWTH_MIB)
    soak.add_argument("--max-threads", type=int, default=DEFAULT_MAX_THREADS)
    soak.add_argument("--max-fds", type=int, default=DEFAULT_MAX_FDS)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "snapshot":
            result = sample_process(args.pid)
            result["ok"] = True
        elif args.command == "source-contract":
            result = source_contract(Path(args.live_root))
        else:
            result = run_soak(args)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
