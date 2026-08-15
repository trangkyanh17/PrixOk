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
    spec = importlib.util.spec_from_file_location("v1561_bot_pid_probe_test", PROBE_PATH)
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
def test_v1561_accepts_realistic_bot_interpreters(argv: list[str]):
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
def test_v1561_rejects_unrelated_processes(argv: list[str]):
    probe = _load_probe()
    assert probe.is_bot_argv(argv) is False


def _write_proc(root: Path, pid: int, argv: list[str]) -> None:
    proc = root / str(pid)
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"\0".join(item.encode() for item in argv) + b"\0")


def test_v1561_fake_proc_requires_exactly_one_worker(tmp_path: Path):
    probe = _load_probe()
    _write_proc(tmp_path, 101, ["/app/mltbenv/bin/python", "-m", "bot"])
    _write_proc(tmp_path, 102, ["python3", "worker.py"])
    assert probe.find_bot_pids(tmp_path) == [101]

    _write_proc(tmp_path, 103, ["python3.14", "-m", "bot"])
    assert probe.find_bot_pids(tmp_path) == [101, 103]


def test_v1561_cli_success_and_multiple_worker_failure(tmp_path: Path):
    _write_proc(tmp_path, 201, ["python", "-m", "bot"])
    ok = subprocess.run(
        [sys.executable, str(PROBE_PATH), "--proc-root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert ok.returncode == 0
    assert ok.stdout.strip() == "201"

    _write_proc(tmp_path, 202, ["/app/mltbenv/bin/python3", "-m", "bot"])
    bad = subprocess.run(
        [sys.executable, str(PROBE_PATH), "--proc-root", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode == 1
    assert "requires exactly one worker" in bad.stderr


def test_v1561_wrapper_patches_only_pid_detector_and_self_tests():
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
