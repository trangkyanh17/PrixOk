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
        ["python3", "-u", "-m", "bot"],
        ["python3", "-OO", "-X", "dev", "-m", "bot"],
        ["python3", "-Wignore", "-m", "bot"],
    ],
)
def test_v1562_accepts_realistic_bot_interpreters(argv: list[str]):
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
        ["python3", "script.py", "-m", "bot"],
        ["python3", "--", "-m", "bot"],
    ],
)
def test_v1562_rejects_unrelated_processes(argv: list[str]):
    probe = _load_probe()
    assert probe.is_bot_argv(argv) is False


def _write_proc(root: Path, pid: int, argv: list[str]) -> None:
    proc = root / str(pid)
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"\0".join(item.encode() for item in argv) + b"\0")


def test_v1562_fake_proc_requires_exactly_one_worker(tmp_path: Path):
    probe = _load_probe()
    _write_proc(tmp_path, 101, ["/app/mltbenv/bin/python", "-u", "-m", "bot"])
    _write_proc(tmp_path, 102, ["python3", "worker.py"])
    assert probe.find_bot_pids(tmp_path) == [101]

    _write_proc(tmp_path, 103, ["python3.14", "-OO", "-m", "bot"])
    assert probe.find_bot_pids(tmp_path) == [101, 103]


def test_v1562_cli_fallback_success_and_multiple_worker_failure(tmp_path: Path):
    missing_lock = tmp_path / "missing.lock"
    _write_proc(tmp_path, 201, ["python", "-u", "-m", "bot"])
    ok = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--proc-root",
            str(tmp_path),
            "--lock-path",
            str(missing_lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert ok.returncode == 0
    assert ok.stdout.strip() == "201"

    _write_proc(tmp_path, 202, ["/app/mltbenv/bin/python3", "-m", "bot"])
    bad = subprocess.run(
        [
            sys.executable,
            str(PROBE_PATH),
            "--proc-root",
            str(tmp_path),
            "--lock-path",
            str(missing_lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode == 1
    assert "requires exactly one worker" in bad.stderr


def _spawn_lock_holder(lock_path: Path, payload: str) -> subprocess.Popen[str]:
    code = r'''
import fcntl
import os
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
payload = sys.argv[2]
with path.open("a+", encoding="utf-8") as handle:
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()) if payload == "PID" else payload)
    handle.write("\n")
    handle.flush()
    print(os.getpid(), flush=True)
    time.sleep(30)
'''
    return subprocess.Popen(
        [sys.executable, "-c", code, str(lock_path), payload],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_v1562_held_singleton_lock_is_authoritative(tmp_path: Path):
    lock_path = tmp_path / "bot.lock"
    holder = _spawn_lock_holder(lock_path, "PID")
    try:
        assert holder.stdout is not None
        holder_pid = int(holder.stdout.readline().strip())
        result = subprocess.run(
            [
                sys.executable,
                str(PROBE_PATH),
                "--proc-root",
                "/proc",
                "--lock-path",
                str(lock_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(holder_pid)
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_v1562_held_lock_with_invalid_pid_fails_closed(tmp_path: Path):
    lock_path = tmp_path / "bot.lock"
    holder = _spawn_lock_holder(lock_path, "not-a-pid")
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip().isdigit()
        result = subprocess.run(
            [
                sys.executable,
                str(PROBE_PATH),
                "--proc-root",
                "/proc",
                "--lock-path",
                str(lock_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1
        assert "lock is held" in result.stderr
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_v1562_free_lock_is_not_trusted(tmp_path: Path):
    probe = _load_probe()
    lock_path = tmp_path / "bot.lock"
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    held, pid = probe.held_lock_pid(lock_path, Path("/proc"))
    assert held is False
    assert pid is None


def test_v1561_wrapper_uses_pid_probe_and_self_tests():
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
