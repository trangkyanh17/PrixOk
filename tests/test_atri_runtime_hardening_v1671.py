from pathlib import Path


def test_v1671_bot_token_client_uses_memory_storage():
    source = Path("bot/core/telegram_manager.py").read_text(encoding="utf-8")
    start = source.index("async def start_bot")
    end = source.index("async def start_user", start)
    bot_block = source[start:end]

    assert "bot_token=Config.BOT_TOKEN" in bot_block
    assert "in_memory=True" in bot_block
    assert "await cls.bot.start()" in bot_block


def test_v1671_semgrep_startup_is_failfast_and_retry_is_on_demand():
    source = Path("bot/modules/atri_runtime_hardening_v1671.py").read_text(
        encoding="utf-8"
    )

    assert "ATRI_RUNTIME_HARDENING_V1671" in source
    assert "SEMGREP_MCP_WARM_START_FAILED reason=%s: %s" in source
    assert "future.set_exception(exc)" in source
    assert "return" in source
    assert "await asyncio.sleep(1)" not in source
    assert "code_plugins._semgrep_worker = _semgrep_worker_failfast" in source


def test_v16711_semgrep_live_session_teardown_keeps_reconnect_path():
    source = Path("bot/modules/atri_runtime_hardening_v1671.py").read_text(
        encoding="utf-8"
    )

    ready_init = source.index("session_ready = False")
    ready_set = source.index("session_ready = True", ready_init)
    teardown_guard = source.index("if session_ready:", ready_set)
    reconnect_log = source.index(
        "SEMGREP_MCP_WARM_RECONNECT_TEARDOWN",
        teardown_guard,
    )
    retry = source.index("continue", reconnect_log)
    failfast = source.index("SEMGREP_MCP_WARM_START_FAILED", retry)

    assert ready_init < ready_set < teardown_guard < reconnect_log < retry < failfast


def test_v1671_hardening_is_installed_before_semgrep_prewarm():
    source = Path("bot/__main__.py").read_text(encoding="utf-8")

    install = source.index("install_atri_runtime_hardening_v1671()")
    import_prewarm = source.index(
        "from .modules.atri_tools.code_plugins import (",
        install,
    )
    schedule = source.index("prewarm_semgrep_mcp()", import_prewarm)

    assert install < import_prewarm < schedule
