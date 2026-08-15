#!/usr/bin/env python3
"""Root-assisted Android/Termux production process and resource probe.

V156.4 exists because Android may hide unrelated /proc entries from the Termux
app UID while KernelSU root can still inspect the real host process tree.  This
probe is deliberately read-only and contains no kill/restart/source mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

KIB_PER_MIB = 1024
_PYTHON_BASENAME = re.compile(r"^python(?:3(?:\.\d+)*)?$")
_LEGACY_BASENAME = "atri-production-watchdog.sh"
_V150_BASENAME = "atri-supervisor"


def _read_cmdline(proc_dir: Path) -> list[str]:
    raw = (proc_dir / "cmdline").read_bytes()
    return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]


def _read_stat_ppid_start(proc_dir: Path) -> tuple[int, int]:
    text = (proc_dir / "stat").read_text(encoding="utf-8", errors="replace")
    end = text.rfind(")")
    if end < 0:
        raise ValueError("malformed proc stat")
    tail = text[end + 2 :].split()
    if len(tail) < 20:
        raise ValueError("short proc stat")
    # Tail starts at field 3 (state): field 4 PPID => index 1; field 22 => index 19.
    return int(tail[1]), int(tail[19])


def _read_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key.strip()] = value.strip()
    return out


def _kib(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(0, int(value.split()[0]))
    except (TypeError, ValueError, IndexError):
        return 0


def _iter_processes(proc_root: Path) -> dict[int, dict[str, Any]]:
    if not proc_root.is_dir():
        raise RuntimeError(f"proc root unavailable: {proc_root}")
    processes: dict[int, dict[str, Any]] = {}
    visible_numeric = 0
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        visible_numeric += 1
        try:
            ppid, start_ticks = _read_stat_ppid_start(entry)
            argv = _read_cmdline(entry)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, ValueError):
            continue
        pid = int(entry.name)
        processes[pid] = {
            "pid": pid,
            "ppid": ppid,
            "start_ticks": start_ticks,
            "argv": argv,
        }
    if visible_numeric == 0:
        raise RuntimeError("proc root exposes no numeric processes")
    if not processes:
        raise RuntimeError("no readable process metadata in proc root")
    return processes


def _is_bot_argv(argv: list[str]) -> bool:
    if len(argv) < 3 or not _PYTHON_BASENAME.fullmatch(Path(argv[0]).name):
        return False
    return any(argv[i : i + 2] == ["-m", "bot"] for i in range(1, len(argv) - 1))


def _is_legacy_argv(argv: list[str]) -> bool:
    return any(Path(arg).name == _LEGACY_BASENAME for arg in argv)


def _is_v150_argv(argv: list[str], v150_bin: str) -> bool:
    if not argv:
        return False
    expected = str(Path(v150_bin))
    first = argv[0]
    return first == expected or Path(first).name == _V150_BASENAME


def list_legacy_pids(proc_root: Path) -> list[int]:
    return sorted(pid for pid, item in _iter_processes(proc_root).items() if _is_legacy_argv(item["argv"]))


def list_v150_pids(proc_root: Path, v150_bin: str) -> list[int]:
    return sorted(
        pid
        for pid, item in _iter_processes(proc_root).items()
        if _is_v150_argv(item["argv"], v150_bin)
    )


def _descendants(processes: dict[int, dict[str, Any]], root_pid: int) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, item in processes.items():
        children.setdefault(int(item["ppid"]), []).append(pid)
    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))
    return seen


def resolve_bot_pid(proc_root: Path, pane_pid: int, recorded_pid: int) -> int:
    processes = _iter_processes(proc_root)
    pane = processes.get(pane_pid)
    if pane is None:
        raise RuntimeError(f"tmux pane PID not visible to root: {pane_pid}")
    if not pane["argv"] or Path(pane["argv"][0]).name != "proot":
        raise RuntimeError(f"tmux pane is not proot: pid={pane_pid} argv={pane['argv']!r}")

    descendants = _descendants(processes, pane_pid)
    candidates = sorted(
        pid
        for pid in descendants
        if pid != pane_pid and _is_bot_argv(processes[pid]["argv"])
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"requires exactly one python -m bot descendant of pane {pane_pid}; found {candidates}"
        )
    bot_pid = candidates[0]
    if recorded_pid <= 0:
        raise RuntimeError(f"invalid recorded lock PID: {recorded_pid}")
    if bot_pid != recorded_pid:
        raise RuntimeError(
            f"tmux descendant PID disagrees with singleton lock PID: bot={bot_pid} lock={recorded_pid}"
        )
    return bot_pid


def sample_process(proc_root: Path, pid: int) -> dict[str, Any]:
    proc = proc_root / str(pid)
    if not proc.is_dir():
        raise ProcessLookupError(pid)
    status = _read_kv(proc / "status")
    meminfo = _read_kv(proc_root / "meminfo")
    argv = _read_cmdline(proc)
    ppid, start_ticks = _read_stat_ppid_start(proc)
    try:
        fd_count = sum(1 for _ in (proc / "fd").iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        fd_count = -1
    return {
        "pid": pid,
        "ppid": ppid,
        "start_ticks": start_ticks,
        "cmdline": " ".join(argv),
        "rss_kib": _kib(status.get("VmRSS")),
        "rss_hwm_kib": _kib(status.get("VmHWM")),
        "swap_kib": _kib(status.get("VmSwap")),
        "threads": int((status.get("Threads") or "0").split()[0]),
        "fd_count": fd_count,
        "mem_available_kib": _kib(meminfo.get("MemAvailable")),
        "swap_free_kib": _kib(meminfo.get("SwapFree")),
        "timestamp_monotonic": time.monotonic(),
    }


def evaluate_samples(
    samples: list[dict[str, Any]],
    *,
    max_rss_growth_mib: int,
    max_swap_growth_mib: int,
    max_threads: int,
    max_fds: int,
) -> dict[str, Any]:
    if not samples:
        raise ValueError("samples required")
    baseline = samples[0]
    start_ticks = int(baseline["start_ticks"])
    rss_peak = max(int(x["rss_kib"]) for x in samples)
    swap_peak = max(int(x["swap_kib"]) for x in samples)
    thread_peak = max(int(x["threads"]) for x in samples)
    fd_values = [int(x["fd_count"]) for x in samples if int(x["fd_count"]) >= 0]
    fd_peak = max(fd_values) if fd_values else -1
    rss_growth = max(0, rss_peak - int(baseline["rss_kib"]))
    swap_growth = max(0, swap_peak - int(baseline["swap_kib"]))
    checks = {
        "pid_stable": all(int(x["start_ticks"]) == start_ticks for x in samples),
        "rss_growth_ok": rss_growth <= max_rss_growth_mib * KIB_PER_MIB,
        "swap_growth_ok": swap_growth <= max_swap_growth_mib * KIB_PER_MIB,
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
        "rss_growth_kib": rss_growth,
        "swap_baseline_kib": int(baseline["swap_kib"]),
        "swap_peak_kib": swap_peak,
        "swap_growth_kib": swap_growth,
        "thread_baseline": int(baseline["threads"]),
        "thread_peak": thread_peak,
        "fd_baseline": int(baseline["fd_count"]),
        "fd_peak": fd_peak,
        "mem_available_min_kib": min(int(x["mem_available_kib"]) for x in samples),
        "swap_free_min_kib": min(int(x["swap_free_kib"]) for x in samples),
        "limits": {
            "max_rss_growth_mib": max_rss_growth_mib,
            "max_swap_growth_mib": max_swap_growth_mib,
            "max_threads": max_threads,
            "max_fds": max_fds,
        },
    }


def run_soak(args: argparse.Namespace) -> dict[str, Any]:
    seconds = max(0, int(args.seconds))
    interval = max(1, int(args.interval))
    proc_root = Path(args.proc_root)
    samples = [sample_process(proc_root, args.pid)]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        samples.append(sample_process(proc_root, args.pid))
    return evaluate_samples(
        samples,
        max_rss_growth_mib=args.max_rss_growth_mib,
        max_swap_growth_mib=args.max_swap_growth_mib,
        max_threads=args.max_threads,
        max_fds=args.max_fds,
    )


def fd_contains(proc_root: Path, pid: int, needle: str) -> bool:
    fd_dir = proc_root / str(pid) / "fd"
    if not fd_dir.is_dir():
        raise ProcessLookupError(pid)
    for fd in fd_dir.iterdir():
        try:
            target = os.readlink(fd)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if needle in target:
            return True
    return False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc-root", default="/proc")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-legacy")

    v150 = sub.add_parser("list-v150")
    v150.add_argument("--v150-bin", required=True)

    bot = sub.add_parser("bot-pid")
    bot.add_argument("--pane-pid", type=int, required=True)
    bot.add_argument("--recorded-pid", type=int, required=True)

    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--pid", type=int, required=True)

    soak = sub.add_parser("soak")
    soak.add_argument("--pid", type=int, required=True)
    soak.add_argument("--seconds", type=int, default=60)
    soak.add_argument("--interval", type=int, default=10)
    soak.add_argument("--max-rss-growth-mib", type=int, default=384)
    soak.add_argument("--max-swap-growth-mib", type=int, default=256)
    soak.add_argument("--max-threads", type=int, default=192)
    soak.add_argument("--max-fds", type=int, default=4096)

    fd = sub.add_parser("fd-clean")
    fd.add_argument("--pid", type=int, required=True)
    fd.add_argument("--forbidden-substring", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    proc_root = Path(args.proc_root)
    try:
        if args.command == "list-legacy":
            for pid in list_legacy_pids(proc_root):
                print(pid)
            return 0
        if args.command == "list-v150":
            for pid in list_v150_pids(proc_root, args.v150_bin):
                print(pid)
            return 0
        if args.command == "bot-pid":
            print(resolve_bot_pid(proc_root, args.pane_pid, args.recorded_pid))
            return 0
        if args.command == "snapshot":
            result = sample_process(proc_root, args.pid)
            result["ok"] = True
        elif args.command == "soak":
            result = run_soak(args)
        else:
            clean = not fd_contains(proc_root, args.pid, args.forbidden_substring)
            result = {"ok": clean, "pid": args.pid, "clean": clean}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
