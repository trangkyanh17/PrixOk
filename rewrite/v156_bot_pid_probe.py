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
from typing import NamedTuple

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


def proc_visible_self_pid(proc_root: Path) -> int | None:
    """Return this process's PID as represented by the selected procfs mount."""
    try:
        target = os.readlink(proc_root / "self")
        component = Path(target).name
        return int(component) if component.isdigit() else None
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return os.getpid() if proc_root == Path("/proc") else None


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


def find_proc_lock_owner_pids_for_identity(
    proc_root: Path,
    device: int,
    inode: int,
) -> list[int]:
    """Find FLOCK owners for an already-proven device/inode identity."""
    major, minor = os.major(device), os.minor(device)
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


def find_proc_lock_owner_pids(proc_root: Path, lock_file: Path) -> list[int]:
    """Find FLOCK owners by matching the lock's kernel device/inode identity."""
    stat = lock_file.stat()
    return find_proc_lock_owner_pids_for_identity(proc_root, stat.st_dev, stat.st_ino)


def find_fd_lock_owner_pids_for_identity(
    proc_root: Path,
    device: int,
    inode: int,
) -> list[int]:
    """Find processes with an FD referencing an exact device/inode identity."""
    identity = (device, inode)
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


def find_fd_lock_owner_pids(proc_root: Path, lock_file: Path) -> list[int]:
    """Find processes with an FD referencing the exact production lock inode."""
    stat = lock_file.stat()
    return find_fd_lock_owner_pids_for_identity(proc_root, stat.st_dev, stat.st_ino)


def read_recorded_pid(lock_file: Path) -> str:
    try:
        return lock_file.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (FileNotFoundError, PermissionError, IndexError, OSError):
        return "unavailable"


class ProcessIdentity(NamedTuple):
    pid: int
    start_ticks: int
    real_uid: int
    source: str


def read_process_start_ticks(proc_root: Path, pid: int) -> int:
    raw = (proc_root / str(pid) / "stat").read_text(
        encoding="utf-8", errors="replace"
    )
    _, separator, tail = raw.rpartition(")")
    if not separator:
        raise RuntimeError(f"invalid proc stat for PID {pid}")
    fields = tail.strip().split()
    if len(fields) < 20:
        raise RuntimeError(f"short proc stat for PID {pid}")
    return int(fields[19])


def read_process_real_uid(proc_root: Path, pid: int) -> int:
    for line in (proc_root / str(pid) / "status").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise RuntimeError(f"missing real UID for PID {pid}")


def resolve_lock_owner_identity(
    proc_root: Path,
    device: int,
    inode: int,
    *,
    expected_uid: int | None = None,
    require_proc_locks: bool = False,
) -> ProcessIdentity:
    """Resolve one exact lock owner and bind it to PID start-time and UID."""
    proc_lock_pids = find_proc_lock_owner_pids_for_identity(
        proc_root, device, inode
    )
    fd_pids = find_fd_lock_owner_pids_for_identity(proc_root, device, inode)

    if len(proc_lock_pids) > 1:
        raise RuntimeError(
            f"ambiguous lock owners: proc_locks={proc_lock_pids} fd_owners={fd_pids}"
        )

    if require_proc_locks and len(proc_lock_pids) != 1:
        raise RuntimeError(
            "requires exactly one /proc/locks owner: "
            f"proc_locks={proc_lock_pids} fd_owners={fd_pids}"
        )

    if proc_lock_pids and fd_pids and proc_lock_pids[0] not in fd_pids:
        raise RuntimeError(
            f"kernel lock owner disagreement: proc_locks={proc_lock_pids} fd_owners={fd_pids}"
        )

    owners = proc_lock_pids or (fd_pids if len(fd_pids) == 1 else [])
    if len(owners) != 1:
        raise RuntimeError(
            f"requires exactly one kernel lock owner: proc_locks={proc_lock_pids} fd_owners={fd_pids}"
        )

    pid = owners[0]
    if not (proc_root / str(pid)).exists():
        raise RuntimeError(f"resolved lock owner PID is not visible in proc root: {pid}")
    real_uid = read_process_real_uid(proc_root, pid)
    if expected_uid is not None and real_uid != expected_uid:
        raise RuntimeError(
            f"lock owner UID mismatch: pid={pid} uid={real_uid} expected={expected_uid}"
        )
    return ProcessIdentity(
        pid=pid,
        start_ticks=read_process_start_ticks(proc_root, pid),
        real_uid=real_uid,
        source="proc_locks" if proc_lock_pids else "fd",
    )


def resolve_lock_owner_pid(proc_root: Path, lock_file: Path) -> int:
    """Resolve one kernel-backed owner and reject ambiguous/disagreeing evidence."""
    stat = lock_file.stat()
    return resolve_lock_owner_identity(
        proc_root, stat.st_dev, stat.st_ino
    ).pid


def read_process_parent_pid(proc_root: Path, pid: int) -> int:
    raw = (proc_root / str(pid) / "stat").read_text(
        encoding="utf-8", errors="replace"
    )
    _, separator, tail = raw.rpartition(")")
    if not separator:
        raise RuntimeError(f"invalid proc stat for PID {pid}")
    fields = tail.strip().split()
    if len(fields) < 2:
        raise RuntimeError(f"short proc stat for PID {pid}")
    return int(fields[1])


def resolve_child_executable(
    proc_root: Path,
    parent_pid: int,
    executable: Path,
    *,
    expected_uid: int | None = None,
) -> ProcessIdentity:
    expected_executable = executable.resolve()
    matches: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if read_process_parent_pid(proc_root, pid) != parent_pid:
                continue
            actual = (entry / "exe").resolve(strict=True)
            if actual != expected_executable:
                continue
            if expected_uid is not None and read_process_real_uid(proc_root, pid) != expected_uid:
                continue
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, RuntimeError, ValueError):
            continue
        matches.append(pid)
    if len(matches) != 1:
        raise RuntimeError(
            f"requires exactly one direct child executable: parent={parent_pid} "
            f"executable={expected_executable} candidates={matches}"
        )
    pid = matches[0]
    return ProcessIdentity(
        pid=pid,
        start_ticks=read_process_start_ticks(proc_root, pid),
        real_uid=read_process_real_uid(proc_root, pid),
        source="parent_exe",
    )


def find_exact_argument_processes(
    proc_root: Path,
    argument: str,
    *,
    expected_uid: int | None = None,
) -> list[ProcessIdentity]:
    matches: list[ProcessIdentity] = []
    self_pid = proc_visible_self_pid(proc_root)
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if self_pid is not None and pid == self_pid:
            # argv-exact's own --argument value must never make the probe
            # report itself as the process being inspected.
            continue
        try:
            if argument not in read_cmdline(entry / "cmdline"):
                continue
            real_uid = read_process_real_uid(proc_root, pid)
            if expected_uid is not None and real_uid != expected_uid:
                continue
            matches.append(
                ProcessIdentity(
                    pid=pid,
                    start_ticks=read_process_start_ticks(proc_root, pid),
                    real_uid=real_uid,
                    source="exact_argv",
                )
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, RuntimeError, ValueError):
            continue
    return sorted(matches)


def _proot_root_arguments(argv: list[str]) -> list[Path]:
    roots: list[Path] = []
    index = 1
    while index < len(argv):
        value = argv[index]
        if value in {"-r", "-S", "--rootfs"} and index + 1 < len(argv):
            roots.append(Path(argv[index + 1]))
            index += 2
            continue
        for prefix in ("--rootfs=", "-r", "-S"):
            if value.startswith(prefix) and len(value) > len(prefix):
                roots.append(Path(value[len(prefix) :]))
                break
        index += 1
    return roots


def resolve_rootfs_from_proot(
    proc_root: Path,
    device: int,
    inode: int,
    *,
    expected_uid: int | None = None,
) -> Path:
    matches: set[Path] = set()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            argv = read_cmdline(entry / "cmdline")
            if not argv or not Path(argv[0]).name.startswith("proot"):
                continue
            if expected_uid is not None and read_process_real_uid(proc_root, pid) != expected_uid:
                continue
            for candidate in _proot_root_arguments(argv):
                if not candidate.is_absolute():
                    candidate = (entry / "cwd").resolve(strict=True) / candidate
                stat = candidate.stat()
                if (stat.st_dev, stat.st_ino) == (device, inode):
                    matches.add(candidate.resolve())
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError, RuntimeError, ValueError):
            continue
    if len(matches) != 1:
        raise RuntimeError(
            f"requires exactly one proven PRoot rootfs; candidates={sorted(map(str, matches))}"
        )
    return next(iter(matches))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument(
        "--strategy",
        choices=("argv", "lock-owner", "child-exe", "argv-exact", "rootfs"),
        default="argv",
    )
    parser.add_argument("--lock-file", default=str(_DEFAULT_LOCK))
    parser.add_argument("--lock-device", type=int)
    parser.add_argument("--lock-inode", type=int)
    parser.add_argument("--expected-uid", type=int)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--require-proc-locks", action="store_true")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--executable")
    parser.add_argument("--argument")
    parser.add_argument("--root-device", type=int)
    parser.add_argument("--root-inode", type=int)
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

    if args.strategy == "argv-exact":
        if not args.argument:
            parser.error("argv-exact requires --argument")
        try:
            identities = find_exact_argument_processes(
                proc_root,
                args.argument,
                expected_uid=args.expected_uid,
            )
        except (FileNotFoundError, PermissionError, OSError, RuntimeError, ValueError) as exc:
            print(f"exact argv gate failed: {exc}", file=sys.stderr)
            return 1
        for identity in identities:
            print(
                f"{identity.pid}|{identity.start_ticks}|{identity.real_uid}|{identity.source}"
                if args.details
                else identity.pid
            )
        return 0

    if args.strategy == "child-exe":
        if args.parent_pid is None or not args.executable:
            parser.error("child-exe requires --parent-pid and --executable")
        try:
            identity = resolve_child_executable(
                proc_root,
                args.parent_pid,
                Path(args.executable),
                expected_uid=args.expected_uid,
            )
        except (FileNotFoundError, PermissionError, OSError, RuntimeError, ValueError) as exc:
            print(f"exact child executable gate failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"{identity.pid}|{identity.start_ticks}|{identity.real_uid}|{identity.source}"
            if args.details
            else identity.pid
        )
        return 0

    if args.strategy == "rootfs":
        if args.root_device is None or args.root_inode is None:
            parser.error("rootfs requires --root-device and --root-inode")
        try:
            print(
                resolve_rootfs_from_proot(
                    proc_root,
                    args.root_device,
                    args.root_inode,
                    expected_uid=args.expected_uid,
                )
            )
        except (FileNotFoundError, PermissionError, OSError, RuntimeError, ValueError) as exc:
            print(f"exact PRoot rootfs gate failed: {exc}", file=sys.stderr)
            return 1
        return 0

    lock_file = Path(args.lock_file)
    try:
        if (args.lock_device is None) != (args.lock_inode is None):
            parser.error("lock identity requires both --lock-device and --lock-inode")
        if args.lock_device is not None:
            identity = resolve_lock_owner_identity(
                proc_root,
                args.lock_device,
                args.lock_inode,
                expected_uid=args.expected_uid,
                require_proc_locks=args.require_proc_locks,
            )
        else:
            stat = lock_file.stat()
            identity = resolve_lock_owner_identity(
                proc_root,
                stat.st_dev,
                stat.st_ino,
                expected_uid=args.expected_uid,
                require_proc_locks=args.require_proc_locks,
            )
    except (FileNotFoundError, PermissionError, OSError, RuntimeError) as exc:
        argv_pids = find_bot_pids(proc_root)
        print(
            "V156.3 production kernel-lock PID gate failed: "
            f"{exc}; recorded_pid={read_recorded_pid(lock_file)}; argv_candidates={argv_pids}",
            file=sys.stderr,
        )
        return 1

    print(
        f"{identity.pid}|{identity.start_ticks}|{identity.real_uid}|{identity.source}"
        if args.details
        else identity.pid
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
