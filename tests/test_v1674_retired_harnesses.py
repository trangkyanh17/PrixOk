import subprocess
from pathlib import Path

import pytest


SCRIPTS = (
    Path("rewrite/termux-v1674-main-final-canary.sh"),
    Path("rewrite/termux-v1674-live-recovery-final.sh"),
)


@pytest.mark.parametrize("script", SCRIPTS)
def test_v1674_unsafe_harness_is_a_final_recovery_compatibility_entry(script: Path):
    source = script.read_text(encoding="utf-8")

    assert 'FINAL_RECOVERY="$SCRIPT_DIR/termux-atri-final-recovery.sh"' in source
    assert 'exec bash "$FINAL_RECOVERY" "$@"' in source
    assert "kill-session" not in source
    assert ".atri-prixok-bot-v133.lock" not in source

    completed = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_v1674_compatibility_entry_delegates_self_test(script: Path):
    completed = subprocess.run(
        ["bash", str(script), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "termux atri final recovery self-test: PASS" in completed.stdout
