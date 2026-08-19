from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from bot import LOGGER


class BackgroundTaskSupervisor:
    """Track fire-and-forget work owned by the v2 runtime.

    Legacy command entrypoints often called ``bot_loop.create_task`` directly,
    which made task multiplicity and failures hard to audit.  v2 routes long
    running transfer work through this supervisor so every spawned task has a
    deterministic name, is retained until completion, and is cancelled during
    runtime shutdown.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(self._tasks)

    def spawn(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str,
    ) -> asyncio.Task[Any]:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        LOGGER.info(
            "PRIXOK_V2_TASK_SPAWN name=%s task=%s active=%s",
            name,
            id(task),
            len(self._tasks),
        )
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            LOGGER.info(
                "PRIXOK_V2_TASK_CANCELLED name=%s task=%s active=%s",
                task.get_name(),
                id(task),
                len(self._tasks),
            )
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return

        if exc is not None:
            LOGGER.error(
                "PRIXOK_V2_TASK_FAILED name=%s task=%s error=%s:%s",
                task.get_name(),
                id(task),
                type(exc).__name__,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        else:
            LOGGER.info(
                "PRIXOK_V2_TASK_DONE name=%s task=%s active=%s",
                task.get_name(),
                id(task),
                len(self._tasks),
            )

    async def shutdown(self, *, timeout: float = 10.0) -> None:
        pending = [task for task in self._tasks if not task.done()]
        if not pending:
            return

        LOGGER.info("PRIXOK_V2_TASK_SHUTDOWN pending=%s", len(pending))
        for task in pending:
            task.cancel()

        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            LOGGER.warning(
                "PRIXOK_V2_TASK_SHUTDOWN_TIMEOUT pending=%s timeout=%s",
                sum(not task.done() for task in pending),
                timeout,
            )


SUPERVISOR = BackgroundTaskSupervisor()
