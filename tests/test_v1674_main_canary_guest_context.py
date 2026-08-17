from pathlib import Path


def test_v1674_main_canary_lock_probe_stays_in_current_debian_guest():
    source = Path("rewrite/termux-v1674-main-final-canary.sh").read_text(encoding="utf-8")
    start = source.index("bot_lock_state()")
    end = source.index("wait_lock_released()", start)
    lock_block = source[start:end]
    assert 'local p="/app/.atri-prixok-bot-v133.lock"' in lock_block
    assert "flock -n 9" in lock_block
    assert "proot-distro login debian" not in lock_block


def test_v1674_main_canary_requires_debian_app_context_before_live_actions():
    source = Path("rewrite/termux-v1674-main-final-canary.sh").read_text(encoding="utf-8")
    exact_main = source.index('section "1. EXACT MAIN"')
    stage = source.index('section "5. STAGE MAIN WRAPPERS"')
    guarded = source[exact_main:stage]
    assert '[[ -f /etc/debian_version ]]' in guarded
    assert '[[ "$ROOT_DIR" == "/app" ]]' in guarded
