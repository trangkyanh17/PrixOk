from __future__ import annotations

from functools import wraps

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

BUFF_SECONDS = 28_800

LUCK_BUFF_PRICE = 5_000_000
XP_BUFF_PRICE = 5_000_000
COIN_BUFF_PRICE = 5_000_000
ATTACK_BUFF_PRICE = 5_000_000
DEFENSE_BUFF_PRICE = 5_000_000
DODGE_BUFF_PRICE = 5_000_000
LUCK_BUFF_SECONDS = BUFF_SECONDS

STARTING_COINS = 2_000_000
NORMAL_GAME_REWARD_XP_MULTIPLIER = 5
GAME_LUCK_CHANCE_MULTIPLIER = 3.0

PLAYER_MAX_HP = 2_000
PLAYER_RESPAWN_SECONDS = 60
XP_PER_LEVEL = 100_000
HIGH_LEVEL_START = 1_000
HIGH_LEVEL_XP_GAIN_RATE = 0.20
HIGH_LEVEL_STAT_MULTIPLIER = 2
MAX_PLAYER_LEVEL = 2_000
HIGH_LEVEL_COUNT = MAX_PLAYER_LEVEL - HIGH_LEVEL_START
MAX_PLAYER_XP = (MAX_PLAYER_LEVEL - 1) * XP_PER_LEVEL

BASE_PLAYER_ATTACK = 100
ATTACK_PER_LEVEL = 40
BASE_PLAYER_DEFENSE = 200
DEFENSE_PER_LEVEL = 50
BASE_PLAYER_DODGE = 0.01
DODGE_PER_LEVEL = 0.0005
MAX_PLAYER_DODGE = 0.25
HP_PER_LEVEL = 500

HP_REGEN_TICK_SECONDS = 5
BASE_HP_REGEN_PER_TICK = 20
HP_REGEN_PER_LEVEL = 30

EQUIPMENT_PART_LABELS = {
    "helmet": "Mũ",
    "armor": "Giáp",
    "weapon": "Vũ khí",
}

# Thứ tự tier được giữ đúng theo yêu cầu: nhôm → đồng → bạc → sắt → vàng
# → kim cương → graphine. Mỗi người chỉ có một set đang sử dụng.
EQUIPMENT_SETS: dict[str, dict[str, Any]] = {
    "nhom": {
        "name": "Set Nhôm",
        "tier": 1,
        "price": 40_000,
        "attack": 1.10,
        "crit": 0.03,
        "protection": 8,
        "durability": 120,
        "description": "Set nhập môn, nhẹ và dễ thay thế.",
    },
    "dong": {
        "name": "Set Đồng",
        "tier": 2,
        "price": 125_000,
        "attack": 1.20,
        "crit": 0.04,
        "protection": 14,
        "durability": 160,
        "description": "Bền hơn Nhôm và bảo vệ ổn định.",
    },
    "bac": {
        "name": "Set Bạc",
        "tier": 3,
        "price": 350_000,
        "attack": 1.35,
        "crit": 0.06,
        "protection": 22,
        "durability": 220,
        "description": "Tăng sát thương và khả năng chống phá giáp.",
    },
    "sat": {
        "name": "Set Sắt",
        "tier": 4,
        "price": 750_000,
        "attack": 1.55,
        "crit": 0.08,
        "protection": 32,
        "durability": 300,
        "description": "Set chiến đấu cân bằng cho săn boss.",
    },
    "vang": {
        "name": "Set Vàng",
        "tier": 5,
        "price": 1_750_000,
        "attack": 1.85,
        "crit": 0.10,
        "protection": 45,
        "durability": 420,
        "description": "Set cao cấp với sức bảo vệ lớn.",
    },
    "kim_cuong": {
        "name": "Set Kim Cương",
        "tier": 6,
        "price": 4_500_000,
        "attack": 2.30,
        "crit": 0.13,
        "protection": 62,
        "durability": 600,
        "description": "Rất bền, sát thương và chí mạng cao.",
    },
    "graphine": {
        "name": "Set Graphine",
        "tier": 7,
        "price": 10_000_000,
        "attack": 3.00,
        "crit": 0.18,
        "protection": 82,
        "durability": 850,
        "description": "Tier tối thượng, siêu nhẹ và siêu bền.",
    },
}


def xp_required_for_level(level: int) -> int:
    capped = max(1, min(MAX_PLAYER_LEVEL, int(level)))
    return (capped - 1) * XP_PER_LEVEL


def player_level_from_xp(xp: int | float) -> int:
    value = max(0, min(MAX_PLAYER_XP, int(xp)))
    low, high = 1, MAX_PLAYER_LEVEL
    while low < high:
        middle = (low + high + 1) // 2
        if xp_required_for_level(middle) <= value:
            low = middle
        else:
            high = middle - 1
    return low


def player_level(user_doc: dict[str, Any]) -> int:
    return player_level_from_xp(int(user_doc.get("xp", 0) or 0))


def player_xp_progress(xp: int | float) -> tuple[int, int]:
    value = max(0, min(MAX_PLAYER_XP, int(xp)))
    level = player_level_from_xp(value)
    if level >= MAX_PLAYER_LEVEL:
        return 0, 0
    current = xp_required_for_level(level)
    following = xp_required_for_level(level + 1)
    return value - current, following - current


def _scaled_stat_steps(level: int) -> int:
    capped = max(1, min(MAX_PLAYER_LEVEL, int(level)))
    regular_steps = min(capped - 1, HIGH_LEVEL_START - 2)
    boosted_steps = max(0, capped - HIGH_LEVEL_START + 1)
    return regular_steps + boosted_steps * HIGH_LEVEL_STAT_MULTIPLIER


def player_max_hp_for_level(level: int) -> int:
    return PLAYER_MAX_HP + _scaled_stat_steps(level) * HP_PER_LEVEL


def player_hp_regen_for_level(level: int) -> int:
    return BASE_HP_REGEN_PER_TICK + _scaled_stat_steps(level) * HP_REGEN_PER_LEVEL


def buff_active(user_doc: dict[str, Any], field: str) -> bool:
    return float(user_doc.get(field, 0) or 0) > time()


def buff_remaining(user_doc: dict[str, Any], field: str) -> int:
    return max(0, int(float(user_doc.get(field, 0) or 0) - time()))


def xp_multiplier(user_doc: dict[str, Any]) -> float:
    return 2.0 if buff_active(user_doc, "xp_buff_until") else 1.0


def coin_multiplier(user_doc: dict[str, Any]) -> float:
    return 2.0 if buff_active(user_doc, "coin_buff_until") else 1.0


def normal_game_coin_reward(user_doc: dict[str, Any], base_coins: int | float) -> int:
    return max(0, int(round(float(base_coins) * coin_multiplier(user_doc))))


def capped_xp_gain(user_doc: dict[str, Any], base_xp: int | float) -> int:
    current_xp = min(MAX_PLAYER_XP, int(user_doc.get("xp", 0) or 0))
    requested = max(0.0, float(base_xp) * xp_multiplier(user_doc))
    threshold = xp_required_for_level(HIGH_LEVEL_START)
    if current_xp < threshold:
        normal_part = min(requested, float(threshold - current_xp))
        reduced_part = max(0.0, requested - normal_part)
        adjusted = normal_part + reduced_part * HIGH_LEVEL_XP_GAIN_RATE
    else:
        adjusted = requested * HIGH_LEVEL_XP_GAIN_RATE
    rounded = int(round(adjusted))
    if requested > 0 and rounded <= 0:
        rounded = 1
    return max(0, min(MAX_PLAYER_XP, current_xp + rounded) - current_xp)


def player_attack_for_level(level: int) -> int:
    return BASE_PLAYER_ATTACK + _scaled_stat_steps(level) * ATTACK_PER_LEVEL


def player_defense_for_level(level: int) -> int:
    return BASE_PLAYER_DEFENSE + _scaled_stat_steps(level) * DEFENSE_PER_LEVEL


def player_dodge_for_level(level: int) -> float:
    return min(
        MAX_PLAYER_DODGE,
        BASE_PLAYER_DODGE + _scaled_stat_steps(level) * DODGE_PER_LEVEL,
    )


def player_attack(user_doc: dict[str, Any]) -> int:
    value = player_attack_for_level(player_level(user_doc))
    return value * 2 if buff_active(user_doc, "attack_buff_until") else value


def player_defense(user_doc: dict[str, Any]) -> int:
    value = player_defense_for_level(player_level(user_doc))
    return value * 2 if buff_active(user_doc, "defense_buff_until") else value


def player_dodge(user_doc: dict[str, Any]) -> float:
    value = player_dodge_for_level(player_level(user_doc))
    if buff_active(user_doc, "dodge_buff_until"):
        value *= 1.5
    return min(MAX_PLAYER_DODGE, value)


def player_hp_regen(user_doc: dict[str, Any]) -> int:
    return player_hp_regen_for_level(player_level(user_doc))


def player_hp_state(user_doc: dict[str, Any]) -> tuple[int, int, int]:
    max_hp = player_max_hp_for_level(player_level(user_doc))
    hp = max(0, min(max_hp, int(user_doc.get("hp", max_hp) or 0)))
    dead_until = float(user_doc.get("dead_until", 0) or 0)
    respawn_remaining = max(0, int(dead_until - time())) if hp <= 0 else 0
    return hp, max_hp, respawn_remaining


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


def game_settings_collection():
    return database.db.game_settings if database.db is not None else None


async def entertainment_enabled() -> bool:
    settings = game_settings_collection()
    if settings is None:
        return True
    document = await settings.find_one(
        {"_id": "global"},
        {"entertainment_enabled": 1},
    )
    if document is None:
        return True
    return bool(document.get("entertainment_enabled", True))


async def set_entertainment_enabled(enabled: bool) -> None:
    settings = game_settings_collection()
    if settings is None:
        raise RuntimeError("MongoDB chưa sẵn sàng")
    await settings.update_one(
        {"_id": "global"},
        {
            "$set": {
                "entertainment_enabled": bool(enabled),
                "updated_at": time(),
            }
        },
        upsert=True,
    )


def entertainment_guard(function):
    @wraps(function)
    async def wrapper(client, message, *args, **kwargs):
        if not await entertainment_enabled():
            await send_message(
                message,
                "⛔ Khu vực giải trí đang tạm đóng bởi chủ bot.",
            )
            return None
        return await function(client, message, *args, **kwargs)

    return wrapper

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
        "armor_owned": True,
        "weapon_owned": True,
        "indestructible": False,
        "armor_durability": base_durability,
        "weapon_durability": base_durability,
        "armor_durability_bonus": 0,
        "weapon_durability_bonus": 0,
        "armor_max_penalty": 0,
        "weapon_max_penalty": 0,
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
                "coins": STARTING_COINS,
                "xp": 0,
                "hp": PLAYER_MAX_HP,
                "max_hp": PLAYER_MAX_HP,
                "hp_regen_at": now,
                "dead_until": 0,
                "luck_buff_until": 0,
                "xp_buff_until": 0,
                "coin_buff_until": 0,
                "attack_buff_until": 0,
                "defense_buff_until": 0,
                "dodge_buff_until": 0,
                "auto_repair_enabled": False,
                "equipment_parts": {},
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
                    "boss_hits": 0,
                    "boss_xp": 0,
                    "boss_coin_drops": 0,
                    "boss_part_drops": 0,
                    "boss_set_drops": 0,
                    "dummy_hits": 0,
                    "dummy_damage": 0,
                    "dummy_xp": 0,
                    "deaths": 0,
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

    max_hp = player_max_hp_for_level(player_level(doc))
    stored_max_hp = max(1, int(doc.get("max_hp", max_hp) or max_hp))
    if stored_max_hp != max_hp:
        repairs["max_hp"] = max_hp
    if "hp" not in doc:
        repairs["hp"] = max_hp
    elif int(doc.get("hp", 0) or 0) > max_hp:
        repairs["hp"] = max_hp
    if "dead_until" not in doc:
        repairs["dead_until"] = 0

    for buff_field in (
        "luck_buff_until",
        "xp_buff_until",
        "coin_buff_until",
        "attack_buff_until",
        "defense_buff_until",
        "dodge_buff_until",
    ):
        if buff_field not in doc:
            repairs[buff_field] = 0

    stored_hp = max(
        0,
        min(
            max_hp,
            int(repairs.get("hp", doc.get("hp", max_hp)) or 0),
        ),
    )
    dead_until = float(
        repairs.get("dead_until", doc.get("dead_until", 0)) or 0
    )
    regen_at_raw = doc.get("hp_regen_at")

    if regen_at_raw is None:
        repairs["hp_regen_at"] = now
    elif stored_hp <= 0:
        if dead_until and dead_until <= now:
            repairs["hp"] = max_hp
            repairs["dead_until"] = 0
            repairs["hp_regen_at"] = now
        elif float(regen_at_raw or now) < now:
            repairs["hp_regen_at"] = now
    elif stored_hp < max_hp:
        elapsed = max(0, int(now - float(regen_at_raw or now)))
        ticks = elapsed // HP_REGEN_TICK_SECONDS
        if ticks:
            healed = min(
                max_hp,
                stored_hp + ticks * player_hp_regen(doc),
            )
            repairs["hp"] = healed
            repairs["hp_regen_at"] = (
                float(regen_at_raw or now)
                + ticks * HP_REGEN_TICK_SECONDS
            )
    elif float(regen_at_raw or now) < now:
        repairs["hp_regen_at"] = now

    if "auto_repair_enabled" not in doc:
        repairs["auto_repair_enabled"] = False

    if not isinstance(doc.get("equipment_parts"), dict):
        repairs["equipment_parts"] = {}

    stat_defaults = {
        "boss_hits": 0,
        "boss_xp": 0,
        "boss_coin_drops": 0,
        "boss_part_drops": 0,
        "boss_set_drops": 0,
        "dummy_hits": 0,
        "dummy_damage": 0,
        "dummy_xp": 0,
        "deaths": 0,
    }
    stats_doc = doc.get("stats")
    if not isinstance(stats_doc, dict):
        stats_doc = {}
    for stat_key, default_value in stat_defaults.items():
        if stat_key not in stats_doc:
            repairs[f"stats.{stat_key}"] = default_value

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
            "armor_owned": legacy_current > 0,
            "weapon_owned": legacy_current > 0,
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
            "indestructible": False,
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

    armor_owned = bool(state.get("armor_owned", True))
    weapon_owned = bool(state.get("weapon_owned", True))
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

    armor_penalty_cap = int(armor_nominal_max * 0.65)
    weapon_penalty_cap = int(weapon_nominal_max * 0.65)
    armor_max_penalty = max(
        0,
        min(
            armor_penalty_cap,
            int(state.get("armor_max_penalty", 0) or 0),
        ),
    )
    weapon_max_penalty = max(
        0,
        min(
            weapon_penalty_cap,
            int(state.get("weapon_max_penalty", 0) or 0),
        ),
    )
    armor_max_durability = max(
        1,
        armor_nominal_max - armor_max_penalty,
    )
    weapon_max_durability = max(
        1,
        weapon_nominal_max - weapon_max_penalty,
    )

    armor_durability = (
        max(
            0,
            min(
                armor_max_durability,
                int(state.get("armor_durability", legacy_current) or 0),
            ),
        )
        if armor_owned
        else 0
    )
    weapon_durability = (
        max(
            0,
            min(
                weapon_max_durability,
                int(state.get("weapon_durability", legacy_current) or 0),
            ),
        )
        if weapon_owned
        else 0
    )

    protection_bonus = max(
        0,
        int(state.get("protection_bonus", 0) or 0),
    )
    protection_before_break = max(
        1,
        min(
            95,
            int(template["protection"]) + protection_bonus,
        ),
    )
    armor_active = armor_owned and armor_durability > 0
    weapon_active = weapon_owned and weapon_durability > 0

    return {
        "id": set_id,
        "name": template["name"],
        "tier": int(template["tier"]),
        "attack": float(template["attack"]) if weapon_active else 1.0,
        "base_attack": float(template["attack"]),
        "attack_penalty": 0.0,
        "crit": float(template["crit"]) if weapon_active else 0.0,
        "protection": protection_before_break if armor_active else 0,
        "base_protection": int(template["protection"]),
        "protection_penalty": 0,
        "indestructible": bool(state.get("indestructible", False)),
        "armor_owned": armor_owned,
        "weapon_owned": weapon_owned,
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
        "merge_level": min(
            10,
            max(0, int(state.get("merge_level", 0) or 0)),
        ),
        "armor_active": armor_active,
        "weapon_active": weapon_active,
        "active": armor_active or weapon_active,
        "durability": min(armor_durability, weapon_durability),
        "max_durability": min(
            armor_max_durability,
            weapon_max_durability,
        ),
    }


def equipped_set_stats(user_doc: dict[str, Any]) -> dict[str, Any] | None:
    set_id = user_doc.get("equipped_set")
    if not isinstance(set_id, str):
        return None
    return effective_set_stats(user_doc, set_id)


def luck_multiplier(user_doc: dict[str, Any]) -> float:
    multiplier = 1.0
    if buff_active(user_doc, "luck_buff_until"):
        multiplier *= 2.0
    admin_percent = max(
        0.0,
        min(100.0, float(user_doc.get("luck_admin_percent", 0) or 0)),
    )
    multiplier *= 1.0 + admin_percent / 100.0
    return min(multiplier, 3.0)


def game_luck_factor(user_doc: dict[str, Any]) -> float:
    return max(1.0, luck_multiplier(user_doc) * GAME_LUCK_CHANCE_MULTIPLIER)


def luck_retry_chance(user_doc: dict[str, Any]) -> float:
    return min(
        1.0,
        max(0.0, luck_multiplier(user_doc) - 1.0)
        * 0.35
        * GAME_LUCK_CHANCE_MULTIPLIER,
    )


def equipment_parts_summary(user_doc: dict[str, Any]) -> str:
    parts = user_doc.get("equipment_parts", {})
    if not isinstance(parts, dict):
        return "🧩 Mảnh boss: <b>Chưa có</b>"

    lines = []
    for set_id, template in EQUIPMENT_SETS.items():
        set_parts = parts.get(set_id, {})
        if not isinstance(set_parts, dict):
            continue
        counts = {
            part: max(0, int(set_parts.get(part, 0) or 0))
            for part in EQUIPMENT_PART_LABELS
        }
        if not any(counts.values()):
            continue
        detail = " · ".join(
            f"{EQUIPMENT_PART_LABELS[part]} {counts[part]}"
            for part in EQUIPMENT_PART_LABELS
        )
        lines.append(
            f"<code>{set_id}</code> — <b>{escape(template['name'])}</b>: {detail}"
        )

    if not lines:
        return "🧩 Mảnh boss: <b>Chưa có</b>"
    return "🧩 <b>Mảnh trang bị từ boss</b>\n" + "\n".join(lines)


def equipment_summary(user_doc: dict[str, Any]) -> str:
    sets = user_doc.get("equipment_sets", {})
    owned_count = len(sets) if isinstance(sets, dict) else 0
    stats = equipped_set_stats(user_doc)
    parts_text = equipment_parts_summary(user_doc)

    if stats is None:
        return (
            "👕 Trang phục tân thủ: <b>Áo phông + quần short</b> "
            "(không cộng chỉ số)\n"
            "👊 Vũ khí: <b>Tay không — sát thương x1.00</b>\n"
            f"📦 Số set sở hữu: <b>{owned_count}</b>\n"
            f"{parts_text}"
        )

    armor_status = (
        "Hoạt động"
        if stats["armor_active"]
        else "Đã mất — mua mới hoặc hợp nhất để khôi phục"
    )
    weapon_status = (
        "Hoạt động"
        if stats["weapon_active"]
        else "Đã mất — mua mới hoặc hợp nhất để khôi phục"
    )
    return (
        f"🧰 Set đang dùng: <b>{escape(stats['name'])}</b>\n"
        f"🛡 Áo giáp: <b>{format_number(stats['armor_durability'])}/"
        f"{format_number(stats['armor_max_durability'])}</b> — "
        f"{armor_status}\n"
        f"🛡 Sức bảo vệ: <b>{stats['protection']}%</b>\n"
        f"⚔️ Vũ khí: <b>{format_number(stats['weapon_durability'])}/"
        f"{format_number(stats['weapon_max_durability'])}</b> — "
        f"{weapon_status}\n"
        f"⚔️ Hệ số sát thương: <b>x{stats['attack']:.2f}</b>\n"
        f"💥 Chí mạng: <b>{stats['crit'] * 100:.0f}%</b>\n"
        f"🧬 Cấp hợp nhất: <b>+{stats['merge_level']}/10</b>\n"
        + ("♾ Độ bền boss: <b>Không tiêu hao</b>\n" if stats["indestructible"] else "")
        + f"🔩 Đã sửa: giáp <b>{stats['armor_repairs']}</b> · "
        f"vũ khí <b>{stats['weapon_repairs']}</b>\n"
        f"📦 Số set sở hữu: <b>{owned_count}</b>\n"
        f"{parts_text}"
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
