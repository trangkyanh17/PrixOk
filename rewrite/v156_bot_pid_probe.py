#!/usr/bin/env python3
"""Resolve the production bot worker without trusting PRoot argv/PID text.

The legacy/default ``argv`` strategy is retained for V156.1 compatibility.
V156.3 uses ``lock-owner``: it identifies the process that owns the exact
production singleton lock inode from kernel-visible state. ``/proc/locks`` is
preferred; ``/proc/<pid>/fd`` inode ownership is an independent fallback and
cross-check. The PID text stored inside the lock and argv matches are only
reported as diagnostics.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_PYTHON_BASENAME = re.compile(r"^python(?:3(?:\.\d+)*)?$")
_PROC_LOCK_RE = re.compile(
    r"^\d+:\s+(?:->\s+)?FLOCK\s+\S+\s+\S+\s+(-?\d+)\s+"
    r"([0-9A-Fa-f]+):([0-9A-Fa-f]+):(\d+)\s+"
)
_DEFAULT_LOCK = Path("/app/.atri-prixok-bot-v133.lock")


def is_bot_argv(argv: list[str]) -> bool:
    """Best-effort argv matcher retained for V156.1 and diagnostics."""
    if len(argv) < 3:
        return False
    interpreter = Path(argv[0]).name
    if not _PYTHON_BASENAME.fullmatch(interpreter):
        return False
    for index in range(1, len(argv) - 1):
        if argv[index : index + 2] == ["-m", "bot"]:
            return True
    return False


def read_cmdline(path: Path) -> list[str]:
    raw = path.read_bytes()
    return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]


def find_bot_pids(proc_root: Path) -> list[int]:
    matches: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = read_cmdline(entry / "cmdline")
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if is_bot_argv(argv):
            matches.append(int(entry.name))
    return sorted(set(matches))


def lock_identity(lock_file: Path) -> tuple[int, int, int]:
    stat = lock_file.stat()
    return os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino


def find_proc_lock_owner_pids(proc_root: Path, lock_file: Path) -> list[int]:
    """Find FLOCK owners by matching the lock's kernel device/inode identity."""
    major, minor, inode = lock_identity(lock_file)
    locks_path = proc_root / "locks"
    matches: list[int] = []
    try:
        lines = locks_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return []

    for line in lines:
        match = _PROC_LOCK_RE.match(line)
        if match is None:
            continue
        pid_text, major_hex, minor_hex, inode_text = match.groups()
        try:
            pid = int(pid_text)
            item = (int(major_hex, 16), int(minor_hex, 16), int(inode_text))
        except ValueError:
            continue
        if pid > 0 and item == (major, minor, inode):
            matches.append(pid)
    return sorted(set(matches))


def find_fd_lock_owner_pids(proc_root: Path, lock_file: Path) -> list[int]:
    """Find processes with an FD referencing the exact production lock inode."""
    lock_stat = lock_file.stat()
    identity = (lock_stat.st_dev, lock_stat.st_ino)
    matches: list[int] = []

    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        for fd in fds:
            try:
                stat = os.stat(fd)
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            if (stat.st_dev, stat.st_ino) == identity:
                matches.append(int(entry.name))
                break
    return sorted(set(matches))


def read_recorded_pid(lock_file: Path) -> str:
    try:
        return lock_file.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (FileNotFoundError, PermissionError, IndexError, OSError):
        return "unavailable"


def resolve_lock_owner_pid(proc_root: Path, lock_file: Path) -> int:
    """Resolve one kernel-backed owner and reject ambiguous/disagreeing evidence."""
    proc_lock_pids = find_proc_lock_owner_pids(proc_root, lock_file)
    fd_pids = find_fd_lock_owner_pids(proc_root, lock_file)

    if len(proc_lock_pids) > 1 or len(fd_pids) > 1:
        raise RuntimeError(
            f"ambiguous lock owners: proc_locks={proc_lock_pids} fd_owners={fd_pids}"
        )

    if proc_lock_pids and fd_pids and proc_lock_pids != fd_pids:
        raise RuntimeError(
            f"kernel lock owner disagreement: proc_locks={proc_lock_pids} fd_owners={fd_pids}"
        )

    owners = proc_lock_pids or fd_pids
    if len(owners) != 1:
        raise RuntimeError(
            f"requires exactly one kernel lock owner: proc_locks={proc_lock_pids} fd_owners={fd_pids}"
        )

    pid = owners[0]
    if not (proc_root / str(pid)).exists():
        raise RuntimeError(f"resolved lock owner PID is not visible in proc root: {pid}")
    return pid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument("--strategy", choices=("argv", "lock-owner"), default="argv")
    parser.add_argument("--lock-file", default=str(_DEFAULT_LOCK))
    args = parser.parse_args()

    proc_root = Path(args.proc_root)
    if args.strategy == "argv":
        pids = find_bot_pids(proc_root)
        if len(pids) != 1:
            print(
                f"V156.1 production bot PID gate requires exactly one worker; found {len(pids)}: {pids}",
                file=sys.stderr,
            )
            return 1
        print(pids[0])
        return 0

    lock_file = Path(args.lock_file)
    try:
        pid = resolve_lock_owner_pid(proc_root, lock_file)
    except (FileNotFoundError, PermissionError, OSError, RuntimeError) as exc:
        argv_pids = find_bot_pids(proc_root)
        print(
            "V156.3 production kernel-lock PID gate failed: "
            f"{exc}; recorded_pid={read_recorded_pid(lock_file)}; argv_candidates={argv_pids}",
            file=sys.stderr,
        )
        return 1

    print(pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
