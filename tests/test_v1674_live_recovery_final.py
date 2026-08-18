import subprocess
from pathlib import Path


SCRIPT = Path("rewrite/termux-v1674-live-recovery-final.sh")


def test_live_recovery_is_retired_into_the_final_lifecycle_transaction():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'FINAL_RECOVERY="$SCRIPT_DIR/termux-atri-final-recovery.sh"' in source
    assert 'exec bash "$FINAL_RECOVERY" "$@"' in source
    assert "kill-session" not in source


def test_live_recovery_compatibility_self_test_uses_final_contract():
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "termux atri final recovery self-test: PASS" in completed.stdout
