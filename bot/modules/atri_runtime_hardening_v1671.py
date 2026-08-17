from __future__ import annotations

# ATRI_RUNTIME_HARDENING_V1671

import asyncio

_INSTALLED = False


def install_atri_runtime_hardening_v1671() -> None:
    """Install production-proven V167.1 runtime guards.

    The bot-token Pyrogram session is configured in-memory in telegram_manager.
    This hook makes Semgrep MCP startup fail fast instead of retrying forever
    when its stdio server cannot initialize. A later real request can create a
    fresh worker through the existing _ensure_semgrep_worker() path.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    from bot import LOGGER
    from .atri_tools import code_plugins

    async def _semgrep_worker_failfast() -> None:
        while True:
            session_ready = False
            reconnect_requested = False
            idle_close_requested = False

            try:
                async with code_plugins._session("semgrep") as session:
                    session_ready = True
                    LOGGER.info("SEMGREP_MCP_WARM_READY")

                    while True:
                        try:
                            item = await asyncio.wait_for(
                                code_plugins._semgrep_worker_queue.get(),
                                timeout=code_plugins.SEMGREP_MCP_IDLE_SECONDS,
                            )
                        except TimeoutError:
                            idle_close_requested = True
                            LOGGER.info(
                                "SEMGREP_MCP_WARM_IDLE_CLOSE seconds=%s",
                                code_plugins.SEMGREP_MCP_IDLE_SECONDS,
                            )
                            break

                        operation, payload, future = item

                        if future.done():
                            continue

                        try:
                            if operation == "list_tools":
                                result = await session.list_tools()
                            elif operation == "call_tool":
                                result = await session.call_tool(
                                    payload["tool"],
                                    arguments=payload.get("arguments") or {},
                                )
                            else:
                                raise RuntimeError(
                                    "Unknown Semgrep worker operation: "
                                    f"{operation}"
                                )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            if not future.done():
                                future.set_exception(exc)

                            reconnect_requested = True
                            LOGGER.warning(
                                "SEMGREP_MCP_WARM_RECONNECT reason=%s: %s",
                                type(exc).__name__,
                                exc,
                            )
                            break
                        else:
                            if not future.done():
                                future.set_result(result)

                    if idle_close_requested:
                        return

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if idle_close_requested:
                    # The worker intentionally went idle. A cleanup failure
                    # must not defeat SEMGREP_MCP_IDLE_SECONDS by spawning a
                    # new Semgrep process forever.
                    LOGGER.warning(
                        "SEMGREP_MCP_WARM_IDLE_TEARDOWN_FAILED reason=%s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    return

                if session_ready and reconnect_requested:
                    # A live session can fail again while its stdio/task-group
                    # context is tearing down. Keep queued work and reconnect.
                    LOGGER.warning(
                        "SEMGREP_MCP_WARM_RECONNECT_TEARDOWN reason=%s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    await asyncio.sleep(0.25)
                    continue

                if session_ready:
                    # No explicit reconnect request exists, so avoid turning
                    # an unexpected teardown into an unbounded restart loop.
                    LOGGER.warning(
                        "SEMGREP_MCP_WARM_TEARDOWN_STOP reason=%s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    return

                # Startup/prewarm is opportunistic. Do not retry once per
                # second forever when uvx/Semgrep cannot initialize.
                LOGGER.warning(
                    "SEMGREP_MCP_WARM_START_FAILED reason=%s: %s",
                    type(exc).__name__,
                    exc,
                )

                queue = code_plugins._semgrep_worker_queue
                if queue is not None:
                    while True:
                        try:
                            item = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break

                        _, _, future = item
                        if not future.done():
                            future.set_exception(exc)

                return

    code_plugins._semgrep_worker = _semgrep_worker_failfast
    code_plugins.ATRI_SEMGREP_FAILFAST_V1671 = True
    _INSTALLED = True
    LOGGER.info("ATRI_RUNTIME_HARDENING_V1671_INSTALLED")
