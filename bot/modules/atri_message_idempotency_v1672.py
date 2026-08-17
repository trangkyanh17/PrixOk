from __future__ import annotations

# ATRI_MESSAGE_IDEMPOTENCY_V1672

import functools
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable


CLAIM_DIR = Path(
    os.getenv(
        "ATRI_MESSAGE_CLAIM_DIR",
        "/app/atri_data/atri_message_claims",
    )
)
CLAIM_TTL_SECONDS = max(
    30,
    int(os.getenv("ATRI_MESSAGE_CLAIM_TTL_SECONDS", "600")),
)
SWEEP_INTERVAL_SECONDS = max(
    30,
    int(os.getenv("ATRI_MESSAGE_CLAIM_SWEEP_SECONDS", "120")),
)

_INSTALLED = False
_LAST_SWEEP_AT = 0.0


def _message_identity(message: Any) -> tuple[int, int, int] | None:
    if message is None:
        return None

    chat_id = int(
        getattr(getattr(message, "chat", None), "id", 0)
        or 0
    )
    thread_id = int(
        getattr(message, "message_thread_id", 0)
        or 0
    )
    message_id = int(
        getattr(message, "id", 0)
        or getattr(message, "message_id", 0)
        or 0
    )

    if chat_id == 0 or message_id == 0:
        return None

    return chat_id, thread_id, message_id


def _claim_path(identity: tuple[int, int, int]) -> Path:
    chat_id, thread_id, message_id = identity
    return CLAIM_DIR / f"{chat_id}_{thread_id}_{message_id}.claim"


def _sweep_stale_claims(now: float) -> None:
    global _LAST_SWEEP_AT

    if now - _LAST_SWEEP_AT < SWEEP_INTERVAL_SECONDS:
        return

    _LAST_SWEEP_AT = now

    try:
        entries = list(CLAIM_DIR.iterdir())
    except OSError:
        return

    stale_before = now - CLAIM_TTL_SECONDS

    for path in entries:
        if not path.name.endswith(".claim"):
            continue

        try:
            if path.stat().st_mtime < stale_before:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _claim_message_once(
    message: Any,
    *,
    route: str,
) -> tuple[bool, tuple[int, int, int] | None]:
    """Atomically claim one Telegram message across tasks/processes.

    The claim is intentionally kept for a bounded TTL after completion. This
    suppresses duplicate Telegram delivery, duplicate handler paths, and a
    second bot worker that shares the same /app data directory.
    """

    identity = _message_identity(message)
    if identity is None:
        return True, None

    now = time.time()

    try:
        CLAIM_DIR.mkdir(parents=True, exist_ok=True)
        try:
            CLAIM_DIR.chmod(0o700)
        except OSError:
            pass
    except OSError:
        # Fail open: idempotency must never make the bot unavailable merely
        # because its optional claim directory cannot be created.
        return True, identity

    path = _claim_path(identity)

    for _ in range(2):
        try:
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            try:
                age = max(0.0, now - path.stat().st_mtime)
            except OSError:
                age = 0.0

            if age >= CLAIM_TTL_SECONDS:
                try:
                    path.unlink()
                    continue
                except OSError:
                    pass

            return False, identity
        except OSError:
            return True, identity
        else:
            try:
                payload = (
                    f"pid={os.getpid()}\n"
                    f"route={route}\n"
                    f"claimed_at={now:.6f}\n"
                ).encode("utf-8")
                os.write(fd, payload)
            finally:
                os.close(fd)

            _sweep_stale_claims(now)
            return True, identity

    return False, identity


def _extract_message(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    message = kwargs.get("message")
    if message is not None:
        return message

    if len(args) >= 2:
        return args[1]

    return args[-1] if args else None


def _wrap_callback(
    callback: Callable[..., Awaitable[Any]],
    *,
    route: str,
    logger: Any,
) -> Callable[..., Awaitable[Any]]:
    if getattr(callback, "_atri_v1672_idempotent", False):
        return callback

    @functools.wraps(callback)
    async def guarded(*args: Any, **kwargs: Any) -> Any:
        message = _extract_message(args, kwargs)
        accepted, identity = _claim_message_once(
            message,
            route=route,
        )

        if not accepted:
            if identity is not None:
                logger.warning(
                    "ATRI_MESSAGE_DUPLICATE_DROPPED_V1672 "
                    "route=%s chat=%s thread=%s message=%s",
                    route,
                    identity[0],
                    identity[1],
                    identity[2],
                )
            return None

        if identity is not None:
            logger.info(
                "ATRI_MESSAGE_CLAIMED_V1672 "
                "route=%s chat=%s thread=%s message=%s",
                route,
                identity[0],
                identity[1],
                identity[2],
            )

        return await callback(*args, **kwargs)

    setattr(guarded, "_atri_v1672_idempotent", True)
    return guarded


def install_atri_message_idempotency_v1672() -> None:
    """Guard both registered and direct Atri entry points by message ID."""

    global _INSTALLED
    if _INSTALLED:
        return

    from bot import LOGGER
    from bot.core import handlers as core_handlers
    from bot.modules import atri_ai

    # core.handlers imported atri_message by value and V157 may already have
    # wrapped that binding. Guard the final callback before add_handlers().
    core_handlers.atri_message = _wrap_callback(
        core_handlers.atri_message,
        route="handler",
        logger=LOGGER,
    )

    # reply_after_external_action() resolves atri_ai.atri_message directly.
    # Guard this path with the same process-shared claim directory so one
    # Telegram update can never produce two independent response pipelines.
    atri_ai.atri_message = _wrap_callback(
        atri_ai.atri_message,
        route="direct",
        logger=LOGGER,
    )

    _INSTALLED = True
    LOGGER.info(
        "ATRI_MESSAGE_IDEMPOTENCY_V1672_INSTALLED ttl=%s path=%s",
        CLAIM_TTL_SECONDS,
        CLAIM_DIR,
    )
