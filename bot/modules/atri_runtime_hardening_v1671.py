from __future__ import annotations

# ATRI_RUNTIME_HARDENING_V1671

import asyncio

_INSTALLED = False


def install_atri_runtime_hardening_v1671() -> None:
    """Install production-proven V167.1 Semgrep runtime guards.

    V167.4 supersedes the old V167.1 in-memory bot-session policy: the Telegram
    bot now keeps its authorized session on disk and handles startup FloodWait
    inside the same worker. This hook remains responsible only for Semgrep MCP
    fail-fast/reconnect behavior.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    from bot import LOGGER
    from .atri_tools import code_plugins

    def _worker_guard() -> asyncio.Lock:
        guard = code_plugins._semgrep_worker_guard
        if guard is None:
            guard = asyncio.Lock()
            code_plugins._semgrep_worker_guard = guard
        return guard

    async def _detach_and_fail_pending(exc: Exception) -> None:
        guard = _worker_guard()
        current = asyncio.current_task()

        async with guard:
            queue = code_plugins._semgrep_worker_queue

            if code_plugins._semgrep_worker_task is current:
                code_plugins._semgrep_worker_task = None
                code_plugins._semgrep_worker_queue = None

            if queue is None:
                return

            while True:
                try:
                    _, _, future = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                if not future.done():
                    future.set_exception(exc)

    async def _idle_stop_or_reconnect() -> bool:
        """Atomically stop an idle worker or keep it alive for queued work."""

        guard = _worker_guard()
        current = asyncio.current_task()

        async with guard:
            queue = code_plugins._semgrep_worker_queue

            if queue is not None and not queue.empty():
                return True

            if code_plugins._semgrep_worker_task is current:
                code_plugins._semgrep_worker_task = None
                code_plugins._semgrep_worker_queue = None

            return False

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
                    if await _idle_stop_or_reconnect():
                        LOGGER.info("SEMGREP_MCP_WARM_IDLE_RACE_RECONNECT")
                        continue
                    return

                if reconnect_requested:
                    await asyncio.sleep(0.25)
                    continue

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if idle_close_requested:
                    if await _idle_stop_or_reconnect():
                        LOGGER.warning(
                            "SEMGREP_MCP_WARM_IDLE_TEARDOWN_RECONNECT_PENDING "
                            "reason=%s: %s",
                            type(exc).__name__,
                            exc,
                        )
                        await asyncio.sleep(0.25)
                        continue

                    LOGGER.warning(
                        "SEMGREP_MCP_WARM_IDLE_TEARDOWN_FAILED reason=%s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    return

                if session_ready and reconnect_requested:
                    LOGGER.warning(
                        "SEMGREP_MCP_WARM_RECONNECT_TEARDOWN reason=%s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    await asyncio.sleep(0.25)
                    continue

                if session_ready:
                    if await _idle_stop_or_reconnect():
                        LOGGER.warning(
                            "SEMGREP_MCP_WARM_TEARDOWN_RECONNECT_PENDING "
                            "reason=%s: %s",
                            type(exc).__name__,
                            exc,
                        )
                        await asyncio.sleep(0.25)
                        continue

                    LOGGER.warning(
                        "SEMGREP_MCP_WARM_TEARDOWN_STOP reason=%s: %s",
                        type(exc).__name__,
                        exc,
                    )
                    return

                LOGGER.warning(
                    "SEMGREP_MCP_WARM_START_FAILED reason=%s: %s",
                    type(exc).__name__,
                    exc,
                )
                await _detach_and_fail_pending(exc)
                return

    async def _semgrep_request_guarded(
        operation: str,
        *,
        tool: str = "",
        arguments: dict | None = None,
    ):
        guard = _worker_guard()
        loop = asyncio.get_running_loop()

        async with guard:
            task = code_plugins._semgrep_worker_task

            if task is None or task.done():
                queue: asyncio.Queue = asyncio.Queue()
                code_plugins._semgrep_worker_queue = queue
                task = asyncio.create_task(
                    code_plugins._semgrep_worker(),
                    name="atri-semgrep-mcp-worker",
                )
                code_plugins._semgrep_worker_task = task
            else:
                queue = code_plugins._semgrep_worker_queue
                if queue is None:
                    queue = asyncio.Queue()
                    code_plugins._semgrep_worker_queue = queue

            future = loop.create_future()
            queue.put_nowait(
                (
                    operation,
                    {
                        "tool": tool,
                        "arguments": arguments or {},
                    },
                    future,
                )
            )

        return await future

    code_plugins._semgrep_worker = _semgrep_worker_failfast
    code_plugins._semgrep_request = _semgrep_request_guarded
    code_plugins.ATRI_SEMGREP_FAILFAST_V1671 = True
    _INSTALLED = True
    LOGGER.info("ATRI_RUNTIME_HARDENING_V1671_INSTALLED")
