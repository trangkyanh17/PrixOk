from __future__ import annotations

from contextlib import closing
import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


DB_PATH = Path(
    os.getenv(
        "ATRI_MEMORY_DB",
        "/app/atri_data/atri_memory.sqlite3",
    )
)

# Keep only the recent conversational window. Long-term facts/preferences are
# handled separately by atri_long_memory; retaining dozens of model turns here
# made old jokes and motifs echo back into new replies.
MAX_HISTORY_ITEMS = max(
    2,
    int(os.getenv("ATRI_MEMORY_MAX_ITEMS", "12")),
)
MAX_CHAT_ROWS = max(
    10,
    int(os.getenv("ATRI_MEMORY_MAX_CHATS", "500")),
)
RETENTION_SECONDS = max(
    3600,
    int(os.getenv("ATRI_MEMORY_RETENTION_SECONDS", "2592000")),
)

_DB_LOCK = asyncio.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=30000")

    return connection


def _initialize_sync() -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_memory (
                chat_key TEXT PRIMARY KEY,
                history_json TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
            """
        )


def _key_to_text(key: Any) -> str:
    try:
        return json.dumps(
            key,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return repr(key)


def _normalize_history(
    history: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    try:
        items = list(history)
    except TypeError:
        return result

    for item in items[-MAX_HISTORY_ITEMS:]:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role") or "").strip()
        parts = item.get("parts")

        if role not in {"user", "model"}:
            continue

        if not isinstance(parts, list):
            continue

        clean_parts: list[dict[str, Any]] = []

        for part in parts:
            if not isinstance(part, dict):
                continue

            try:
                json.dumps(part, ensure_ascii=False)
            except (TypeError, ValueError):
                continue

            clean_parts.append(part)

        if clean_parts:
            result.append(
                {
                    "role": role,
                    "parts": clean_parts,
                }
            )

    return result


def _load_sync(key: Any) -> list[dict[str, Any]]:
    _initialize_sync()
    chat_key = _key_to_text(key)

    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT history_json
            FROM chat_memory
            WHERE chat_key = ?
            """,
            (chat_key,),
        ).fetchone()

    if row is None:
        return []

    try:
        history = json.loads(
            str(row["history_json"])
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    return _normalize_history(history)


def _save_sync(
    key: Any,
    history: Any,
) -> None:
    _initialize_sync()

    chat_key = _key_to_text(key)
    normalized = _normalize_history(history)
    now = int(time.time())

    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with closing(_connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO chat_memory(
                chat_key,
                history_json,
                message_count,
                updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_key)
            DO UPDATE SET
                history_json = excluded.history_json,
                message_count = excluded.message_count,
                updated_at = excluded.updated_at
            """,
            (
                chat_key,
                payload,
                len(normalized),
                now,
            ),
        )
        connection.execute(
            "DELETE FROM chat_memory WHERE updated_at < ?",
            (now - RETENTION_SECONDS,),
        )
        connection.execute(
            """
            DELETE FROM chat_memory
            WHERE chat_key IN (
                SELECT chat_key
                FROM chat_memory
                ORDER BY updated_at DESC, chat_key DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (MAX_CHAT_ROWS,),
        )


def _clear_sync(key: Any) -> None:
    _initialize_sync()
    chat_key = _key_to_text(key)

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            DELETE FROM chat_memory
            WHERE chat_key = ?
            """,
            (chat_key,),
        )


async def load_chat_history(
    key: Any,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _load_sync,
        key,
    )


async def save_chat_history(
    key: Any,
    history: Any,
) -> None:
    async with _DB_LOCK:
        await asyncio.to_thread(
            _save_sync,
            key,
            history,
        )


async def clear_chat_history(
    key: Any,
) -> None:
    async with _DB_LOCK:
        await asyncio.to_thread(
            _clear_sync,
            key,
        )
