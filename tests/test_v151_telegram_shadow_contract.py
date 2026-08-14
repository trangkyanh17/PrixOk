import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHADOW = ROOT / "bot" / "modules" / "atri_v150_shadow.py"
MAIN = ROOT / "bot" / "__main__.py"
WATCHDOG = ROOT / "rewrite" / "termux-v150-production-watchdog.sh"
CANARY = ROOT / "rewrite" / "termux-v151-shadow-canary.sh"
PATCHER = ROOT / "rewrite" / "v151_shadow_patch.py"
TERMUX_LAUNCHER = ROOT / "termux" / "prixok-bot.sh"


def test_shadow_bridge_defaults_off_and_runs_before_production_groups():
    source = SHADOW.read_text(encoding="utf-8")
    assert "_HANDLER_GROUP = -1000" in source
    assert '_env_bool("ATRI_V150_TELEGRAM_SHADOW", False)' in source
    assert "/.local/state/atri-v151-shadow/enabled" in source
    assert "MessageHandler(_observe_message" in source
    assert "EditedMessageHandler(_observe_edited_message" in source
    assert "CallbackQueryHandler(_observe_callback)" in source


def test_shadow_bridge_has_no_telegram_outbound_api():
    source = SHADOW.read_text(encoding="utf-8")
    forbidden = (
        ".send_message(",
        ".send_document(",
        ".send_photo(",
        ".send_video(",
        ".send_audio(",
        ".send_voice(",
        ".send_sticker(",
        ".send_animation(",
        ".edit_message_text(",
        ".answer_callback_query(",
    )
    for marker in forbidden:
        assert marker not in source


def test_shadow_registration_and_env_propagation_are_explicit():
    main_source = MAIN.read_text(encoding="utf-8")
    watchdog_source = WATCHDOG.read_text(encoding="utf-8")
    launcher_source = TERMUX_LAUNCHER.read_text(encoding="utf-8")

    assert "add_v150_shadow_handlers(TgClient.bot)" in main_source
    assert 'SHADOW_CONFIG="$HOME/.local/state/atri-v151-shadow/runtime.env"' in watchdog_source
    assert 'ATRI_V150_TELEGRAM_SHADOW="${ATRI_V150_TELEGRAM_SHADOW:-false}"' in watchdog_source
    assert 'SHADOW_ENABLED="${ATRI_V150_TELEGRAM_SHADOW:-false}"' in launcher_source
    assert 'ATRI_V150_TELEGRAM_SHADOW="$SHADOW_ENABLED"' in launcher_source


def test_canary_manager_is_narrow_and_has_guarded_rollback():
    canary = CANARY.read_text(encoding="utf-8")
    patcher = PATCHER.read_text(encoding="utf-8")

    assert "v151_shadow_patch.py" in canary
    assert "ATRI_V150_TELEGRAM_SHADOW=true" in canary
    assert "tmux send-keys -t prixok-bot C-c" in canary
    assert "AUTO ROLLBACK" in canary
    assert not re.search(
        r"(?m)^\s*git\s+(?:pull|reset|checkout|clean)\b", canary
    )
    assert not re.search(r"(?m)^\s*rm\s+-rf\s+/app(?:/|\s|$)", canary)
    assert "bot/__main__.py" in patcher
    assert "bot/modules/atri_v150_shadow.py" in patcher
    assert "changed after V151 apply" in patcher
