from __future__ import annotations

# ATRI_RESPONSE_OUTPUT_GUARD_V1673

import fcntl
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


def _mutex_path() -> Path:
    return OUTPUT_CLAIM_DIR / ".claims.lock"


def _open_claim_mutex() -> int:
    fd = os.open(
        _mutex_path(),
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _close_claim_mutex(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _safe_unlink_same_inode(path: Path, expected_stat: os.stat_result) -> None:
    try:
        current = path.stat()
    except OSError:
        return

    if (
        current.st_dev == expected_stat.st_dev
        and current.st_ino == expected_stat.st_ino
    ):
        try:
            path.unlink()
        except OSError:
            pass


def _sweep_stale_claims(now: float) -> None:
    """Remove expired claims while the global claim mutex is held."""

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
            stat = path.stat()
            if stat.st_mtime < stale_before:
                _safe_unlink_same_inode(path, stat)
        except OSError:
            continue


def _claim_output_once(
    message: Any,
) -> tuple[bool, tuple[int, int, int] | None]:
    """Atomically claim the Telegram response surface for one source message.

    V167.2 protects ingress. V167.3 independently protects the final Telegram
    output boundary. A short cross-process flock serializes stale replacement,
    creation and cleanup so two workers cannot both reclaim the same expired
    claim. Filesystem failures fail open rather than suppressing Atri replies.
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
        return True, identity

    try:
        mutex_fd = _open_claim_mutex()
    except OSError:
        return True, identity

    try:
        path = _claim_path(identity)
        _sweep_stale_claims(now)

        try:
            existing = path.stat()
        except FileNotFoundError:
            existing = None
        except OSError:
            return True, identity

        if existing is not None:
            age = max(0.0, now - existing.st_mtime)
            if age < OUTPUT_CLAIM_TTL_SECONDS:
                return False, identity

            # The global mutex guarantees no cooperating claimant can replace
            # this path between the inode check and unlink.
            _safe_unlink_same_inode(path, existing)

            try:
                if path.exists():
                    return False, identity
            except OSError:
                return True, identity

        try:
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return False, identity
        except OSError:
            return True, identity

        created_stat: os.stat_result | None = None
        write_ok = False
        try:
            created_stat = os.fstat(fd)
            payload = (
                f"pid={os.getpid()}\n"
                f"claimed_at={now:.6f}\n"
            ).encode("utf-8")
            os.write(fd, payload)
            write_ok = True
        except OSError:
            write_ok = False
        finally:
            try:
                os.close(fd)
            except OSError:
                write_ok = False

        if not write_ok:
            if created_stat is not None:
                _safe_unlink_same_inode(path, created_stat)
            # Fail open: a broken claim store must not make Atri silent.
            return True, identity

        return True, identity
    finally:
        try:
            _close_claim_mutex(mutex_fd)
        except OSError:
            pass


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
