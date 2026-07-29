from __future__ import annotations

import asyncio
from html import escape
from time import time

from pymongo import ReturnDocument
from pymongo.errors import ConfigurationError

from bot import LOGGER
from bot.core.config_manager import Config

try:
    from motor.motor_asyncio import AsyncIOMotorClient as _MongoClient
except ImportError:  # pragma: no cover
    from pymongo import AsyncMongoClient as _MongoClient


COIN_NAME = "xu"
START_COINS = 5_000

_client = None
_db = None
_init_lock = asyncio.Lock()


def command_name(base: str) -> str:
    """Ghép hậu tố lệnh của bot (CMD_SUFFIX) nếu có."""
    suffix = getattr(Config, "CMD_SUFFIX", "") or ""
    return f"{base}{suffix}"


def _database_name() -> str:
    for attribute in ("DATABASE_NAME", "DB_NAME"):
        value = getattr(Config, attribute, "") or ""
        if value:
            return value
    return "mltb"


async def get_db():
    global _client, _db
    if _db is not None:
        return _db

    async with _init_lock:
        if _db is not None:
            return _db

        uri = (getattr(Config, "DATABASE_URL", "") or "").strip()
        if not uri:
            raise RuntimeError(
                "DATABASE_URL chưa được cấu hình nên hệ thống tiền tệ không chạy được."
            )

        client = _MongoClient(uri)
        try:
            database = client.get_default_database()
        except ConfigurationError:
            database = None
        if database is None:
            database = client[_database_name()]

        try:
            await database.game_users.create_index("coins")
        except Exception as error:
            LOGGER.warning(f"game: không tạo được index coins ({error})")

        _client = client
        _db = database
        return _db


def raw_name(user) -> str:
    """Tên gốc, chưa escape. Dùng để lưu DB và làm nhãn nút bấm."""
    if user is None:
        return "Ẩn danh"
    name = getattr(user, "first_name", None) or getattr(user, "username", None) or "Ẩn danh"
    return str(name)[:32]


def display_name(user) -> str:
    """Tên đã escape HTML, dùng khi ghép vào tin nhắn."""
    return escape(raw_name(user))


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def format_coins(amount: int) -> str:
    return f"{int(amount):,}".replace(",", ".") + f" {COIN_NAME}"


async def get_user(user_id: int, name: str | None = None) -> dict:
    db = await get_db()
    on_insert = {
        "coins": START_COINS,
        "wins": 0,
        "losses": 0,
        "streak": 0,
        "best_streak": 0,
        "last_duck": 0.0,
        "created_at": time(),
    }
    update: dict = {}
    if name:
        update["$set"] = {"name": name}
    else:
        on_insert["name"] = ""
    update["$setOnInsert"] = on_insert

    return await db.game_users.find_one_and_update(
        {"_id": user_id},
        update,
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def get_coins(user_id: int) -> int:
    document = await get_user(user_id)
    return int(document.get("coins", 0))


async def add_coins(user_id: int, amount: int, name: str | None = None) -> int:
    await get_user(user_id, name)
    db = await get_db()
    document = await db.game_users.find_one_and_update(
        {"_id": user_id},
        {"$inc": {"coins": int(amount)}},
        return_document=ReturnDocument.AFTER,
    )
    return int(document.get("coins", 0))


async def take_coins(user_id: int, amount: int, name: str | None = None) -> bool:
    """Trừ tiền có kiểm tra số dư. Trả về False nếu không đủ."""
    amount = int(amount)
    if amount <= 0:
        return True
    await get_user(user_id, name)
    db = await get_db()
    result = await db.game_users.update_one(
        {"_id": user_id, "coins": {"$gte": amount}},
        {"$inc": {"coins": -amount}},
    )
    return result.modified_count == 1


async def set_coins(user_id: int, amount: int) -> int:
    await get_user(user_id)
    db = await get_db()
    document = await db.game_users.find_one_and_update(
        {"_id": user_id},
        {"$set": {"coins": max(0, int(amount))}},
        return_document=ReturnDocument.AFTER,
    )
    return int(document.get("coins", 0))


async def record_result(user_id: int, won: bool) -> int:
    """Cập nhật thắng/thua, trả về chuỗi thắng hiện tại."""
    db = await get_db()
    if not won:
        await db.game_users.update_one(
            {"_id": user_id},
            {"$inc": {"losses": 1}, "$set": {"streak": 0}},
        )
        return 0

    document = await db.game_users.find_one_and_update(
        {"_id": user_id},
        {"$inc": {"wins": 1, "streak": 1}},
        return_document=ReturnDocument.AFTER,
    )
    streak = int(document.get("streak", 1))
    if streak > int(document.get("best_streak", 0)):
        await db.game_users.update_one(
            {"_id": user_id},
            {"$set": {"best_streak": streak}},
        )
    return streak


async def touch_duck(user_id: int) -> None:
    db = await get_db()
    await db.game_users.update_one({"_id": user_id}, {"$set": {"last_duck": time()}})


async def record_duck(user_id: int, distance: int, reward: int) -> None:
    """Ghi lại một lượt đua vịt vào hồ sơ người chơi."""
    db = await get_db()
    await db.game_users.update_one(
        {"_id": user_id},
        {
            "$inc": {"duck_races": 1, "duck_earned": int(reward)},
            "$max": {"duck_best": int(distance)},
            "$set": {"last_duck": time()},
        },
    )


async def top_users(limit: int = 10) -> list[dict]:
    db = await get_db()
    cursor = db.game_users.find({}).sort("coins", -1).limit(limit)
    return await cursor.to_list(limit)


async def coin_rank(user_id: int) -> int:
    """Thứ hạng theo số dư, tính từ 1."""
    db = await get_db()
    document = await db.game_users.find_one({"_id": user_id})
    if document is None:
        return 0
    higher = await db.game_users.count_documents(
        {"coins": {"$gt": int(document.get("coins", 0))}}
    )
    return higher + 1


async def top_winrate(limit: int = 10, min_games: int = 5) -> list[dict]:
    """Xếp hạng tỉ lệ thắng ma sói, chỉ tính người đủ số ván tối thiểu."""
    db = await get_db()
    pipeline = [
        {
            "$addFields": {
                "_wins": {"$ifNull": ["$wins", 0]},
                "_losses": {"$ifNull": ["$losses", 0]},
            }
        },
        {"$addFields": {"games": {"$add": ["$_wins", "$_losses"]}}},
        {"$match": {"games": {"$gte": max(1, min_games)}}},
        {"$addFields": {"rate": {"$divide": ["$_wins", "$games"]}}},
        {"$sort": {"rate": -1, "_wins": -1, "games": -1}},
        {"$limit": limit},
    ]
    cursor = db.game_users.aggregate(pipeline)
    return await cursor.to_list(limit)


async def entertainment_enabled(chat_id: int) -> bool:
    try:
        db = await get_db()
        document = await db.game_settings.find_one({"_id": f"chat:{chat_id}"})
    except Exception as error:
        LOGGER.error(f"game: đọc game_settings lỗi ({error})")
        return True
    if document is None:
        return True
    return bool(document.get("enabled", True))


async def set_entertainment(chat_id: int, enabled: bool) -> None:
    db = await get_db()
    await db.game_settings.update_one(
        {"_id": f"chat:{chat_id}"},
        {"$set": {"enabled": bool(enabled), "updated_at": time()}},
        upsert=True,
    )
