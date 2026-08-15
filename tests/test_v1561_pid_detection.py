from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "rewrite/v156_bot_pid_probe.py"
WRAPPER_PATH = ROOT / "rewrite/termux-v1561-performance-canary.sh"
BASE_CANARY_PATH = ROOT / "rewrite/termux-v156-performance-canary.sh"


def _load_probe():
    spec = importlib.util.spec_from_file_location("v1562_bot_pid_probe_test", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "argv",
    [
        ["python", "-m", "bot"],
        ["python3", "-m", "bot"],
        ["python3.14", "-m", "bot"],
        ["/app/mltbenv/bin/python", "-m", "bot"],
        ["/app/mltbenv/bin/python3", "-m", "bot", "--flag"],
    ],
)
def test_v1562_keeps_argv_matcher_for_diagnostics(argv: list[str]):
    probe = _load_probe()
    assert probe.is_bot_argv(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        ["python", "bot.py"],
        ["python3", "-m", "pytest"],
        ["bash", "-m", "bot"],
        ["python-helper", "-m", "bot"],
        ["python3", "-m", "bot_helper"],
    ],
)
def test_v1562_argv_diagnostic_rejects_unrelated_processes(argv: list[str]):
    probe = _load_probe()
    assert probe.is_bot_argv(argv) is False


def _write_proc(root: Path, pid: int, argv: list[str]) -> Path:
    proc = root / str(pid)
    (proc / "fd").mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"\0".join(item.encode() for item in argv) + b"\0")
    return proc


def _attach_fd(proc: Path, fd: int, target: Path) -> None:
    (proc / "fd" / str(fd)).symlink_to(target)


def test_v1562_lock_owner_identity_ignores_proot_argv_shape(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("locked\n", encoding="utf-8")

    owner = _write_proc(proc_root, 101, ["proot", "--link2symlink", "/app/mltbenv/bin/python", "-m", "bot"])
    _attach_fd(owner, 9, lock)
    _attach_fd(owner, 10, lock)  # one PID with multiple lock FDs must deduplicate
    other = tmp_path / "other.file"
    other.write_text("other\n", encoding="utf-8")
    unrelated = _write_proc(proc_root, 102, ["python3", "worker.py"])
    _attach_fd(unrelated, 4, other)

    assert probe.find_lock_owner_pids(proc_root, lock) == [101]


def test_v1562_lock_owner_gate_fails_closed_on_multiple_owners(tmp_path: Path):
    probe = _load_probe()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("locked\n", encoding="utf-8")

    first = _write_proc(proc_root, 201, ["proot", "python", "-m", "bot"])
    second = _write_proc(proc_root, 202, ["/app/mltbenv/bin/python3", "-m", "bot"])
    _attach_fd(first, 9, lock)
    _attach_fd(second, 9, lock)
    assert probe.find_lock_owner_pids(proc_root, lock) == [201, 202]


def test_v1562_cli_uses_lock_owner_not_cmdline(tmp_path: Path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    lock = tmp_path / "production.lock"
    lock.write_text("locked\n", encoding="utf-8")

    owner = _write_proc(proc_root, 301, ["proot-wrapper", "opaque-argv"])
    _attach_fd(owner, 11, lock)
    ok = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--proc-root",
            str(proc_root),
            "--lock-file",
            str(lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert ok.returncode == 0
    assert ok.stdout.strip() == "301"

    second = _write_proc(proc_root, 302, ["python3", "-m", "bot"])
    _attach_fd(second, 8, lock)
    bad = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--proc-root",
            str(proc_root),
            "--lock-file",
            str(lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode == 1
    assert "requires exactly one lock owner" in bad.stderr
    assert "argv_candidates=[302]" in bad.stderr


def test_v1562_cli_missing_lock_fails_closed(tmp_path: Path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--proc-root",
            str(proc_root),
            "--lock-file",
            str(tmp_path / "missing.lock"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "cannot stat lock file" in result.stderr


def test_v1562_wrapper_patches_only_pid_detector_and_self_tests():
    source = WRAPPER_PATH.read_text(encoding="utf-8")
    assert "production_bot_pid() {" in source
    assert "v156_bot_pid_probe.py" in source
    assert "legacy strict PID matcher survived patch" in source
    assert "pgrep -f '[p]ython3 -m bot'" in BASE_CANARY_PATH.read_text(encoding="utf-8")

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
    assert "PID hotfix self-test: PASS" in self_test.stdout
