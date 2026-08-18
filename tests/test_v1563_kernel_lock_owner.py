from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "rewrite/v156_bot_pid_probe.py"
WRAPPER_PATH = ROOT / "rewrite/termux-v1563-performance-canary.sh"
BASE_CANARY_PATH = ROOT / "rewrite/termux-v156-performance-canary.sh"


def _load_probe():
    spec = importlib.util.spec_from_file_location("v1563_bot_pid_probe_test", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _proc_entry(
    proc_root: Path,
    pid: int,
    argv: list[str] | None = None,
    *,
    parent_pid: int = 1,
    start_ticks: int = 100,
    uid: int | None = None,
) -> Path:
    proc = proc_root / str(pid)
    (proc / "fd").mkdir(parents=True)
    stat_tail = ["S", str(parent_pid), *("0" for _ in range(17)), str(start_ticks)]
    (proc / "stat").write_text(
        f"{pid} (worker) {' '.join(stat_tail)}\n", encoding="utf-8"
    )
    real_uid = os.getuid() if uid is None else uid
    (proc / "status").write_text(
        f"Name:\tworker\nUid:\t{real_uid}\t{real_uid}\t{real_uid}\t{real_uid}\n",
        encoding="utf-8",
    )
    if argv is not None:
        (proc / "cmdline").write_bytes(
            b"\0".join(item.encode() for item in argv) + b"\0"
        )
    return proc


def _write_proc_lock(proc_root: Path, lock: Path, pid: int, lock_id: int = 1) -> None:
    stat = lock.stat()
    line = (
        f"{lock_id}: FLOCK  ADVISORY  WRITE {pid} "
        f"{os.major(stat.st_dev):x}:{os.minor(stat.st_dev):x}:{stat.st_ino} 0 EOF\n"
    )
    path = proc_root / "locks"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _attach_fd(proc: Path, fd: int, target: Path) -> None:
    (proc / "fd" / str(fd)).symlink_to(target)


def test_v1563_proc_locks_resolves_owner_even_when_recorded_pid_is_wrong(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("99999999\n", encoding="utf-8")

    _proc_entry(proc_root, 301, ["proot-wrapper", "opaque-worker"])
    _write_proc_lock(proc_root, lock, 301)

    assert probe.find_proc_lock_owner_pids(proc_root, lock) == [301]
    assert probe.resolve_lock_owner_pid(proc_root, lock) == 301


def test_v1563_fd_inode_scan_is_kernel_identity_fallback(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("not-a-usable-pid\n", encoding="utf-8")

    owner = _proc_entry(proc_root, 401, ["proot", "opaque"])
    _attach_fd(owner, 9, lock)
    _attach_fd(owner, 10, lock)

    assert probe.find_proc_lock_owner_pids(proc_root, lock) == []
    assert probe.find_fd_lock_owner_pids(proc_root, lock) == [401]
    assert probe.resolve_lock_owner_pid(proc_root, lock) == 401


def test_v1563_destructive_gate_requires_proc_locks_not_fd_fallback(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("guest-pid\n", encoding="utf-8")
    owner = _proc_entry(proc_root, 411, ["proot", "opaque"])
    _attach_fd(owner, 9, lock)

    with pytest.raises(RuntimeError, match="/proc/locks owner"):
        probe.resolve_lock_owner_identity(
            proc_root,
            lock.stat().st_dev,
            lock.stat().st_ino,
            expected_uid=os.getuid(),
            require_proc_locks=True,
        )


def test_v1563_cross_check_rejects_kernel_source_disagreement(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("501\n", encoding="utf-8")

    _proc_entry(proc_root, 501, ["opaque-a"])
    other = _proc_entry(proc_root, 502, ["opaque-b"])
    _write_proc_lock(proc_root, lock, 501)
    _attach_fd(other, 8, lock)

    with pytest.raises(RuntimeError, match="disagreement"):
        probe.resolve_lock_owner_pid(proc_root, lock)


def test_v1563_rejects_multiple_kernel_lock_owners(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("601\n", encoding="utf-8")

    _proc_entry(proc_root, 601, ["opaque-a"])
    _proc_entry(proc_root, 602, ["opaque-b"])
    _write_proc_lock(proc_root, lock, 601, 1)
    _write_proc_lock(proc_root, lock, 602, 2)

    with pytest.raises(RuntimeError, match="ambiguous"):
        probe.resolve_lock_owner_pid(proc_root, lock)


def test_v1563_lock_owner_cli_uses_kernel_owner_not_argv_or_recorded_pid(tmp_path: Path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("123\n", encoding="utf-8")

    _proc_entry(proc_root, 701, ["proot-wrapper", "not-python-looking"])
    _write_proc_lock(proc_root, lock, 701)

    result = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--strategy",
            "lock-owner",
            "--proc-root",
            str(proc_root),
            "--lock-file",
            str(lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "701"


def test_v1563_identity_cli_binds_owner_to_host_pid_start_time_and_uid(tmp_path: Path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("guest-pid-is-not-host-pid\n", encoding="utf-8")
    _proc_entry(proc_root, 711, ["opaque-proot-worker"], start_ticks=9876)
    _write_proc_lock(proc_root, lock, 711)
    stat = lock.stat()

    result = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--strategy",
            "lock-owner",
            "--proc-root",
            str(proc_root),
            "--lock-device",
            str(stat.st_dev),
            "--lock-inode",
            str(stat.st_ino),
            "--expected-uid",
            str(os.getuid()),
            "--details",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"711|9876|{os.getuid()}|proc_locks"


def test_v1563_identity_gate_rejects_wrong_uid(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("1\n", encoding="utf-8")
    _proc_entry(proc_root, 721, uid=os.getuid() + 1)
    _write_proc_lock(proc_root, lock, 721)

    with pytest.raises(RuntimeError, match="UID mismatch"):
        probe.resolve_lock_owner_identity(
            proc_root,
            lock.stat().st_dev,
            lock.stat().st_ino,
            expected_uid=os.getuid(),
        )


def test_v1563_exact_child_executable_uses_parent_exe_and_start_time(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    executable = tmp_path / "atri-supervisor"
    executable.write_text("binary", encoding="utf-8")
    child = _proc_entry(
        proc_root,
        731,
        [str(executable)],
        parent_pid=730,
        start_ticks=4444,
    )
    (child / "exe").symlink_to(executable)

    identity = probe.resolve_child_executable(
        proc_root,
        730,
        executable,
        expected_uid=os.getuid(),
    )
    assert identity == probe.ProcessIdentity(731, 4444, os.getuid(), "parent_exe")


def test_v1563_exact_argv_scan_does_not_use_substring_matching(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    target = "/data/data/com.termux/files/home/atri-production-watchdog.sh"
    _proc_entry(proc_root, 735, ["bash", target], start_ticks=11)
    _proc_entry(proc_root, 736, ["bash", target + ".backup"], start_ticks=12)

    assert probe.find_exact_argument_processes(
        proc_root,
        target,
        expected_uid=os.getuid(),
    ) == [probe.ProcessIdentity(735, 11, os.getuid(), "exact_argv")]


def test_v1563_rootfs_is_derived_from_live_proot_argv_and_inode(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    rootfs = tmp_path / "dynamic-container" / "rootfs"
    rootfs.mkdir(parents=True)
    _proc_entry(
        proc_root,
        741,
        ["/data/data/com.termux/files/usr/bin/proot", "--rootfs", str(rootfs)],
    )

    resolved = probe.resolve_rootfs_from_proot(
        proc_root,
        rootfs.stat().st_dev,
        rootfs.stat().st_ino,
        expected_uid=os.getuid(),
    )
    assert resolved == rootfs.resolve()


def test_v1563_failure_reports_recorded_pid_and_argv_only_as_diagnostics(tmp_path: Path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("888\n", encoding="utf-8")
    _proc_entry(proc_root, 801, ["python3", "-m", "bot"])

    result = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--strategy",
            "lock-owner",
            "--proc-root",
            str(proc_root),
            "--lock-file",
            str(lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "recorded_pid=888" in result.stderr
    assert "argv_candidates=[801]" in result.stderr


def test_v1563_preserves_v1561_default_argv_strategy(tmp_path: Path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    proc = _proc_entry(proc_root, 901, ["/app/mltbenv/bin/python3", "-X", "dev", "-m", "bot"])
    assert proc.exists()

    result = subprocess.run(
        [sys.executable, str(PROBE_PATH), "--proc-root", str(proc_root)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "901"


def test_v1563_wrapper_patches_only_pid_detector_and_self_tests():
    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
    base = BASE_CANARY_PATH.read_text(encoding="utf-8")

    assert base.count("production_bot_pid() {") == 1
    assert "pgrep -f '[p]ython3 -m bot'" in base
    assert "--strategy lock-owner" in wrapper
    assert "/app/.atri-prixok-bot-v133.lock" in wrapper
    assert "termux-v1562-performance-canary.sh" not in wrapper

    syntax = subprocess.run(
        ["bash", "-n", str(WRAPPER_PATH)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    env = os.environ.copy()
    env.setdefault("TMPDIR", "/tmp")
    self_test = subprocess.run(
        ["bash", str(WRAPPER_PATH), "--self-test"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert "kernel-lock PID self-test: PASS" in self_test.stdout
