from __future__ import annotations

from html import escape
from secrets import SystemRandom
from time import time

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    DISCIPLE_FUSION_SECONDS,
    DISCIPLE_PRICE,
    EQUIPMENT_SETS,
    FUSION_POTION_PRICE,
    MAX_EQUIPMENT_MERGE_LEVEL,
    MAX_PLAYER_LEVEL,
    disciple_hp_state,
    disciple_max_hp,
    disciple_state,
    disciple_summary,
    ensure_message_user,
    entertainment_guard,
    format_number,
    player_hp_state,
    player_max_hp,
    require_game_collection,
    require_user,
    resolve_target,
    user_lock,
)

RNG = SystemRandom()

DISCIPLE_SHOP_ITEMS = {
    "de_tu": {
        "name": "Đệ Tử Ngẫu Nhiên",
        "price": DISCIPLE_PRICE,
        "aliases": {
            "de_tu",
            "detu",
            "đệ_tử",
            "đệ tử",
            "disciple",
        },
        "description": (
            "Nhận ngẫu nhiên đệ tử Nam hoặc Nữ. Mua lại sẽ thay đệ tử hiện tại."
        ),
    },
    "thuoc_hop_the": {
        "name": "Thuốc Hợp Thể Vĩnh Viễn",
        "price": FUSION_POTION_PRICE,
        "aliases": {
            "thuoc_hop_the",
            "thuochopthe",
            "thuốc_hợp_thể",
            "thuốc hợp thể",
            "fusion_potion",
        },
        "description": (
            "Sở hữu vĩnh viễn, không giới hạn lượt dùng. Kích hoạt bằng /thuoc."
        ),
    },
}


def random_disciple_gender() -> str:
    return RNG.choice(("male", "female"))


@new_task
@entertainment_guard
async def disciple_command(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return
    user_doc = await ensure_message_user(collection, message)
    if user_doc is None:
        return

    await send_message(
        message,
        disciple_summary(user_doc)
        + "\n\nMua hoặc đổi đệ tử: <code>/buy de_tu</code> — 100.000.000 xu.\n"
        "Hợp thể 10 phút: <code>/hopthe</code>.\n"
        "Dùng thuốc hợp thể vĩnh viễn: <code>/thuoc</code>.",
    )


@new_task
@entertainment_guard
async def fuse_with_disciple(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    user_id = int(message.from_user.id)
    async with user_lock(user_id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        disciple = disciple_state(user_doc)
        if disciple is None:
            await send_message(
                message,
                "❌ Chưa có đệ tử. Mua bằng <code>/buy de_tu</code>.",
            )
            return

        disciple_hp, disciple_max, remaining = disciple_hp_state(user_doc)
        if disciple_hp <= 0:
            await send_message(
                message,
                f"💀 Đệ tử đang hồi sinh, còn <b>{remaining // 60} phút "
                f"{remaining % 60} giây</b>.",
            )
            return

        if bool(user_doc.get("disciple_fusion_permanent", False)):
            await send_message(message, "♾ Đệ tử đã hợp thể vĩnh viễn với sư phụ.")
            return

        now = time()
        fusion_until = now + DISCIPLE_FUSION_SECONDS
        temp_doc = dict(user_doc)
        temp_doc["disciple_fusion_until"] = fusion_until
        new_max_hp = player_max_hp(temp_doc)
        hp, _, _ = player_hp_state(user_doc)
        boosted_hp = min(new_max_hp, hp + disciple_max)

        await collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "disciple_fusion_until": fusion_until,
                    "hp": boosted_hp,
                    "max_hp": new_max_hp,
                    "hp_regen_at": now,
                    "updated_at": now,
                },
                "$inc": {"stats.disciple_fusions": 1},
            },
        )

    await send_message(
        message,
        "✨ <b>HỢP THỂ THÀNH CÔNG!</b>\n\n"
        "Toàn bộ chỉ số và năng lực đặc biệt của đệ tử đã cộng vào sư phụ "
        f"trong <b>{DISCIPLE_FUSION_SECONDS // 60} phút</b>.",
    )


@new_task
@entertainment_guard
async def use_fusion_potion(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    user_id = int(message.from_user.id)
    async with user_lock(user_id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        disciple = disciple_state(user_doc)
        if disciple is None:
            await send_message(
                message,
                "❌ Chưa có đệ tử. Mua bằng <code>/buy de_tu</code>.",
            )
            return
        if not bool(user_doc.get("fusion_potion_owned", False)):
            await send_message(
                message,
                "❌ Chưa sở hữu Thuốc Hợp Thể Vĩnh Viễn. "
                "Mua bằng <code>/buy thuoc_hop_the</code> với giá "
                "<b>500.000.000 xu</b>.",
            )
            return

        disciple_hp, disciple_max, remaining = disciple_hp_state(user_doc)
        if disciple_hp <= 0:
            await send_message(
                message,
                f"💀 Đệ tử đang hồi sinh, còn <b>{remaining // 60} phút "
                f"{remaining % 60} giây</b>.",
            )
            return

        now = time()
        temp_doc = dict(user_doc)
        temp_doc["disciple_fusion_permanent"] = True
        temp_doc["disciple_fusion_until"] = 0
        new_max_hp = player_max_hp(temp_doc)
        hp, _, _ = player_hp_state(user_doc)
        boosted_hp = min(new_max_hp, hp + disciple_max)

        await collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "disciple_fusion_permanent": True,
                    "disciple_fusion_until": 0,
                    "hp": boosted_hp,
                    "max_hp": new_max_hp,
                    "hp_regen_at": now,
                    "updated_at": now,
                },
                "$inc": {"stats.disciple_permanent_fusions": 1},
            },
        )

    await send_message(
        message,
        "♾ <b>HỢP THỂ VĨNH VIỄN!</b>\n\n"
        "Thuốc không bị tiêu hao và có thể dùng lại không giới hạn sau khi đổi đệ tử.",
    )


@new_task
async def max_disciple(_, message):
    collection = await require_game_collection(message)
    if collection is None:
        return

    parts = (message.text or "").split(maxsplit=1)
    target_raw = parts[1].strip() if len(parts) > 1 else None
    if not target_raw and not (
        message.reply_to_message and message.reply_to_message.from_user
    ):
        await send_message(
            message,
            "Cách dùng: <code>/maxdt user_id</code>, "
            "<code>/maxdt @username</code> hoặc reply người dùng.",
        )
        return

    target = await resolve_target(collection, message, target_raw)
    if target is None:
        return
    target_id, target_name = target
    target_id = int(target_id)

    async with user_lock(target_id):
        user_doc = await collection.find_one({"_id": target_id})
        if user_doc is None:
            await send_message(
                message,
                "❌ Người dùng chưa có hồ sơ game.",
            )
            return
        disciple = disciple_state(user_doc)
        if disciple is None:
            await send_message(
                message,
                "❌ Người dùng này chưa có đệ tử.",
            )
            return

        gender = str(disciple.get("gender") or "male")
        maxed = dict(disciple)
        maxed["maxed"] = True
        maxed["equipment_set"] = "graphine_toi_thuong"
        maxed["merge_level"] = MAX_EQUIPMENT_MERGE_LEVEL
        maxed["indestructible"] = True
        maxed["dead_until"] = 0
        maxed["maxed_at"] = time()

        temp_doc = dict(user_doc)
        temp_doc["disciple"] = maxed
        max_hp = disciple_max_hp(temp_doc)
        maxed["hp"] = max_hp
        maxed["max_hp"] = max_hp

        await collection.update_one(
            {"_id": target_id},
            {
                "$set": {
                    "disciple": maxed,
                    "updated_at": time(),
                }
            },
        )

    template = EQUIPMENT_SETS["graphine_toi_thuong"]
    gender_name = "Nam" if gender == "male" else "Nữ"
    await send_message(
        message,
        f"✅ Đã max đệ tử của <b>{escape(str(target_name))}</b>.\n\n"
        f"Giới tính: <b>{gender_name}</b>\n"
        f"Cấp độ: <b>{MAX_PLAYER_LEVEL}/{MAX_PLAYER_LEVEL}</b>\n"
        f"HP: <b>{format_number(max_hp)}</b>\n"
        f"Trang bị: <b>{escape(str(template['name']))}</b>\n"
        f"Hợp nhất: <b>+{MAX_EQUIPMENT_MERGE_LEVEL}/"
        f"{MAX_EQUIPMENT_MERGE_LEVEL}</b>\n"
        "♾ Trang bị đặc quyền không tiêu hao độ bền.",
    )
