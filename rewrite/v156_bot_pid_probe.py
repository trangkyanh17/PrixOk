#!/usr/bin/env python3
"""Resolve the single production Atri bot worker PID.

V156.2 treats the V133 singleton lock as the authoritative production identity:
the bot writes its own PID into the file immediately after acquiring the flock.
If that held-lock contract is unavailable, fall back to a conservative semantic
scan for Python ``-m bot`` invocations.
"""

from __future__ import annotations

import argparse
import fcntl
import re
import sys
from pathlib import Path

DEFAULT_LOCK_PATH = Path("/app/.atri-prixok-bot-v133.lock")
_PYTHON_BASENAME = re.compile(r"^python(?:3(?:\.\d+)*)?$")
_NO_VALUE_OPTIONS = {
    "-b",
    "-bb",
    "-B",
    "-d",
    "-E",
    "-i",
    "-I",
    "-O",
    "-OO",
    "-P",
    "-q",
    "-s",
    "-S",
    "-u",
    "-v",
}
_VALUE_OPTIONS = {"-W", "-X"}


def is_bot_argv(argv: list[str]) -> bool:
    """Recognize Python CLI forms whose selected module is exactly ``bot``."""
    if len(argv) < 3 or not _PYTHON_BASENAME.fullmatch(Path(argv[0]).name):
        return False

    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "-m":
            return index + 1 < len(argv) and argv[index + 1] == "bot"
        if arg in _NO_VALUE_OPTIONS:
            index += 1
            continue
        if arg in _VALUE_OPTIONS:
            index += 2
            continue
        if (arg.startswith("-W") or arg.startswith("-X")) and len(arg) > 2:
            index += 1
            continue
        return False
    return False


def read_cmdline(path: Path) -> list[str]:
    raw = path.read_bytes()
    return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]


def find_bot_pids(proc_root: Path) -> list[int]:
    matches: list[int] = []
    try:
        entries = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return matches
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            argv = read_cmdline(entry / "cmdline")
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if is_bot_argv(argv):
            matches.append(int(entry.name))
    return sorted(matches)


def held_lock_pid(lock_path: Path, proc_root: Path) -> tuple[bool, int | None]:
    """Return ``(held, pid)`` for the V133 singleton lock.

    A held lock with malformed/stale PID content returns ``(True, None)`` so the
    caller fails closed instead of silently falling back to process guessing.
    """
    try:
        handle = lock_path.open("r+", encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return False, None

    with handle:
        handle.seek(0)
        raw = handle.read().strip()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            held = True
        else:
            held = False
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    if not held:
        return False, None
    if not raw.isdigit():
        return True, None
    pid = int(raw)
    if pid <= 1 or not (proc_root / str(pid)).is_dir():
        return True, None
    return True, pid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))
    args = parser.parse_args()

    proc_root = Path(args.proc_root)
    lock_path = Path(args.lock_path)
    lock_held, lock_pid = held_lock_pid(lock_path, proc_root)
    if lock_held:
        if lock_pid is None:
            print(
                "V156.2 singleton lock is held but its PID contract is invalid",
                file=sys.stderr,
            )
            return 1
        print(lock_pid)
        return 0

    pids = find_bot_pids(proc_root)
    if len(pids) != 1:
        print(
            f"V156.2 production bot PID gate requires exactly one worker; found {len(pids)}: {pids}",
            file=sys.stderr,
        )
        return 1
    print(pids[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
