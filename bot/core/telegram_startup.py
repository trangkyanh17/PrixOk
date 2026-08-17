from __future__ import annotations

from asyncio import sleep
from collections.abc import Awaitable, Callable
from typing import Any

from pyrogram.errors import FloodWait


async def start_bot_client(
    client: Any,
    logger: Any,
    *,
    sleeper: Callable[[float], Awaitable[None]] = sleep,
) -> Any:
    """Start a bot client without converting Telegram FloodWait into a crash loop.

    Kurigram disconnects a client before re-raising a start/authorization error,
    so the same client can be retried after the exact server-requested delay.
    Only FloodWait is absorbed here; invalid tokens, API errors and programming
    errors still propagate to the production worker and remain observable.
    """

    while True:
        try:
            return await client.start()
        except FloodWait as exc:
            wait_seconds = max(1, int(exc.value))
            logger.warning(
                "TELEGRAM_BOT_START_FLOOD_WAIT seconds=%s action=wait-in-process",
                wait_seconds,
            )
            await sleeper(wait_seconds + 1)
