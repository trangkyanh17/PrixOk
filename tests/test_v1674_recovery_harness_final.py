from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "rewrite/termux-v1674-live-recovery-v2.sh"
RESCUE = ROOT / "rewrite/termux-v1674-orphan-rescue.sh"


def _bash_n(path: Path) -> None:
    result = subprocess.run(
        ["bash", "-n", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _self_test(path: Path) -> None:
    result = subprocess.run(
        ["bash", str(path), "--self-test"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "self-test: PASS" in result.stdout


def test_live_recovery_v2_is_nounset_safe_and_does_not_orphan_worker() -> None:
    text = LIVE.read_text(encoding="utf-8")
    _bash_n(LIVE)
    _self_test(LIVE)

    assert 'tmux send-keys -t "$BOT_SESSION" C-c' in text
    assert 'tmux kill-session -t "$BOT_SESSION"' not in text
    assert 'local name="$1" timeout="$2" deadline=' not in text
    assert 'local timeout="$1" deadline=' not in text
    assert 'local old="$1" timeout="$2" deadline=' not in text
    assert "find -L /app" in text
    assert "persistent-session=PASS" in text
    assert "no-FloodWait=PASS" in text
    assert "refusing destructive fallback" in text


def test_orphan_rescue_resolves_both_proot_distro_layouts_and_never_deletes_lock() -> None:
    text = RESCUE.read_text(encoding="utf-8")
    _bash_n(RESCUE)
    _self_test(RESCUE)

    assert "containers/debian/rootfs" in text
    assert "installed-rootfs/debian" in text
    assert "--strategy lock-owner" in text
    assert "--proc-root /proc" in text
    assert "find -L /app" in text
    assert "lock owner changed" in text
    assert "refusing KILL" in text
    assert "rm -f /app/.atri-prixok-bot-v133.lock" not in text
    assert "git reset" not in text
    assert "git clean" not in text


def test_recovery_harnesses_do_not_contain_destructive_source_or_lock_patterns() -> None:
    for path in (LIVE, RESCUE):
        text = path.read_text(encoding="utf-8")
        assert "stash pop" not in text
        assert "git reset" not in text
        assert "git clean" not in text
        assert "rm -f /app/.atri-prixok-bot-v133.lock" not in text
