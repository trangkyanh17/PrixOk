from __future__ import annotations

from asyncio import Lock
from html import escape
from time import time
from typing import Any

from pymongo import ReturnDocument
from pyrogram.errors import RPCError

from ..core.telegram_manager import TgClient
from ..helper.ext_utils.db_handler import database
from ..helper.telegram_helper.message_utils import send_message


USER_LOCKS: dict[int, Lock] = {}
CHAT_LOCKS: dict[int, Lock] = {}
TRANSFER_LOCK = Lock()

LUCK_BUFF_PRICE = 20_000
LUCK_BUFF_SECONDS = 86_400

# Thứ tự tier được giữ đúng theo yêu cầu: nhôm → đồng → bạc → sắt → vàng
# → kim cương → graphine. Mỗi người chỉ có một set đang sử dụng.
EQUIPMENT_SETS: dict[str, dict[str, Any]] = {
    "nhom": {
        "name": "Set Nhôm",
        "tier": 1,
        "price": 8_000,
        "attack": 1.10,
        "crit": 0.03,
        "protection": 8,
        "durability": 120,
        "description": "Set nhập môn, nhẹ và dễ thay thế.",
    },
    "dong": {
        "name": "Set Đồng",
        "tier": 2,
        "price": 25_000,
        "attack": 1.20,
        "crit": 0.04,
        "protection": 14,
        "durability": 160,
        "description": "Bền hơn Nhôm và bảo vệ ổn định.",
    },
    "bac": {
        "name": "Set Bạc",
        "tier": 3,
        "price": 70_000,
        "attack": 1.35,
        "crit": 0.06,
        "protection": 22,
        "durability": 220,
        "description": "Tăng sát thương và khả năng chống phá giáp.",
    },
    "sat": {
        "name": "Set Sắt",
        "tier": 4,
        "price": 150_000,
        "attack": 1.55,
        "crit": 0.08,
        "protection": 32,
        "durability": 300,
        "description": "Set chiến đấu cân bằng cho săn boss.",
    },
    "vang": {
        "name": "Set Vàng",
        "tier": 5,
        "price": 350_000,
        "attack": 1.85,
        "crit": 0.10,
        "protection": 45,
        "durability": 420,
        "description": "Set cao cấp với sức bảo vệ lớn.",
    },
    "kim_cuong": {
        "name": "Set Kim Cương",
        "tier": 6,
        "price": 900_000,
        "attack": 2.30,
        "crit": 0.13,
        "protection": 62,
        "durability": 600,
        "description": "Rất bền, sát thương và chí mạng cao.",
    },
    "graphine": {
        "name": "Set Graphine",
        "tier": 7,
        "price": 2_000_000,
        "attack": 3.00,
        "crit": 0.18,
        "protection": 82,
        "durability": 850,
        "description": "Tier tối thượng, siêu nhẹ và siêu bền.",
    },
}


def format_number(value: int | float) -> str:
    return f"{int(round(value)):,}".replace(",", ".")


def display_name_from_user(user) -> str:
    if user is None:
        return "Không rõ"
    full_name = " ".join(
        part
        for part in (
            getattr(user, "first_name", None),
            getattr(user, "last_name", None),
        )
        if part
    ).strip()
    if full_name:
        return full_name
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    return str(getattr(user, "id", "Không rõ"))


def display_name(message) -> str:
    return display_name_from_user(message.from_user)


def game_collection():
    return database.db.game_users if database.db is not None else None


def code_collection():
    return database.db.game_codes if database.db is not None else None


def drop_collection():
    return database.db.game_drops if database.db is not None else None


def boss_collection():
    return database.db.game_bosses if database.db is not None else None


async def require_game_collection(message):
    collection = game_collection()
    if collection is None:
        await send_message(
            message,
            "❌ Hệ thống trò chơi cần MongoDB. Hãy kiểm tra DATABASE_URL.",
        )
    return collection


async def require_user(message):
    if message.from_user is None:
        await send_message(
            message,
            "❌ Lệnh này chỉ dùng bằng tài khoản người dùng, không dùng qua kênh.",
        )
        return None
    return message.from_user


def new_set_state(set_id: str) -> dict[str, Any]:
    template = EQUIPMENT_SETS[set_id]
    base_durability = int(template["durability"])
    return {
        # Áo giáp và vũ khí dùng hai thanh độ bền riêng.
        "armor_durability": base_durability,
        "weapon_durability": base_durability,
        "armor_durability_bonus": 0,
        "weapon_durability_bonus": 0,
        # Mỗi lần sửa chữa làm giảm vĩnh viễn giới hạn độ bền.
        "armor_max_penalty": 0,
        "weapon_max_penalty": 0,
        # Sửa giáp giảm bảo vệ; sửa vũ khí giảm hệ số tấn công.
        "protection_bonus": 0,
        "protection_penalty": 0,
        "attack_penalty": 0.0,
        "armor_repairs": 0,
        "weapon_repairs": 0,
        "merge_level": 0,
        "successful_merges": 0,
        "failed_merges": 0,
        "acquired_at": time(),
    }


async def ensure_user(collection, user) -> dict[str, Any]:
    now = time()
    username = (getattr(user, "username", None) or "").strip()
    await collection.update_one(
        {"_id": int(user.id)},
        {
            "$setOnInsert": {
                "coins": 0,
                "xp": 0,
                "created_at": now,
                "stats": {
                    "fish_count": 0,
                    "mine_count": 0,
                    "fish_value": 0,
                    "mine_value": 0,
                    "games_played": 0,
                    "games_won": 0,
                    "games_lost": 0,
                    "bet_wagered": 0,
                    "bet_profit": 0,
                    "shipper_count": 0,
                    "rocket_count": 0,
                    "boss_damage": 0,
                    "boss_kills": 0,
                    "boss_rewards": 0,
                    "equipment_merge_success": 0,
                    "equipment_merge_failed": 0,
                    "equipment_armor_repairs": 0,
                    "equipment_weapon_repairs": 0,
                },
                "equipment_sets": {},
                "equipped_set": None,
                "luck_admin_percent": 0,
            },
            "$set": {
                "display_name": display_name_from_user(user),
                "username": username,
                "username_lower": username.lower(),
                "updated_at": now,
            },
        },
        upsert=True,
    )
    doc = await collection.find_one({"_id": int(user.id)}) or {}

    repairs: dict[str, Any] = {}
    sets = doc.get("equipment_sets")
    if not isinstance(sets, dict):
        repairs["equipment_sets"] = {}
        sets = {}

    # Tự động chuyển dữ liệu V2 (một thanh độ bền) sang V3
    # (độ bền áo giáp và vũ khí riêng) mà không làm mất set đã mua.
    for set_id, state in sets.items():
        template = EQUIPMENT_SETS.get(set_id)
        if template is None or not isinstance(state, dict):
            continue
        base = int(template["durability"])
        legacy_bonus = max(0, int(state.get("durability_bonus", 0) or 0))
        legacy_max = base + legacy_bonus
        legacy_current = max(
            0,
            min(legacy_max, int(state.get("durability", legacy_max) or 0)),
        )
        prefix = f"equipment_sets.{set_id}"
        defaults = {
            "armor_durability": legacy_current,
            "weapon_durability": legacy_current,
            "armor_durability_bonus": legacy_bonus,
            "weapon_durability_bonus": legacy_bonus,
            "armor_max_penalty": 0,
            "weapon_max_penalty": 0,
            "protection_penalty": 0,
            "attack_penalty": 0.0,
            "armor_repairs": 0,
            "weapon_repairs": 0,
        }
        for key, value in defaults.items():
            if key not in state:
                repairs[f"{prefix}.{key}"] = value

    equipped = doc.get("equipped_set")
    if equipped is not None and (
        not isinstance(equipped, str) or equipped not in sets
    ):
        repairs["equipped_set"] = None

    if repairs:
        await collection.update_one({"_id": int(user.id)}, {"$set": repairs})
        doc = await collection.find_one({"_id": int(user.id)}) or doc
    return doc


async def ensure_message_user(collection, message) -> dict[str, Any] | None:
    user = await require_user(message)
    if user is None:
        return None
    return await ensure_user(collection, user)


def parse_positive_int(raw: str, *, name: str = "số xu") -> int:
    try:
        value = int(raw.replace(".", "").replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} phải là số nguyên dương.") from exc
    if value <= 0:
        raise ValueError(f"{name} phải lớn hơn 0.")
    return value


def parse_coin_amount(raw: str, balance: int) -> int:
    if raw.strip().lower() == "all":
        if balance <= 0:
            raise ValueError("Số dư hiện tại bằng 0.")
        return balance
    return parse_positive_int(raw)


async def reserve_coins(collection, user_id: int, amount: int):
    return await collection.find_one_and_update(
        {"_id": int(user_id), "coins": {"$gte": int(amount)}},
        {
            "$inc": {"coins": -int(amount)},
            "$set": {"updated_at": time()},
        },
        return_document=ReturnDocument.AFTER,
    )


async def add_coins(collection, user_id: int, amount: int) -> None:
    await collection.update_one(
        {"_id": int(user_id)},
        {
            "$inc": {"coins": int(amount)},
            "$set": {"updated_at": time()},
        },
        upsert=True,
    )


def user_lock(user_id: int) -> Lock:
    return USER_LOCKS.setdefault(int(user_id), Lock())


def chat_lock(chat_id: int) -> Lock:
    return CHAT_LOCKS.setdefault(int(chat_id), Lock())


def remaining_seconds(last_used: float | int | None, cooldown: int) -> int:
    if not last_used:
        return 0
    return max(0, cooldown - int(time() - float(last_used)))


def set_state(user_doc: dict[str, Any], set_id: str) -> dict[str, Any] | None:
    sets = user_doc.get("equipment_sets", {})
    state = sets.get(set_id) if isinstance(sets, dict) else None
    return state if isinstance(state, dict) else None


def effective_set_stats(user_doc: dict[str, Any], set_id: str) -> dict[str, Any] | None:
    template = EQUIPMENT_SETS.get(set_id)
    state = set_state(user_doc, set_id)
    if template is None or state is None:
        return None

    base_durability = int(template["durability"])
    legacy_bonus = max(0, int(state.get("durability_bonus", 0) or 0))
    legacy_current = int(
        state.get("durability", base_durability + legacy_bonus) or 0
    )

    armor_bonus = max(
        0,
        int(state.get("armor_durability_bonus", legacy_bonus) or 0),
    )
    weapon_bonus = max(
        0,
        int(state.get("weapon_durability_bonus", legacy_bonus) or 0),
    )
    armor_nominal_max = base_durability + armor_bonus
    weapon_nominal_max = base_durability + weapon_bonus

    # Sửa chữa chỉ có thể làm giảm tối đa 65% giới hạn độ bền danh nghĩa.
    armor_penalty_cap = int(armor_nominal_max * 0.65)
    weapon_penalty_cap = int(weapon_nominal_max * 0.65)
    armor_max_penalty = max(
        0,
        min(armor_penalty_cap, int(state.get("armor_max_penalty", 0) or 0)),
    )
    weapon_max_penalty = max(
        0,
        min(weapon_penalty_cap, int(state.get("weapon_max_penalty", 0) or 0)),
    )
    armor_max_durability = max(1, armor_nominal_max - armor_max_penalty)
    weapon_max_durability = max(1, weapon_nominal_max - weapon_max_penalty)

    armor_durability = max(
        0,
        min(
            armor_max_durability,
            int(state.get("armor_durability", legacy_current) or 0),
        ),
    )
    weapon_durability = max(
        0,
        min(
            weapon_max_durability,
            int(state.get("weapon_durability", legacy_current) or 0),
        ),
    )

    protection_bonus = max(0, int(state.get("protection_bonus", 0) or 0))
    protection_penalty = max(0, int(state.get("protection_penalty", 0) or 0))
    protection_before_break = max(
        1,
        min(
            95,
            int(template["protection"]) + protection_bonus - protection_penalty,
        ),
    )

    attack_penalty = max(0.0, float(state.get("attack_penalty", 0.0) or 0.0))
    attack_before_break = max(1.0, float(template["attack"]) - attack_penalty)
    armor_active = armor_durability > 0
    weapon_active = weapon_durability > 0

    return {
        "id": set_id,
        "name": template["name"],
        "tier": int(template["tier"]),
        "attack": attack_before_break if weapon_active else 1.0,
        "base_attack": float(template["attack"]),
        "attack_penalty": attack_penalty,
        "crit": float(template["crit"]) if weapon_active else 0.02,
        "protection": protection_before_break if armor_active else 0,
        "base_protection": int(template["protection"]),
        "protection_penalty": protection_penalty,
        "armor_durability": armor_durability,
        "armor_max_durability": armor_max_durability,
        "armor_nominal_max": armor_nominal_max,
        "armor_max_penalty": armor_max_penalty,
        "weapon_durability": weapon_durability,
        "weapon_max_durability": weapon_max_durability,
        "weapon_nominal_max": weapon_nominal_max,
        "weapon_max_penalty": weapon_max_penalty,
        "armor_repairs": int(state.get("armor_repairs", 0) or 0),
        "weapon_repairs": int(state.get("weapon_repairs", 0) or 0),
        "merge_level": int(state.get("merge_level", 0) or 0),
        "armor_active": armor_active,
        "weapon_active": weapon_active,
        "active": armor_active or weapon_active,
        # Hai khóa dưới giữ tương thích với code cũ ngoài module boss.
        "durability": min(armor_durability, weapon_durability),
        "max_durability": min(armor_max_durability, weapon_max_durability),
    }


def equipped_set_stats(user_doc: dict[str, Any]) -> dict[str, Any] | None:
    set_id = user_doc.get("equipped_set")
    if not isinstance(set_id, str):
        return None
    return effective_set_stats(user_doc, set_id)


def luck_multiplier(user_doc: dict[str, Any]) -> float:
    multiplier = 1.0
    if float(user_doc.get("luck_buff_until", 0) or 0) > time():
        multiplier *= 1.25
    admin_percent = max(
        0.0,
        min(100.0, float(user_doc.get("luck_admin_percent", 0) or 0)),
    )
    multiplier *= 1.0 + admin_percent / 100.0
    return min(multiplier, 3.0)


def luck_retry_chance(user_doc: dict[str, Any]) -> float:
    return min(0.50, max(0.0, luck_multiplier(user_doc) - 1.0) * 0.35)


def equipment_summary(user_doc: dict[str, Any]) -> str:
    sets = user_doc.get("equipment_sets", {})
    owned_count = len(sets) if isinstance(sets, dict) else 0
    stats = equipped_set_stats(user_doc)
    if stats is None:
        return (
            "🧰 Set đang dùng: <b>Chưa trang bị</b>\n"
            f"📦 Số set sở hữu: <b>{owned_count}</b>"
        )

    armor_status = "Hoạt động" if stats["armor_active"] else "Hỏng"
    weapon_status = "Hoạt động" if stats["weapon_active"] else "Hỏng"
    return (
        f"🧰 Set đang dùng: <b>{escape(stats['name'])}</b>\n"
        f"🛡 Áo giáp: <b>{format_number(stats['armor_durability'])}/"
        f"{format_number(stats['armor_max_durability'])}</b> — {armor_status}\n"
        f"🛡 Sức bảo vệ: <b>{stats['protection']}%</b> "
        f"(hao hụt sửa chữa: -{stats['protection_penalty']} điểm)\n"
        f"⚔️ Vũ khí: <b>{format_number(stats['weapon_durability'])}/"
        f"{format_number(stats['weapon_max_durability'])}</b> — {weapon_status}\n"
        f"⚔️ Hệ số sát thương: <b>x{stats['attack']:.2f}</b> "
        f"(hao hụt sửa chữa: -x{stats['attack_penalty']:.2f})\n"
        f"💥 Chí mạng: <b>{stats['crit'] * 100:.0f}%</b>\n"
        f"🧬 Cấp hợp nhất: <b>+{stats['merge_level']}</b>\n"
        f"🔩 Đã sửa: giáp <b>{stats['armor_repairs']}</b> · "
        f"vũ khí <b>{stats['weapon_repairs']}</b>\n"
        f"📦 Số set sở hữu: <b>{owned_count}</b>"
    )


async def resolve_target(
    collection,
    message,
    raw: str | None,
    *,
    allow_all: bool = False,
) -> tuple[int | str, str] | None:
    if raw and allow_all and raw.strip().lower() == "all":
        return "all", "tất cả người chơi"

    if raw:
        token = raw.strip()
        if token.lstrip("-").isdigit():
            user_id = int(token)
            row = await collection.find_one({"_id": user_id})
            name = row.get("display_name") if row else user_id
            return user_id, str(name)

        username = token.lstrip("@").lower()
        row = await collection.find_one({"username_lower": username})
        if row:
            return int(row["_id"]), str(row.get("display_name") or f"@{username}")

        try:
            tg_user = await TgClient.bot.get_users(token)
        except (RPCError, ValueError):
            tg_user = None
        if tg_user is not None:
            await ensure_user(collection, tg_user)
            return int(tg_user.id), display_name_from_user(tg_user)

    reply = message.reply_to_message
    if reply and reply.from_user:
        await ensure_user(collection, reply.from_user)
        return int(reply.from_user.id), display_name_from_user(reply.from_user)

    await send_message(
        message,
        "❌ Không tìm thấy người dùng. Hãy reply, dùng ID hoặc @username.",
    )
    return None
