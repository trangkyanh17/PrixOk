from __future__ import annotations

from contextlib import closing
import asyncio
import os
import sqlite3
import time
from pathlib import Path

from pyrogram.types import ChatPermissions

from bot import LOGGER


DB_PATH = Path(
    os.getenv(
        "ATRI_ROSE_DB",
        "/app/atri_data/atri_rose.sqlite3",
    )
)

_WORKER_TASK: asyncio.Task | None = None
_DB_LOCK = asyncio.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=30000")

    return connection


def _init_sync() -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rose_timed_actions (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rose_timed_actions_expires
            ON rose_timed_actions(expires_at)
            """
        )

        connection.commit()


def _save_sync(
    chat_id: int,
    user_id: int,
    action: str,
    expires_at: int,
) -> None:
    _init_sync()

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO rose_timed_actions(
                chat_id,
                user_id,
                action,
                expires_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET
                action = excluded.action,
                expires_at = excluded.expires_at,
                created_at = excluded.created_at
            """,
            (
                int(chat_id),
                int(user_id),
                str(action),
                int(expires_at),
                int(time.time()),
            ),
        )

        connection.commit()


def _cancel_sync(
    chat_id: int,
    user_id: int,
) -> None:
    _init_sync()

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            DELETE FROM rose_timed_actions
            WHERE chat_id = ? AND user_id = ?
            """,
            (
                int(chat_id),
                int(user_id),
            ),
        )

        connection.commit()


def _due_sync(
    now: int,
) -> list[dict]:
    _init_sync()

    with closing(_connect()) as connection, connection:
        rows = connection.execute(
            """
            SELECT chat_id, user_id, action, expires_at
            FROM rose_timed_actions
            WHERE expires_at <= ?
            ORDER BY expires_at ASC
            LIMIT 100
            """,
            (int(now),),
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


async def _save(
    chat_id: int,
    user_id: int,
    action: str,
    expires_at: int,
) -> None:
    async with _DB_LOCK:
        await asyncio.to_thread(
            _save_sync,
            chat_id,
            user_id,
            action,
            expires_at,
        )


async def cancel_timed_release(
    chat_id: int,
    user_id: int,
) -> None:
    async with _DB_LOCK:
        await asyncio.to_thread(
            _cancel_sync,
            chat_id,
            user_id,
        )


async def _due() -> list[dict]:
    async with _DB_LOCK:
        return await asyncio.to_thread(
            _due_sync,
            int(time.time()),
        )


def _restore_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
    )


async def schedule_timed_release(
    client,
    chat_id: int,
    user_id: int,
    action: str,
    seconds: int,
) -> int:
    seconds = int(
        seconds or 0
    )

    if seconds < 30:
        raise ValueError(
            "Thời gian phải từ 30 giây trở lên."
        )

    if seconds > 366 * 86400:
        raise ValueError(
            "Thời gian tối đa là 366 ngày."
        )

    action = str(
        action or ""
    ).casefold()

    if action not in {
        "tmute",
        "tban",
    }:
        raise ValueError(
            f"Timed action không hỗ trợ: {action}"
        )

    expires_at = int(
        time.time()
    ) + seconds

    await _save(
        chat_id,
        user_id,
        action,
        expires_at,
    )

    ensure_timed_release_worker(
        client
    )

    return expires_at


def _status_text(
    member,
) -> str:
    status = getattr(
        member,
        "status",
        "",
    )

    value = getattr(
        status,
        "value",
        status,
    )

    return str(
        value or ""
    ).casefold()


async def _release_one(
    client,
    row: dict,
) -> bool:
    chat_id = int(
        row["chat_id"]
    )
    user_id = int(
        row["user_id"]
    )
    action = str(
        row["action"]
    ).casefold()

    try:
        try:
            member = await client.get_chat_member(
                chat_id,
                user_id,
            )
            status = _status_text(
                member
            )
        except Exception as exc:
            text = str(
                exc
            ).casefold()

            if (
                "user_not_participant" in text
                or "not participant" in text
            ):
                await cancel_timed_release(
                    chat_id,
                    user_id,
                )
                return True

            raise

        if action == "tmute":
            if "restricted" in status:
                await client.restrict_chat_member(
                    chat_id,
                    user_id,
                    permissions=_restore_permissions(),
                )

        elif action == "tban":
            if (
                "banned" in status
                or "kicked" in status
            ):
                await client.unban_chat_member(
                    chat_id,
                    user_id,
                )

        else:
            LOGGER.warning(
                "Unknown Atri timed action: %s",
                action,
            )

        await cancel_timed_release(
            chat_id,
            user_id,
        )

        LOGGER.info(
            "Atri timed moderation expired "
            "chat=%s user=%s action=%s",
            chat_id,
            user_id,
            action,
        )

        return True

    except Exception:
        LOGGER.exception(
            "Atri timed moderation release failed "
            "chat=%s user=%s action=%s",
            chat_id,
            user_id,
            action,
        )

        return False


async def _worker(
    client,
) -> None:
    async with _DB_LOCK:
        await asyncio.to_thread(
            _init_sync
        )

    while True:
        try:
            rows = await _due()

            for row in rows:
                await _release_one(
                    client,
                    row,
                )

        except asyncio.CancelledError:
            raise

        except Exception:
            LOGGER.exception(
                "Atri timed moderation worker failed"
            )

        await asyncio.sleep(
            2
        )


def ensure_timed_release_worker(
    client,
) -> None:
    global _WORKER_TASK

    if (
        _WORKER_TASK is not None
        and not _WORKER_TASK.done()
    ):
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    _WORKER_TASK = loop.create_task(
        _worker(
            client
        ),
        name="atri-rose-timed-actions",
    )
