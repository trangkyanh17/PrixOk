#!/usr/bin/env python3
"""Resolve the single production `python -m bot` worker from /proc.

V156.1 intentionally parses argv instead of using a pgrep string so the
production gate accepts python, python3, and versioned/path interpreters while
still refusing zero or multiple bot workers.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_PYTHON_BASENAME = re.compile(r"^python(?:3(?:\.\d+)*)?$")


def is_bot_argv(argv: list[str]) -> bool:
    if len(argv) < 3:
        return False
    interpreter = Path(argv[0]).name
    return bool(_PYTHON_BASENAME.fullmatch(interpreter)) and argv[1:3] == ["-m", "bot"]


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
    return sorted(matches)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc-root", default="/proc")
    args = parser.parse_args()

    pids = find_bot_pids(Path(args.proc_root))
    if len(pids) != 1:
        print(
            f"V156.1 production bot PID gate requires exactly one worker; found {len(pids)}: {pids}",
            file=sys.stderr,
        )
        return 1
    print(pids[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
