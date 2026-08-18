from __future__ import annotations

# ATRI_TELEGRAM_UPDATE_IDEMPOTENCY_V1684

import fcntl
import hashlib
import os
import time
from pathlib import Path
from typing import Any


UPDATE_CLAIM_DIR = Path(
    os.getenv(
        "ATRI_TELEGRAM_UPDATE_CLAIM_DIR",
        "/app/atri_data/atri_telegram_update_claims",
    )
)
UPDATE_CLAIM_TTL_SECONDS = max(
    30,
    int(os.getenv("ATRI_TELEGRAM_UPDATE_CLAIM_TTL_SECONDS", "600")),
)
UPDATE_SWEEP_INTERVAL_SECONDS = max(
    30,
    int(os.getenv("ATRI_TELEGRAM_UPDATE_SWEEP_SECONDS", "120")),
)

_LAST_SWEEP_AT = 0.0


def _scalar_identity(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()[:256]


def _update_identity(update: Any) -> str | None:
    """Return the strongest stable identity exposed by a parsed update."""

    if update is None:
        return None

    explicit_update_id = _scalar_identity(
        getattr(update, "update_id", None)
    )
    if explicit_update_id:
        return f"update:{explicit_update_id}"

    # Callback-query IDs are unique per click. The backing message ID cannot
    # be used here because independent navigation clicks edit the same message.
    if getattr(update, "data", None) is not None:
        callback_id = _scalar_identity(getattr(update, "id", None))
        if callback_id:
            return f"callback:{callback_id}"
        return None

    chat_id = int(
        getattr(getattr(update, "chat", None), "id", 0)
        or 0
    )
    thread_id = int(
        getattr(update, "message_thread_id", 0)
        or 0
    )
    message_id = int(
        getattr(update, "id", 0)
        or getattr(update, "message_id", 0)
        or 0
    )

    if chat_id == 0 or message_id == 0:
        return None

    return f"message:{chat_id}:{thread_id}:{message_id}"


def _claim_path(identity: str) -> Path:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return UPDATE_CLAIM_DIR / f"{digest}.claim"


def _mutex_path() -> Path:
    return UPDATE_CLAIM_DIR / ".claims.lock"


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


def _safe_unlink_same_inode(
    path: Path,
    expected_stat: os.stat_result,
) -> None:
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

    if now - _LAST_SWEEP_AT < UPDATE_SWEEP_INTERVAL_SECONDS:
        return

    _LAST_SWEEP_AT = now
    stale_before = now - UPDATE_CLAIM_TTL_SECONDS

    try:
        entries = list(UPDATE_CLAIM_DIR.iterdir())
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


def _write_all(fd: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("short claim write")
        remaining = remaining[written:]


def claim_telegram_update_once(
    update: Any,
    *,
    route: str,
) -> tuple[bool, str | None]:
    """Atomically claim one Telegram event before its first side effect.

    Claims are shared across tasks and processes. Filesystem failures fail open
    so an unavailable optional claim store cannot make the bot silent.
    """

    identity = _update_identity(update)
    if identity is None:
        return True, None

    now = time.time()

    try:
        UPDATE_CLAIM_DIR.mkdir(parents=True, exist_ok=True)
        try:
            UPDATE_CLAIM_DIR.chmod(0o700)
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
            if age < UPDATE_CLAIM_TTL_SECONDS:
                return False, identity

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
            safe_route = str(route or "").replace("\n", " ")[:160]
            payload = (
                f"pid={os.getpid()}\n"
                f"route={safe_route}\n"
                f"identity={identity}\n"
                f"claimed_at={now:.6f}\n"
            ).encode("utf-8")
            _write_all(fd, payload)
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
            return True, identity

        return True, identity
    finally:
        try:
            _close_claim_mutex(mutex_fd)
        except OSError:
            pass
