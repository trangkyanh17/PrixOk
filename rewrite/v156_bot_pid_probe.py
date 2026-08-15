#!/usr/bin/env python3
"""Resolve the single production bot worker from /proc.

V156.2 uses ownership of the production lock file as the primary identity
signal. This avoids relying on argv rendering inside Termux/proot, where the
same Python worker may appear behind wrapper-specific command lines.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_PYTHON_BASENAME = re.compile(r"^python(?:3(?:\.\d+)*)?$")
_DEFAULT_LOCK = Path("/app/.atri-prixok-bot-v133.lock")


def is_bot_argv(argv: list[str]) -> bool:
    """Best-effort diagnostic matcher retained from V156.1."""
    if len(argv) < 3:
        return False
    interpreter = Path(argv[0]).name
    return bool(_PYTHON_BASENAME.fullmatch(interpreter)) and argv[1:3] == ["-m", "bot"]


def read_cmdline(path: Path) -> list[str]:
    raw = path.read_bytes()
    return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]


def find_bot_pids(proc_root: Path) -> list[int]:
    """Return argv-matching bot PIDs for diagnostics only."""
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
    return sorted(matches)


def find_lock_owner_pids(proc_root: Path, lock_file: Path) -> list[int]:
    """Return PIDs with an open FD referencing the exact lock inode.

    Comparing st_dev/st_ino through /proc/<pid>/fd is robust across proot path
    rewriting because the kernel FD still references the same underlying file.
    Multiple FDs held by one PID are deduplicated.
    """
    lock_stat = lock_file.stat()
    lock_identity = (lock_stat.st_dev, lock_stat.st_ino)
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
            if (stat.st_dev, stat.st_ino) == lock_identity:
                matches.append(int(entry.name))
                break

    return sorted(matches)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument("--lock-file", default=str(_DEFAULT_LOCK))
    args = parser.parse_args()

    proc_root = Path(args.proc_root)
    lock_file = Path(args.lock_file)
    try:
        pids = find_lock_owner_pids(proc_root, lock_file)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        print(f"V156.2 production lock PID gate cannot stat lock file {lock_file}: {exc}", file=sys.stderr)
        return 1

    if len(pids) != 1:
        argv_pids = find_bot_pids(proc_root)
        print(
            "V156.2 production lock PID gate requires exactly one lock owner; "
            f"found {len(pids)}: {pids}; argv_candidates={argv_pids}",
            file=sys.stderr,
        )
        return 1

    print(pids[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
