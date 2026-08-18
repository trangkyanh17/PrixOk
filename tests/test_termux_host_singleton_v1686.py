from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "termux" / "prixok-bot.sh"


def test_termux_host_singleton_self_test_passes():
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--self-test"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "prixok host singleton self-test: PASS" in result.stdout


def test_termux_host_singleton_is_before_proot_exec():
    source = LAUNCHER.read_text(encoding="utf-8")
    lock = source.index("ATRI_PRODUCTION_HOST_SINGLETON_V1686_DUPLICATE_BLOCKED")
    proot = source.index("exec proot-distro login debian")
    assert lock < proot
    assert 'exec 9<>"$HOST_LOCK_PATH"' in source
    assert 'flock -n "$HOST_LOCK_FD"' in source
    assert "exit 73" in source
