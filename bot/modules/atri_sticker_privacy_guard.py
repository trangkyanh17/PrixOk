from __future__ import annotations

# ATRI_STICKER_CHAT_PRIVACY_V154
# Learned Telegram stickers are private conversation-derived media. The legacy
# sticker table is global and only remembers first_chat_id/last_chat_id, so its
# candidate query could replay a sticker learned in one chat into another.
#
# This guard adds a normalized chat-scope table. Existing rows are migrated only
# from chat IDs the legacy schema can prove (first + last). Intermediate chats
# are intentionally not guessed; seeing that sticker again records the scope.

import logging
from contextlib import closing
from typing import Any


_LOGGER = logging.getLogger("bot")
_INSTALLED = False


def install_atri_sticker_privacy_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from bot.modules import atri_stickers as stickers

    if getattr(stickers, "_ATRI_V154_CHAT_SCOPE", False):
        _INSTALLED = True
        return

    original_initialize = stickers._initialize_sync
    original_learn = stickers._learn_sync
    original_candidates = stickers._candidate_rows_sync
    original_delete = stickers._delete_sticker_sync

    def guarded_initialize_sync() -> None:
        original_initialize()
        with closing(stickers._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sticker_chat_scope (
                    file_unique_id TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    PRIMARY KEY(file_unique_id, chat_id)
                );
                CREATE INDEX IF NOT EXISTS idx_sticker_chat_scope_chat
                    ON sticker_chat_scope(chat_id, last_seen_at DESC);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO sticker_chat_scope(
                    file_unique_id, chat_id, last_seen_at
                )
                SELECT file_unique_id, first_chat_id, last_seen_at
                FROM stickers
                WHERE first_chat_id IS NOT NULL AND first_chat_id != 0
                """
            )
            connection.execute(
                """
                INSERT INTO sticker_chat_scope(
                    file_unique_id, chat_id, last_seen_at
                )
                SELECT file_unique_id, last_chat_id, last_seen_at
                FROM stickers
                WHERE last_chat_id IS NOT NULL AND last_chat_id != 0
                ON CONFLICT(file_unique_id, chat_id)
                DO UPDATE SET last_seen_at = MAX(
                    sticker_chat_scope.last_seen_at,
                    excluded.last_seen_at
                )
                """
            )

    def guarded_learn_sync(**kwargs: Any) -> None:
        original_learn(**kwargs)
        file_unique_id = str(kwargs.get("file_unique_id") or "").strip()
        try:
            chat_id = int(kwargs.get("chat_id") or 0)
        except (TypeError, ValueError):
            chat_id = 0
        if not file_unique_id or not chat_id:
            return
        guarded_initialize_sync()
        with closing(stickers._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO sticker_chat_scope(
                    file_unique_id, chat_id, last_seen_at
                ) VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(file_unique_id, chat_id)
                DO UPDATE SET last_seen_at = excluded.last_seen_at
                """,
                (file_unique_id, chat_id),
            )

    def guarded_candidate_rows_sync(
        chat_id: int,
        exclude_unique_id: str,
    ):
        guarded_initialize_sync()
        rows = original_candidates(chat_id, exclude_unique_id)
        with closing(stickers._connect()) as connection, connection:
            allowed = {
                str(row["file_unique_id"])
                for row in connection.execute(
                    """
                    SELECT file_unique_id
                    FROM sticker_chat_scope
                    WHERE chat_id = ?
                    """,
                    (int(chat_id),),
                ).fetchall()
            }
        return [
            row
            for row in rows
            if str(row["file_unique_id"]) in allowed
        ]

    def guarded_delete_sticker_sync(file_unique_id: str) -> None:
        original_delete(file_unique_id)
        guarded_initialize_sync()
        with closing(stickers._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM sticker_chat_scope WHERE file_unique_id = ?",
                (str(file_unique_id),),
            )

    stickers._initialize_sync = guarded_initialize_sync
    stickers._learn_sync = guarded_learn_sync
    stickers._candidate_rows_sync = guarded_candidate_rows_sync
    stickers._delete_sticker_sync = guarded_delete_sticker_sync
    stickers._ATRI_V154_CHAT_SCOPE = True
    _INSTALLED = True
    _LOGGER.info("ATRI_STICKER_CHAT_PRIVACY_V154_INSTALLED")
