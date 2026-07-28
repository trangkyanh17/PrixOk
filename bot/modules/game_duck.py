from __future__ import annotations

from html import escape
from secrets import SystemRandom
from time import time
from typing import Any

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    buff_active,
    capped_xp_gain,
    ensure_message_user,
    entertainment_guard,
    format_number,
    minigame_coin_reward,
    remaining_seconds,
    require_game_collection,
    require_user,
    user_lock,
)


RNG = SystemRandom()
DUCK_RACE_COOLDOWN = 30
DUCK_COINS_PER_METER = 25
DUCK_XP_DISTANCE_DIVISOR = 10

DUCK_BOATS: dict[str, dict[str, Any]] = {
    "thuyen_vit_go": {
        "name": "Thuyền Vịt Gỗ",
        "price": 0,
        "min_distance": 100,
        "max_distance": 220,
        "description": "Thuyền mặc định, miễn phí.",
    },
    "thuyen_vit_dong": {
        "name": "Thuyền Vịt Đồng",
        "price": 2_000_000,
        "min_distance": 180,
        "max_distance": 340,
        "description": "Khung đồng chắc hơn, quãng đường ổn định.",
    },
    "thuyen_vit_bac": {
        "name": "Thuyền Vịt Bạc",
        "price": 10_000_000,
        "min_distance": 300,
        "max_distance": 520,
        "description": "Nhẹ hơn và giữ tốc độ tốt trên quãng dài.",
    },
    "thuyen_vit_vang": {
        "name": "Thuyền Vịt Vàng",
        "price": 50_000_000,
        "min_distance": 480,
        "max_distance": 760,
        "description": "Động cơ cao cấp, phần thưởng lớn hơn rõ rệt.",
    },
    "thuyen_vit_graphine": {
        "name": "Thuyền Vịt Graphine",
        "price": 200_000_000,
        "min_distance": 700,
        "max_distance": 1_100,
        "description": "Mẫu nhanh nhất, tối ưu cho quãng đường cực xa.",
    },
}


def selected_duck_boat(user_doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    boat_id = str(user_doc.get("equipped_duck_boat") or "thuyen_vit_go")
    if boat_id not in DUCK_BOATS:
        boat_id = "thuyen_vit_go"
    owned = user_doc.get("duck_boats", {})
    if boat_id != "thuyen_vit_go" and (
        not isinstance(owned, dict) or boat_id not in owned
    ):
        boat_id = "thuyen_vit_go"
    return boat_id, DUCK_BOATS[boat_id]


@new_task
@entertainment_guard
async def duck_race(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return

        remaining = remaining_seconds(
            user_doc.get("cooldowns", {}).get("duck_race"),
            DUCK_RACE_COOLDOWN,
        )
        if remaining:
            await send_message(
                message,
                f"⏳ Thuyền đang bảo dưỡng. Chờ <b>{remaining} giây</b> để đua tiếp.",
            )
            return

        boat_id, boat = selected_duck_boat(user_doc)
        distance = RNG.randint(
            int(boat["min_distance"]),
            int(boat["max_distance"]),
        )
        base_coins = distance * DUCK_COINS_PER_METER
        coin_gain = minigame_coin_reward(user_doc, base_coins)
        base_xp = max(5, distance // DUCK_XP_DISTANCE_DIVISOR)
        xp_gain = capped_xp_gain(user_doc, base_xp)
        now = time()

        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$set": {
                    "cooldowns.duck_race": now,
                    "equipped_duck_boat": boat_id,
                    "updated_at": now,
                },
                "$inc": {
                    "coins": coin_gain,
                    "xp": xp_gain,
                    "stats.duck_races": 1,
                    "stats.duck_distance": distance,
                    "stats.duck_coins": coin_gain,
                    "stats.duck_xp": xp_gain,
                },
                "$max": {"stats.duck_best_distance": distance},
            },
        )

    coin_buff = " · bùa x2 tiền" if buff_active(user_doc, "coin_buff_until") else ""
    xp_buff = " · bùa x2 EXP" if buff_active(user_doc, "xp_buff_until") else ""
    await send_message(
        message,
        "🦆 <b>Đua Vịt</b>\n\n"
        f"🚤 Thuyền: <b>{escape(str(boat['name']))}</b>\n"
        f"📏 Quãng đường: <b>{format_number(distance)} m</b>\n"
        f"💰 Thưởng: <b>{format_number(coin_gain)} xu</b>{coin_buff}\n"
        f"⭐ EXP: <b>+{format_number(xp_gain)}</b>{xp_buff}\n"
        f"⏱ Hồi lệnh: <b>{DUCK_RACE_COOLDOWN} giây</b>\n\n"
        "Mua thuyền tốt hơn tại <code>/shop</code> bằng <code>/buy tên_vật_phẩm</code>.",
    )
