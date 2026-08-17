import re
import subprocess
from pathlib import Path

SCRIPT = Path("rewrite/termux-v1674-live-recovery-final.sh")


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_live_recovery_self_test_passes():
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "atri v1674 live recovery self-test: PASS" in completed.stdout


def test_live_recovery_follows_app_symlink_for_persistent_session():
    text = source()
    assert "find -L /app -maxdepth 1" in text
    assert "persistent session not found through symlink-safe find -L /app" in text


def test_live_recovery_is_not_another_source_cutover():
    text = source()
    assert not re.search(r"git\s+(pull|reset|checkout|clean)\b", text)
    assert "stash pop" not in text
    assert not re.search(r"rm\s+[^\n]*\.atri-prixok-bot[^\n]*lock", text)
    assert 'tmux kill-session -t "$WATCH_SESSION"' not in text


def test_live_recovery_exercises_bot_and_supervisor_recovery_once():
    text = source()
    assert 'tmux kill-session -t "$BOT_SESSION"' in text
    assert 'kill -TERM "$old_sup"' in text
    assert "SUPERVISOR_START pid=" in text
    assert "BOT_SESSION_RECOVERY=" in text
    assert "SUPERVISOR_RECOVERY=" in text
    assert "ATRI_V1674_LIVE_RECOVERY=PASS" in text


def test_live_recovery_has_ten_round_pre_and_post_stability():
    text = source()
    assert 'STABILITY_ROUNDS="${ATRI_V1674_TEST_ROUNDS:-10}"' in text
    assert "PRE_STABILITY=" in text
    assert "POST_STABILITY=" in text
    assert text.count("for ((i=1;i<=STABILITY_ROUNDS;i++))") == 2


def test_supervisor_pid_resolution_uses_logged_marker_and_liveness_check():
    text = source()
    assert "SUPERVISOR_START pid=[0-9]+" in text
    assert 'kill -0 "$pid"' in text
    assert "atri-supervisor" in text
