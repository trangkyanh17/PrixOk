from __future__ import annotations

from html import escape
from time import time

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    EQUIPMENT_SETS,
    MAX_EQUIPMENT_MERGE_LEVEL,
    MAX_PLAYER_LEVEL,
    MAX_PLAYER_XP,
    entertainment_guard,
    ensure_message_user,
    format_number,
    new_set_state,
    player_max_hp,
    require_game_collection,
    require_user,
    resolve_target,
    user_lock,
)


def _top_equipment_set() -> tuple[str, dict]:
    set_id = "graphine_toi_thuong"
    return set_id, EQUIPMENT_SETS[set_id]


def _maxed_set_state(set_id: str) -> dict:
    template = EQUIPMENT_SETS[set_id]
    base_durability = int(template["durability"])
    durability_bonus = base_durability * 2
    max_durability = base_durability + durability_bonus

    state = new_set_state(set_id)
    state.update(
        {
            "armor_owned": True,
            "weapon_owned": True,
            "armor_durability": max_durability,
            "weapon_durability": max_durability,
            "armor_durability_bonus": durability_bonus,
            "weapon_durability_bonus": durability_bonus,
            "armor_max_penalty": 0,
            "weapon_max_penalty": 0,
            "protection_bonus": int(template["protection"]),
            "protection_penalty": 0,
            "attack_penalty": 0.0,
            "merge_level": MAX_EQUIPMENT_MERGE_LEVEL,
            "successful_merges": MAX_EQUIPMENT_MERGE_LEVEL,
            "failed_merges": 0,
            "indestructible": True,
            "granted_by_maxlevel": True,
            "granted_at": time(),
        }
    )
    return state


@new_task
async def max_level_user(_, message):
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
            "Cách dùng: <code>/maxlevel user_id</code>, "
            "<code>/maxlevel @username</code> hoặc reply người dùng.",
        )
        return

    target = await resolve_target(collection, message, target_raw)
    if target is None:
        return
    target_id, target_name = target
    target_id = int(target_id)

    existing = await collection.find_one({"_id": target_id})
    if existing is None:
        await send_message(
            message,
            "❌ Người dùng chưa có hồ sơ game. Họ cần dùng ít nhất một "
            "lệnh game trước khi nhận /maxlevel.",
        )
        return

    set_id, template = _top_equipment_set()
    set_state = _maxed_set_state(set_id)
    maxed_doc = dict(existing)
    maxed_doc["xp"] = MAX_PLAYER_XP
    max_hp = player_max_hp(maxed_doc)
    now = time()

    async with user_lock(target_id):
        await collection.update_one(
            {"_id": target_id},
            {
                "$unset": {
                    "equipment_sets.graphine": "",
                },
                "$set": {
                    "xp": MAX_PLAYER_XP,
                    "hp": max_hp,
                    "max_hp": max_hp,
                    "dead_until": 0,
                    "hp_regen_at": now,
                    f"equipment_sets.{set_id}": set_state,
                    "equipped_set": set_id,
                    "updated_at": now,
                }
            },
        )

    await send_message(
        message,
        f"✅ Đã max cho <b>{escape(str(target_name))}</b>.\n\n"
        f"⭐ Cấp độ: <b>{MAX_PLAYER_LEVEL}/{MAX_PLAYER_LEVEL}</b>\n"
        f"✨ Tổng EXP: <b>{format_number(MAX_PLAYER_XP)}</b>\n"
        f"❤️ HP: <b>{format_number(max_hp)}/{format_number(max_hp)}</b>\n"
        f"🧰 Trang bị: <b>{escape(str(template['name']))}</b>\n"
        f"🧬 Hợp nhất: <b>+{MAX_EQUIPMENT_MERGE_LEVEL}/"
        f"{MAX_EQUIPMENT_MERGE_LEVEL}</b>\n"
        "♾ Giáp và vũ khí được tặng bởi /maxlevel không tiêu hao độ bền "
        "khi đánh boss.",
    )


@new_task
@entertainment_guard
async def toggle_auto_repair(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return
    if await ensure_message_user(collection, message) is None:
        return

    parts = (message.text or "").split()
    action = parts[1].strip().lower() if len(parts) > 1 else "status"
    aliases_on = {"on", "bat", "bật", "enable"}
    aliases_off = {"off", "tat", "tắt", "disable"}
    aliases_status = {"status", "trangthai", "trạngthái"}

    if len(parts) > 2 or action not in aliases_on | aliases_off | aliases_status:
        await send_message(
            message,
            "Cách dùng:\n"
            "<code>/autosua on</code> — tự sửa giáp và vũ khí sau mỗi lượt boss\n"
            "<code>/autosua off</code>\n"
            "<code>/autosua status</code>",
        )
        return

    user_id = int(message.from_user.id)
    if action in aliases_status:
        user_doc = await collection.find_one(
            {"_id": user_id},
            {"auto_repair_enabled": 1},
        )
        enabled = bool((user_doc or {}).get("auto_repair_enabled", False))
    else:
        enabled = action in aliases_on
        await collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "auto_repair_enabled": enabled,
                    "updated_at": time(),
                }
            },
            upsert=True,
        )

    await send_message(
        message,
        "🔧 Auto sửa trang bị hiện đang "
        f"<b>{'BẬT' if enabled else 'TẮT'}</b>.\n"
        "Khi bật, bot tự sửa cả giáp và vũ khí đang dùng sau mỗi lượt "
        "đánh boss, thu phí theo tier và áp dụng hao mòn giới hạn độ bền "
        "như lệnh /suachua. Nếu không đủ xu, auto sửa tự tắt.",
    )
