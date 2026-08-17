from pathlib import Path


def _start_bot_block() -> str:
    source = Path("bot/core/telegram_manager.py").read_text(encoding="utf-8")
    start = source.index("async def start_bot")
    end = source.index("async def start_user", start)
    return source[start:end]


def test_bot_token_session_survives_process_restarts():
    bot_block = _start_bot_block()

    assert "bot_token=Config.BOT_TOKEN" in bot_block
    assert "workdir=\"/app\"" in bot_block
    assert "in_memory=True" not in bot_block
    assert "in_memory=False" in bot_block


def test_bot_start_floodwait_is_held_inside_one_worker():
    source = Path("bot/core/telegram_manager.py").read_text(encoding="utf-8")
    bot_block = _start_bot_block()

    assert "from pyrogram.errors import FloodWait" in source
    assert "except FloodWait as exc:" in bot_block
    assert "TELEGRAM_BOT_START_FLOOD_WAIT" in bot_block
    assert "await sleep(wait_seconds + 1)" in bot_block


def test_v150_launcher_respawns_supervisor_without_respawning_bot_itself():
    source = Path("rewrite/termux-v150-production-watchdog.sh").read_text(
        encoding="utf-8"
    )

    assert "while true; do" in source
    assert "SUPERVISOR_EXIT" in source
    assert "SUPERVISOR_RESTART_BACKOFF" in source
    assert "trap 'request_stop' TERM INT HUP" in source
    assert 'exec env \\\n' not in source
    assert "tmux new-session" not in source
