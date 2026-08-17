from pathlib import Path


def _source() -> str:
    return Path("bot/modules/atri_runtime_hardening_v1671.py").read_text(
        encoding="utf-8"
    )


def test_v1671_bot_token_client_uses_memory_storage():
    source = Path("bot/core/telegram_manager.py").read_text(encoding="utf-8")
    start = source.index("async def start_bot")
    end = source.index("async def start_user", start)
    bot_block = source[start:end]

    assert "bot_token=Config.BOT_TOKEN" in bot_block
    assert "in_memory=True" in bot_block
    assert "await cls.bot.start()" in bot_block


def test_v1671_semgrep_startup_is_failfast_and_retry_is_on_demand():
    source = _source()

    assert "ATRI_RUNTIME_HARDENING_V1671" in source
    assert "SEMGREP_MCP_WARM_START_FAILED reason=%s: %s" in source
    assert "await _detach_and_fail_pending(exc)" in source
    assert "await asyncio.sleep(1)" not in source
    assert "code_plugins._semgrep_worker = _semgrep_worker_failfast" in source


def test_v16711_semgrep_live_session_teardown_keeps_reconnect_path():
    source = _source()

    ready_init = source.index("session_ready = False")
    ready_set = source.index("session_ready = True", ready_init)
    reconnect_init = source.index("reconnect_requested = False", ready_init)
    reconnect_set = source.index("reconnect_requested = True", ready_set)
    teardown_guard = source.index(
        "if session_ready and reconnect_requested:",
        reconnect_set,
    )
    reconnect_log = source.index(
        "SEMGREP_MCP_WARM_RECONNECT_TEARDOWN",
        teardown_guard,
    )
    retry = source.index("continue", reconnect_log)

    assert ready_init < reconnect_init < ready_set < reconnect_set
    assert reconnect_set < teardown_guard < reconnect_log < retry


def test_v16712_semgrep_idle_close_does_not_unconditionally_restart():
    source = _source()

    idle_set = source.index("idle_close_requested = True")
    idle_decision = source.index("if await _idle_stop_or_reconnect():", idle_set)
    idle_race_log = source.index("SEMGREP_MCP_WARM_IDLE_RACE_RECONNECT", idle_decision)
    idle_stop = source.index("return", idle_race_log)

    assert idle_set < idle_decision < idle_race_log < idle_stop


def test_v16713_semgrep_enqueue_and_idle_shutdown_share_one_guard():
    source = _source()

    helper = source.index("async def _idle_stop_or_reconnect()")
    queue_check = source.index(
        "if queue is not None and not queue.empty():",
        helper,
    )
    detach = source.index(
        "code_plugins._semgrep_worker_task = None",
        queue_check,
    )

    request = source.index("async def _semgrep_request_guarded(")
    request_guard = source.index("async with guard:", request)
    task_check = source.index("if task is None or task.done():", request_guard)
    enqueue = source.index("queue.put_nowait(", task_check)
    install = source.index(
        "code_plugins._semgrep_request = _semgrep_request_guarded",
        enqueue,
    )

    assert helper < queue_check < detach
    assert request < request_guard < task_check < enqueue < install


def test_v16713_idle_teardown_with_pending_work_reconnects():
    source = _source()

    idle_teardown = source.index("if idle_close_requested:", source.index("except Exception as exc:"))
    race_check = source.index("if await _idle_stop_or_reconnect():", idle_teardown)
    pending_log = source.index(
        "SEMGREP_MCP_WARM_IDLE_TEARDOWN_RECONNECT_PENDING",
        race_check,
    )
    retry = source.index("continue", pending_log)
    final_idle_log = source.index("SEMGREP_MCP_WARM_IDLE_TEARDOWN_FAILED", retry)
    final_stop = source.index("return", final_idle_log)

    assert idle_teardown < race_check < pending_log < retry < final_idle_log < final_stop


def test_v1671_hardening_is_installed_before_semgrep_prewarm():
    source = Path("bot/__main__.py").read_text(encoding="utf-8")

    install = source.index("install_atri_runtime_hardening_v1671()")
    import_prewarm = source.index(
        "from .modules.atri_tools.code_plugins import (",
        install,
    )
    schedule = source.index("prewarm_semgrep_mcp()", import_prewarm)

    assert install < import_prewarm < schedule
