from __future__ import annotations

from contextlib import closing
import asyncio
import logging
import math
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from bot.core.config_manager import Config


LOGGER = logging.getLogger(__name__)

DB_PATH = Path(
    os.getenv(
        "ATRI_STICKER_DB",
        "/app/atri_data/stickers.sqlite3",
    )
)

DB_LOCK = asyncio.Lock()

DEFAULT_SETTINGS = {
    "learn_enabled": "1",
    "reply_enabled": "1",
    "reply_chance": "12",
    "cooldown_seconds": "0",
    "max_per_hour": "0",
}


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
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS stickers (
                file_unique_id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                emoji TEXT,
                set_name TEXT,
                is_animated INTEGER NOT NULL DEFAULT 0,
                is_video INTEGER NOT NULL DEFAULT 0,
                seen_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                first_chat_id INTEGER,
                last_chat_id INTEGER,
                last_user_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS sticker_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sticker_sent_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                file_unique_id TEXT NOT NULL,
                sent_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_stickers_last_chat
            ON stickers(last_chat_id);

            CREATE INDEX IF NOT EXISTS idx_sticker_sent_chat_time
            ON sticker_sent_history(chat_id, sent_at);
            """
        )

        for key, value in DEFAULT_SETTINGS.items():
            connection.execute(
                """
                INSERT OR IGNORE INTO sticker_settings(
                    setting_key,
                    setting_value
                ) VALUES (?, ?)
                """,
                (key, value),
            )


def _get_setting_sync(key: str) -> str:
    _initialize_sync()

    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT setting_value
            FROM sticker_settings
            WHERE setting_key = ?
            """,
            (key,),
        ).fetchone()

    return (
        str(row["setting_value"])
        if row
        else DEFAULT_SETTINGS.get(key, "")
    )


def _set_setting_sync(key: str, value: str) -> None:
    _initialize_sync()

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO sticker_settings(
                setting_key,
                setting_value
            ) VALUES (?, ?)
            ON CONFLICT(setting_key)
            DO UPDATE SET setting_value = excluded.setting_value
            """,
            (key, str(value)),
        )


async def _get_setting(key: str) -> str:
    return await asyncio.to_thread(_get_setting_sync, key)


async def _set_setting(key: str, value: str) -> None:
    async with DB_LOCK:
        await asyncio.to_thread(
            _set_setting_sync,
            key,
            value,
        )


async def _setting_bool(key: str) -> bool:
    value = await _get_setting(key)
    return value.strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def _setting_int(
    key: str,
    default: int,
) -> int:
    try:
        return int(await _get_setting(key))
    except (TypeError, ValueError):
        return default


def _learn_sync(
    *,
    file_id: str,
    file_unique_id: str,
    emoji: str,
    set_name: str,
    is_animated: bool,
    is_video: bool,
    chat_id: int,
    user_id: int,
) -> None:
    _initialize_sync()
    now = int(time.time())

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO stickers(
                file_unique_id,
                file_id,
                emoji,
                set_name,
                is_animated,
                is_video,
                seen_count,
                first_seen_at,
                last_seen_at,
                first_chat_id,
                last_chat_id,
                last_user_id
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(file_unique_id)
            DO UPDATE SET
                file_id = excluded.file_id,
                emoji = excluded.emoji,
                set_name = excluded.set_name,
                is_animated = excluded.is_animated,
                is_video = excluded.is_video,
                seen_count = stickers.seen_count + 1,
                last_seen_at = excluded.last_seen_at,
                last_chat_id = excluded.last_chat_id,
                last_user_id = excluded.last_user_id
            """,
            (
                file_unique_id,
                file_id,
                emoji,
                set_name,
                int(is_animated),
                int(is_video),
                now,
                now,
                chat_id,
                chat_id,
                user_id,
            ),
        )


async def learn_sticker_from_message(
    message: Any,
) -> bool:
    if not await _setting_bool("learn_enabled"):
        return False

    sticker = getattr(message, "sticker", None)

    if sticker is None:
        return False

    user = getattr(message, "from_user", None)

    if bool(getattr(user, "is_bot", False)):
        return False

    file_id = str(
        getattr(sticker, "file_id", "") or ""
    ).strip()
    file_unique_id = str(
        getattr(sticker, "file_unique_id", "") or ""
    ).strip()

    if not file_id or not file_unique_id:
        return False

    chat = getattr(message, "chat", None)

    chat_id = int(
        getattr(chat, "id", 0) or 0
    )
    user_id = int(
        getattr(user, "id", 0) or 0
    )

    async with DB_LOCK:
        await asyncio.to_thread(
            _learn_sync,
            file_id=file_id,
            file_unique_id=file_unique_id,
            emoji=str(
                getattr(sticker, "emoji", "") or ""
            ),
            set_name=str(
                getattr(sticker, "set_name", "") or ""
            ),
            is_animated=bool(
                getattr(sticker, "is_animated", False)
            ),
            is_video=bool(
                getattr(sticker, "is_video", False)
            ),
            chat_id=chat_id,
            user_id=user_id,
        )

    return True


def _recent_send_state_sync(
    chat_id: int,
) -> tuple[int | None, int]:
    _initialize_sync()

    now = int(time.time())
    hour_ago = now - 3600

    with closing(_connect()) as connection, connection:
        latest = connection.execute(
            """
            SELECT MAX(sent_at) AS latest
            FROM sticker_sent_history
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()

        hourly = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM sticker_sent_history
            WHERE chat_id = ?
              AND sent_at >= ?
            """,
            (chat_id, hour_ago),
        ).fetchone()

    latest_value = (
        int(latest["latest"])
        if latest and latest["latest"] is not None
        else None
    )
    hourly_value = (
        int(hourly["total"])
        if hourly
        else 0
    )

    return latest_value, hourly_value


def _candidate_rows_sync(
    chat_id: int,
    exclude_unique_id: str,
) -> list[sqlite3.Row]:
    _initialize_sync()

    with closing(_connect()) as connection, connection:
        recent_rows = connection.execute(
            """
            SELECT file_unique_id
            FROM sticker_sent_history
            WHERE chat_id = ?
            ORDER BY sent_at DESC
            LIMIT 6
            """,
            (chat_id,),
        ).fetchall()

        excluded = {
            str(row["file_unique_id"])
            for row in recent_rows
        }

        if exclude_unique_id:
            excluded.add(exclude_unique_id)

        rows = connection.execute(
            """
            SELECT *
            FROM stickers
            ORDER BY
                CASE
                    WHEN last_chat_id = ? THEN 0
                    ELSE 1
                END,
                last_seen_at DESC
            LIMIT 1000
            """,
            (chat_id,),
        ).fetchall()

    return [
        row
        for row in rows
        if str(row["file_unique_id"]) not in excluded
    ]


def _record_sent_sync(
    chat_id: int,
    file_unique_id: str,
) -> None:
    _initialize_sync()

    now = int(time.time())

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            INSERT INTO sticker_sent_history(
                chat_id,
                file_unique_id,
                sent_at
            ) VALUES (?, ?, ?)
            """,
            (chat_id, file_unique_id, now),
        )

        connection.execute(
            """
            DELETE FROM sticker_sent_history
            WHERE sent_at < ?
            """,
            (now - 604800,),
        )


def _delete_sticker_sync(
    file_unique_id: str,
) -> None:
    _initialize_sync()

    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            DELETE FROM stickers
            WHERE file_unique_id = ?
            """,
            (file_unique_id,),
        )


async def maybe_send_random_sticker(
    client: Any,
    message: Any,
    *,
    reason: str = "ai_reply",
) -> bool:
    if not await _setting_bool("reply_enabled"):
        return False

    base_chance = max(
        0,
        min(
            await _setting_int(
                "reply_chance",
                12,
            ),
            100,
        ),
    )

    chance = (
        min(100, base_chance * 2)
        if reason == "sticker"
        else base_chance
    )

    if chance <= 0:
        return False

    if random.uniform(0, 100) > chance:
        return False

    chat = getattr(message, "chat", None)
    chat_id = int(
        getattr(chat, "id", 0) or 0
    )

    if not chat_id:
        return False

    cooldown = max(
        0,
        await _setting_int(
            "cooldown_seconds",
            180,
        ),
    )
    max_per_hour = max(
        0,
        await _setting_int(
            "max_per_hour",
            0,
        ),
    )

    latest, hourly = await asyncio.to_thread(
        _recent_send_state_sync,
        chat_id,
    )

    now = int(time.time())

    if latest is not None and now - latest < cooldown:
        return False

    if max_per_hour > 0 and hourly >= max_per_hour:
        return False

    incoming_sticker = getattr(
        message,
        "sticker",
        None,
    )
    exclude_unique_id = str(
        getattr(
            incoming_sticker,
            "file_unique_id",
            "",
        )
        or ""
    )

    rows = await asyncio.to_thread(
        _candidate_rows_sync,
        chat_id,
        exclude_unique_id,
    )

    if not rows:
        return False

    weights = [
        min(
            20.0,
            1.0
            + math.sqrt(
                max(
                    1,
                    int(row["seen_count"]),
                )
            ),
        )
        for row in rows
    ]

    selected = random.choices(
        rows,
        weights=weights,
        k=1,
    )[0]

    file_id = str(selected["file_id"])
    file_unique_id = str(
        selected["file_unique_id"]
    )

    try:
        await client.send_sticker(
            chat_id=chat_id,
            sticker=file_id,
            reply_to_message_id=getattr(
                message,
                "id",
                None,
            ),
            disable_notification=True,
        )
    except Exception:
        LOGGER.exception(
            "Không gửi được sticker %s",
            file_unique_id,
        )

        async with DB_LOCK:
            await asyncio.to_thread(
                _delete_sticker_sync,
                file_unique_id,
            )

        return False

    async with DB_LOCK:
        await asyncio.to_thread(
            _record_sent_sync,
            chat_id,
            file_unique_id,
        )

    return True


def _stats_sync() -> dict[str, int]:
    _initialize_sync()

    with closing(_connect()) as connection, connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS unique_stickers,
                COALESCE(SUM(seen_count), 0) AS total_seen,
                COALESCE(SUM(is_animated), 0) AS animated,
                COALESCE(SUM(is_video), 0) AS video
            FROM stickers
            """
        ).fetchone()

        sent = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM sticker_sent_history
            """
        ).fetchone()

    return {
        "unique_stickers": int(
            row["unique_stickers"]
        ),
        "total_seen": int(row["total_seen"]),
        "animated": int(row["animated"]),
        "video": int(row["video"]),
        "sent": int(sent["total"]),
    }


async def get_sticker_state() -> dict[str, Any]:
    return {
        "learn_enabled": await _setting_bool(
            "learn_enabled"
        ),
        "reply_enabled": await _setting_bool(
            "reply_enabled"
        ),
        "reply_chance": await _setting_int(
            "reply_chance",
            12,
        ),
        "cooldown_seconds": await _setting_int(
            "cooldown_seconds",
            180,
        ),
        "max_per_hour": await _setting_int(
            "max_per_hour",
            8,
        ),
    }


async def handle_sticker_control(
    message: Any,
    command: str,
    argument: str,
) -> bool:
    user = getattr(message, "from_user", None)
    user_id = int(
        getattr(user, "id", 0) or 0
    )

    if user_id != int(Config.OWNER_ID):
        await message.reply_text(
            "Chỉ Prix mới được thay đổi cấu hình sticker.",
            quote=True,
            parse_mode=None,
        )
        return True

    value = str(argument or "").strip().casefold()

    if command in {
        "stickerlearn",
        "stickerreply",
    }:
        key = (
            "learn_enabled"
            if command == "stickerlearn"
            else "reply_enabled"
        )

        if not value:
            state = await get_sticker_state()
            current = bool(state[key])

            await message.reply_text(
                f"{command}: "
                f"{'on' if current else 'off'}",
                quote=True,
                parse_mode=None,
            )
            return True

        if value not in {"on", "off"}:
            await message.reply_text(
                f"Dùng: /{command} on hoặc off",
                quote=True,
                parse_mode=None,
            )
            return True

        await _set_setting(
            key,
            "1" if value == "on" else "0",
        )

        await message.reply_text(
            f"Đã đặt {command}: {value}",
            quote=True,
            parse_mode=None,
        )
        return True

    if command == "stickerchance":
        if not value:
            state = await get_sticker_state()

            await message.reply_text(
                "Xác suất gửi sticker hiện tại: "
                f"{state['reply_chance']}%",
                quote=True,
                parse_mode=None,
            )
            return True

        try:
            chance = int(value)
        except ValueError:
            chance = -1

        if not 0 <= chance <= 100:
            await message.reply_text(
                "Dùng: /stickerchance 0-100",
                quote=True,
                parse_mode=None,
            )
            return True

        await _set_setting(
            "reply_chance",
            str(chance),
        )

        await message.reply_text(
            f"Đã đặt xác suất sticker: {chance}%",
            quote=True,
            parse_mode=None,
        )
        return True

    if command == "stickercooldown":
        default_cooldown = int(
            DEFAULT_SETTINGS["cooldown_seconds"]
        )

        if not value:
            state = await get_sticker_state()

            await message.reply_text(
                "Cooldown sticker hiện tại: "
                f"{state['cooldown_seconds']} giây/chat\n"
                "Dùng: /stickercooldown 0-86400\n"
                "Hoặc: /stickercooldown default",
                quote=True,
                parse_mode=None,
            )
            return True

        if value == "default":
            cooldown = default_cooldown
        else:
            try:
                cooldown = int(value)
            except ValueError:
                cooldown = -1

        if not 0 <= cooldown <= 86400:
            await message.reply_text(
                "Cooldown phải từ 0 đến 86400 giây.\n"
                "Dùng 0 để tắt cooldown.",
                quote=True,
                parse_mode=None,
            )
            return True

        await _set_setting(
            "cooldown_seconds",
            str(cooldown),
        )

        await message.reply_text(
            "Đã đặt cooldown sticker: "
            f"{cooldown} giây/chat.",
            quote=True,
            parse_mode=None,
        )
        return True

    if command == "stickerlimit":
        default_limit = int(
            DEFAULT_SETTINGS["max_per_hour"]
        )

        if not value:
            state = await get_sticker_state()

            await message.reply_text(
                "Giới hạn sticker hiện tại: "
                f"{state['max_per_hour']}/giờ/chat\n"
                "Dùng: /stickerlimit 0-100\n"
                "Hoặc: /stickerlimit default",
                quote=True,
                parse_mode=None,
            )
            return True

        if value == "default":
            limit = default_limit
        else:
            try:
                limit = int(value)
            except ValueError:
                limit = -1

        if not 0 <= limit <= 100:
            await message.reply_text(
                "Giới hạn phải từ 0 đến 100 sticker/giờ/chat.\n"
                "Dùng 0 để bỏ giới hạn.",
                quote=True,
                parse_mode=None,
            )
            return True

        await _set_setting(
            "max_per_hour",
            str(limit),
        )

        await message.reply_text(
            "Đã đặt giới hạn sticker: "
            f"{limit}/giờ/chat.",
            quote=True,
            parse_mode=None,
        )
        return True

    if command == "stickerstats":
        state = await get_sticker_state()
        stats = await asyncio.to_thread(_stats_sync)

        await message.reply_text(
            "Thống kê sticker Atri\n"
            f"Học: {'on' if state['learn_enabled'] else 'off'}\n"
            f"Gửi: {'on' if state['reply_enabled'] else 'off'}\n"
            f"Xác suất: {state['reply_chance']}%\n"
            f"Cooldown: {state['cooldown_seconds']} giây\n"
            f"Giới hạn: {state['max_per_hour']}/giờ/chat\n"
            f"Sticker khác nhau: {stats['unique_stickers']}\n"
            f"Tổng lượt quan sát: {stats['total_seen']}\n"
            f"Sticker động: {stats['animated']}\n"
            f"Sticker video: {stats['video']}\n"
            f"Đã gửi: {stats['sent']}",
            quote=True,
            parse_mode=None,
        )
        return True

    return False
