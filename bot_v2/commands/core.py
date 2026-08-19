from __future__ import annotations

from time import monotonic_ns


async def ping(_, message) -> None:
    """Native v2 ping command.

    The legacy implementation is decorated with ``new_task`` and therefore
    schedules a second coroutine after the Telegram dispatcher calls the
    handler.  v2 keeps the callback itself awaitable so one dispatcher
    invocation maps to one reply/edit pipeline and is directly observable by
    tests and handler inventory.
    """

    started = monotonic_ns()
    reply = await message.reply_text(
        "Starting Ping",
        quote=True,
        parse_mode=None,
    )
    elapsed_ms = max(0, (monotonic_ns() - started) // 1_000_000)
    await reply.edit_text(
        f"{elapsed_ms} ms",
        parse_mode=None,
    )
