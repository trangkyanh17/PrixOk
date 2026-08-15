from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "rewrite/termux-v1562-performance-canary.sh"
BASE_CANARY = ROOT / "rewrite/termux-v156-performance-canary.sh"
BOT_MAIN = ROOT / "bot/__main__.py"


def test_v1562_uses_worker_written_singleton_lock_as_pid_source():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    main = BOT_MAIN.read_text(encoding="utf-8")

    assert "/app/.atri-prixok-bot-v133.lock" in wrapper
    assert 'kill -0 "$pid"' in wrapper
    assert "v156_bot_pid_probe.py" not in wrapper

    # The lock is not a guessed side channel: the production worker itself
    # acquires it and writes os.getpid() into it after successful flock().
    assert '_ATRI_V133_LOCK_PATH = _AtriV133Path("/app/.atri-prixok-bot-v133.lock")' in main
    assert "_atri_v133_fcntl.flock(" in main
    assert "_ATRI_V133_LOCK_HANDLE.write(str(_atri_v133_os.getpid()) + \"\\n\")" in main


def test_v1562_wrapper_replaces_only_base_pid_detector_and_self_tests():
    wrapper = WRAPPER.read_text(encoding="utf-8")
    base = BASE_CANARY.read_text(encoding="utf-8")

    assert base.count("production_bot_pid() {") == 1
    assert "pgrep -f '[p]ython3 -m bot'" in base
    assert "production_bot_pid() {" in wrapper
    assert "legacy strict PID matcher survived patch" in wrapper
    assert "argv-based PID probe survived patch" in wrapper

    syntax = subprocess.run(
        ["bash", "-n", str(WRAPPER)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    env = os.environ.copy()
    env.setdefault("TMPDIR", "/tmp")
    self_test = subprocess.run(
        ["bash", str(WRAPPER), "--self-test"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert self_test.returncode == 0, self_test.stdout + self_test.stderr
    assert "lock-PID hotfix self-test: PASS" in self_test.stdout


def test_v1562_lock_pid_recipe_accepts_live_pid_and_rejects_dead_pid(tmp_path: Path):
    lock = tmp_path / "worker.lock"
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")

    live = subprocess.run(
        [
            "bash",
            "-lc",
            'set -Eeuo pipefail; IFS= read -r pid <"$1"; [[ "$pid" =~ ^[0-9]+$ ]]; kill -0 "$pid"; printf "%s\\n" "$pid"',
            "bash",
            str(lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert live.returncode == 0, live.stderr
    assert live.stdout.strip() == str(os.getpid())

    lock.write_text("99999999\n", encoding="utf-8")
    dead = subprocess.run(
        [
            "bash",
            "-lc",
            'set -Eeuo pipefail; IFS= read -r pid <"$1"; [[ "$pid" =~ ^[0-9]+$ ]]; kill -0 "$pid"',
            "bash",
            str(lock),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert dead.returncode != 0
