from __future__ import annotations

# ATRI_RESPONSE_OUTPUT_GUARD_V1673

import os
import time
from pathlib import Path
from typing import Any


OUTPUT_CLAIM_DIR = Path(
    os.getenv(
        "ATRI_RESPONSE_OUTPUT_CLAIM_DIR",
        "/app/atri_data/atri_response_output_claims",
    )
)
OUTPUT_CLAIM_TTL_SECONDS = max(
    30,
    int(os.getenv("ATRI_RESPONSE_OUTPUT_CLAIM_TTL_SECONDS", "600")),
)
OUTPUT_SWEEP_INTERVAL_SECONDS = max(
    30,
    int(os.getenv("ATRI_RESPONSE_OUTPUT_SWEEP_SECONDS", "120")),
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
    return OUTPUT_CLAIM_DIR / f"{chat_id}_{thread_id}_{message_id}.claim"


def _sweep_stale_claims(now: float) -> None:
    global _LAST_SWEEP_AT

    if now - _LAST_SWEEP_AT < OUTPUT_SWEEP_INTERVAL_SECONDS:
        return

    _LAST_SWEEP_AT = now
    stale_before = now - OUTPUT_CLAIM_TTL_SECONDS

    try:
        entries = list(OUTPUT_CLAIM_DIR.iterdir())
    except OSError:
        return

    for path in entries:
        if not path.name.endswith(".claim"):
            continue
        try:
            if path.stat().st_mtime < stale_before:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _claim_output_once(
    message: Any,
) -> tuple[bool, tuple[int, int, int] | None]:
    """Claim the Telegram response surface for one source message.

    This is deliberately independent from the ingress V167.2 claim. Even if a
    legacy callback, duplicate handler binding, or another worker bypasses the
    ingress wrapper, only one response-state pipeline may emit thinking/final
    media for the same Telegram message during the bounded TTL.
    """

    identity = _message_identity(message)
    if identity is None:
        return True, None

    now = time.time()

    try:
        OUTPUT_CLAIM_DIR.mkdir(parents=True, exist_ok=True)
        try:
            OUTPUT_CLAIM_DIR.chmod(0o700)
        except OSError:
            pass
    except OSError:
        # Fail open so a filesystem problem never makes Atri unavailable.
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

            if age >= OUTPUT_CLAIM_TTL_SECONDS:
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
                    f"claimed_at={now:.6f}\n"
                ).encode("utf-8")
                os.write(fd, payload)
            finally:
                os.close(fd)

            _sweep_stale_claims(now)
            return True, identity

    return False, identity


def _ensure_state_owner(state: Any, logger: Any) -> bool:
    cached = getattr(state, "_atri_v1673_output_owner", None)
    if cached is not None:
        return bool(cached)

    accepted, identity = _claim_output_once(
        getattr(state, "source_message", None)
    )
    setattr(state, "_atri_v1673_output_owner", bool(accepted))
    setattr(state, "_atri_v1673_output_identity", identity)

    if identity is not None:
        if accepted:
            logger.info(
                "ATRI_RESPONSE_OUTPUT_CLAIMED_V1673 "
                "chat=%s thread=%s message=%s pid=%s",
                identity[0],
                identity[1],
                identity[2],
                os.getpid(),
            )
        else:
            logger.warning(
                "ATRI_RESPONSE_OUTPUT_DUPLICATE_DROPPED_V1673 "
                "chat=%s thread=%s message=%s pid=%s",
                identity[0],
                identity[1],
                identity[2],
                os.getpid(),
            )

    return bool(accepted)


def install_atri_response_output_guard_v1673() -> None:
    """Make visual response emission idempotent at the final output boundary."""

    global _INSTALLED
    if _INSTALLED:
        return

    from bot import LOGGER
    from bot.modules import atri_response_states as states

    cls = states.AtriResponseState
    if getattr(cls, "_atri_v1673_output_guarded", False):
        _INSTALLED = True
        return

    original_show_thinking = cls.show_thinking
    original_finalize = cls.finalize
    original_finalize_error = cls.finalize_error

    async def show_thinking_guarded(self, *args: Any, **kwargs: Any):
        if not _ensure_state_owner(self, LOGGER):
            return None
        return await original_show_thinking(self, *args, **kwargs)

    async def finalize_guarded(self, *args: Any, **kwargs: Any):
        if not _ensure_state_owner(self, LOGGER):
            return None
        return await original_finalize(self, *args, **kwargs)

    async def finalize_error_guarded(self, *args: Any, **kwargs: Any):
        if not _ensure_state_owner(self, LOGGER):
            return None
        return await original_finalize_error(self, *args, **kwargs)

    cls.show_thinking = show_thinking_guarded
    cls.finalize = finalize_guarded
    cls.finalize_error = finalize_error_guarded
    setattr(cls, "_atri_v1673_output_guarded", True)

    _INSTALLED = True
    LOGGER.info(
        "ATRI_RESPONSE_OUTPUT_GUARD_V1673_INSTALLED ttl=%s path=%s",
        OUTPUT_CLAIM_TTL_SECONDS,
        OUTPUT_CLAIM_DIR,
    )
