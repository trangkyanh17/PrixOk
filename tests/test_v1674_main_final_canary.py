import re
import subprocess
from pathlib import Path

SCRIPT = Path("rewrite/termux-v1674-main-final-canary.sh")


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_v1674_main_canary_self_test_passes():
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "v1674 main canary self-test: PASS" in completed.stdout


def test_v1674_main_canary_requires_exact_clean_origin_main():
    source = _source()
    assert 'EXPECTED_BRANCH="main"' in source
    assert "git fetch --quiet origin main" in source
    assert "git rev-parse origin/main" in source
    assert '[[ "$branch" == "$EXPECTED_BRANCH" ]]' in source
    assert '"$head" == "$origin_main"' in source
    assert "git diff --quiet" in source
    assert "git diff --cached --quiet" in source
    assert not re.search(r"git\s+(pull|reset|checkout|clean)\b", source)


def test_v1674_main_canary_checks_the_real_incident_contract():
    source = _source()
    for marker in (
        "in_memory=False",
        "await start_bot_client(cls.bot, LOGGER)",
        "TELEGRAM_BOT_START_FLOOD_WAIT",
        "PERSISTENT_SESSION=PASS",
        "NO_RESTART_STORM=PASS",
        "BOT_SESSION_RECOVERY=PASS",
        "SUPERVISOR_RECOVERY=PASS",
        "POST_STABILITY=PASS",
        "ATRI_V1674_MAIN_FINAL_CANARY=PASS",
    ):
        assert marker in source


def test_v1674_main_canary_does_not_delete_or_force_unlock_bot_singleton():
    source = _source()
    assert ".atri-prixok-bot-v133.lock" in source
    assert "wait_lock_released" in source
    assert not re.search(r"rm\s+[^\n]*\.atri-prixok-bot[^\n]*lock", source)
    assert "flock -n 9" in source


def test_v1674_main_canary_has_ten_round_default_and_real_recovery_actions():
    source = _source()
    assert 'STABILITY_ROUNDS="${ATRI_V1674_TEST_ROUNDS:-10}"' in source
    assert 'host_run "tmux kill-session -t prixok-bot"' in source
    assert 'host_run "kill -TERM \'$old_sup\'"' in source
    assert "supervisor crash unnecessarily restarted healthy bot" in source
