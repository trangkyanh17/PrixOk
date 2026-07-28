from __future__ import annotations

from html import escape
from math import ceil
from secrets import SystemRandom
from time import time
from unicodedata import combining, normalize

from pymongo import ReturnDocument
from pyrogram.enums import ChatType

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    EQUIPMENT_PART_LABELS,
    EQUIPMENT_SETS,
    NORMAL_GAME_REWARD_XP_MULTIPLIER,
    DISCIPLE_RESPAWN_SECONDS,
    MAX_EQUIPMENT_CRIT,
    MAX_EQUIPMENT_MERGE_LEVEL,
    MAX_PLAYER_LEVEL,
    MAX_PLAYER_XP,
    PLAYER_RESPAWN_SECONDS,
    add_coins,
    boss_collection,
    capped_xp_gain,
    dummy_coin_reward,
    chat_lock,
    disciple_armor_penetration_bonus,
    disciple_attack_value,
    disciple_defense,
    disciple_dodge,
    disciple_equipment_stats,
    disciple_fusion_active,
    disciple_hp_state,
    disciple_is_alive,
    disciple_state,
    FEMALE_CHARM_CHANCE,
    FEMALE_CHARM_SECONDS,
    effective_set_stats,
    ensure_message_user,
    entertainment_guard,
    equipped_set_stats,
    format_number,
    new_set_state,
    player_attack,
    player_attack_for_level,
    player_defense,
    player_defense_for_level,
    player_dodge,
    player_dodge_for_level,
    player_hp_regen_for_level,
    player_hp_state,
    player_level,
    player_level_from_xp,
    player_max_hp_for_level,
    require_game_collection,
    require_user,
    reserve_coins,
    set_state,
    user_lock,
)


RNG = SystemRandom()
BOSS_RANDOM_SUMMON_COST = 20_000
BOSS_TARGETED_SUMMON_COST = 70_000
BOSS_LIFETIME = 3_600
BOSS_ATTACK_COOLDOWN = 0

BOSS_PART_TYPES = ("helmet", "armor", "weapon")
BOSS_COIN_DROP_BASE_CHANCE = 0.30
BOSS_COIN_DROP_TIER_CHANCE = 0.015
BOSS_PART_DROP_BASE_CHANCE = 0.010
BOSS_PART_DROP_TIER_CHANCE = 0.0018
BOSS_SET_DROP_BASE_CHANCE = 0.00005
BOSS_SET_DROP_TIER_CHANCE = 0.00005

SUPER_BOSS_RANDOM_CHANCE = 0.30
SUPER_BOSS_TARGETED_CHANCE = 0.30
SUPER_BOSS_STAT_MULTIPLIER = 200
NORMAL_BOSS_STAT_MULTIPLIER = 50
NORMAL_BOSS_COMBAT_MULTIPLIER = 5
BOSS_ARMOR_PENETRATION = 0.80
BOSS_XP_MULTIPLIER = 10
BOSS_WEAR_MIN = 15
BOSS_WEAR_MAX = 25
DUMMY_BASE_XP = 5
DUMMY_ACTIVITY_XP_MULTIPLIER = 2
DUMMY_ACTIVITY_COIN_MULTIPLIER = 1.5
DUMMY_COIN_MIN = 400
DUMMY_COIN_MAX = 1_800
BOSS_REWARD_RATE = 0.50
BOSS_ENRAGE_THRESHOLD = 0.50
BOSS_ENRAGE_MULTIPLIER = 3
BOSS_ENRAGE_ARMOR_REDUCTION = 0.50
SUPER_BOSS_EXECUTION_COST = 50_000_000
SUPER_BOSS_PAID_REWARD_RATE = 0.05
BOSS_DEFENSE_SCALE = 5_000

# Hợp nhất luôn tiêu hao set nguyên liệu. Tỉ lệ thành công phụ thuộc chênh lệch tier.
MERGE_BASE_CHANCE = 65
MERGE_MIN_CHANCE = 35
MERGE_MAX_CHANCE = 90

# Sửa chữa phục hồi độ bền hiện tại nhưng gây hao mòn vĩnh viễn.
# Mỗi lần sửa mất 4% giới hạn độ bền danh nghĩa; giới hạn không thấp hơn 35%.
REPAIR_MAX_DURABILITY_LOSS_RATE = 0.04
REPAIR_MAX_PENALTY_RATE = 0.65
REPAIR_COST_BY_TIER = {
    1: 1_000_000,
    2: 2_000_000,
    3: 3_000_000,
    4: 4_000_000,
    5: 6_000_000,
    6: 8_000_000,
    7: 10_000_000,
    8: 10_000_000,
}

BOSS_TEMPLATES = [
    {
        "id": "slime_king",
        "name": "Vua Slime",
        "emoji": "🟢",
        "hp": 5_000,
        "reward": 25_000,
        "weight": 260,
        "wear_min": 8,
        "wear_max": 14,
    },
    {
        "id": "venom_spider_queen",
        "name": "Nhện Chúa Độc",
        "emoji": "🕷️",
        "hp": 10_000,
        "reward": 50_000,
        "weight": 180,
        "wear_min": 11,
        "wear_max": 19,
    },
    {
        "id": "stone_giant",
        "name": "Khổng Lồ Đá",
        "emoji": "🗿",
        "hp": 15_000,
        "reward": 80_000,
        "weight": 140,
        "wear_min": 15,
        "wear_max": 25,
    },
    {
        "id": "frost_fenrir",
        "name": "Fenrir Băng Giá",
        "emoji": "🐺",
        "hp": 24_000,
        "reward": 130_000,
        "weight": 100,
        "wear_min": 20,
        "wear_max": 32,
    },
    {
        "id": "lava_demon_king",
        "name": "Ma Vương Dung Nham",
        "emoji": "🌋",
        "hp": 32_000,
        "reward": 175_000,
        "weight": 80,
        "wear_min": 23,
        "wear_max": 36,
    },
    {
        "id": "sea_dragon",
        "name": "Hải Long",
        "emoji": "🐉",
        "hp": 40_000,
        "reward": 220_000,
        "weight": 70,
        "wear_min": 25,
        "wear_max": 40,
    },
    {
        "id": "death_knight",
        "name": "Kỵ Sĩ Tử Thần",
        "emoji": "💀",
        "hp": 65_000,
        "reward": 360_000,
        "weight": 50,
        "wear_min": 30,
        "wear_max": 48,
    },
    {
        "id": "immortal_phoenix",
        "name": "Phượng Hoàng Bất Tử",
        "emoji": "🔥",
        "hp": 80_000,
        "reward": 480_000,
        "weight": 40,
        "wear_min": 34,
        "wear_max": 55,
    },
    {
        "id": "void_lord",
        "name": "Chúa Tể Hư Không",
        "emoji": "👁️",
        "hp": 100_000,
        "reward": 650_000,
        "weight": 30,
        "wear_min": 40,
        "wear_max": 65,
    },
    {
        "id": "abyss_kraken",
        "name": "Kraken Vực Sâu",
        "emoji": "🦑",
        "hp": 150_000,
        "reward": 1_000_000,
        "weight": 22,
        "wear_min": 45,
        "wear_max": 72,
    },
    {
        "id": "thunder_titan",
        "name": "Titan Sấm Sét",
        "emoji": "⚡",
        "hp": 250_000,
        "reward": 1_700_000,
        "weight": 15,
        "wear_min": 55,
        "wear_max": 85,
    },
    {
        "id": "golden_dragon_god",
        "name": "Long Thần Hoàng Kim",
        "emoji": "🐲",
        "hp": 400_000,
        "reward": 3_000_000,
        "weight": 10,
        "wear_min": 65,
        "wear_max": 100,
    },
    {
        "id": "world_eater",
        "name": "Kẻ Nuốt Thế Giới",
        "emoji": "🌍",
        "hp": 700_000,
        "reward": 5_500_000,
        "weight": 2,
        "wear_min": 80,
        "wear_max": 125,
    },
    {
        "id": "chaos_emperor",
        "name": "Hoàng Đế Hỗn Mang",
        "emoji": "🌀",
        "hp": 1_000_000,
        "reward": 8_500_000,
        "weight": 1,
        "wear_min": 95,
        "wear_max": 145,
    },
    {
        "id": "celestial_war_god",
        "name": "Chiến Thần Thiên Giới",
        "emoji": "⚔️",
        "hp": 1_500_000,
        "reward": 10_000_000,
        "weight": 1,
        "wear_min": 110,
        "wear_max": 165,
    },
    {
        "id": "primordial_behemoth",
        "name": "Cự Thú Thái Cổ",
        "emoji": "🦣",
        "hp": 2_000_000,
        "reward": 12_000_000,
        "weight": 1,
        "wear_min": 125,
        "wear_max": 185,
    },
    {
        "id": "blood_moon_empress",
        "name": "Nữ Đế Huyết Nguyệt",
        "emoji": "🌘",
        "hp": 2_800_000,
        "reward": 15_000_000,
        "weight": 1,
        "wear_min": 140,
        "wear_max": 205,
    },
    {
        "id": "time_devourer",
        "name": "Kẻ Nuốt Thời Gian",
        "emoji": "⌛",
        "hp": 4_000_000,
        "reward": 19_000_000,
        "weight": 1,
        "wear_min": 160,
        "wear_max": 230,
    },
    {
        "id": "star_forge_colossus",
        "name": "Cự Thần Lò Sao",
        "emoji": "🌟",
        "hp": 5_500_000,
        "reward": 24_000_000,
        "weight": 1,
        "wear_min": 180,
        "wear_max": 255,
    },
    {
        "id": "dimensional_tyrant",
        "name": "Bạo Chúa Không Gian",
        "emoji": "🪐",
        "hp": 7_500_000,
        "reward": 30_000_000,
        "weight": 1,
        "wear_min": 200,
        "wear_max": 280,
    },
    {
        "id": "cosmic_hydra",
        "name": "Hydra Vũ Trụ",
        "emoji": "🐍",
        "hp": 10_000_000,
        "reward": 38_000_000,
        "weight": 1,
        "wear_min": 225,
        "wear_max": 310,
    },
    {
        "id": "entropy_sovereign",
        "name": "Chúa Tể Entropy",
        "emoji": "☄️",
        "hp": 14_000_000,
        "reward": 48_000_000,
        "weight": 1,
        "wear_min": 250,
        "wear_max": 340,
    },
    {
        "id": "reality_breaker",
        "name": "Kẻ Phá Vỡ Thực Tại",
        "emoji": "💥",
        "hp": 20_000_000,
        "reward": 62_000_000,
        "weight": 1,
        "wear_min": 280,
        "wear_max": 380,
    },
    {
        "id": "omniverse_overlord",
        "name": "Bá Chủ Đa Vũ Trụ",
        "emoji": "🌌",
        "hp": 30_000_000,
        "reward": 80_000_000,
        "weight": 1,
        "wear_min": 320,
        "wear_max": 430,
    },
]


def _boss_tier(boss: dict) -> int:
    boss_id = str(boss.get("boss_id") or boss.get("id") or "")
    for index, template in enumerate(BOSS_TEMPLATES, start=1):
        if template["id"] == boss_id:
            return index
    stored = int(boss.get("tier", 1) or 1)
    return max(1, min(len(BOSS_TEMPLATES), stored))


def _equipment_set_for_boss(boss_tier: int) -> str:
    target_tier = max(1, min(8, ceil(int(boss_tier) / 2)))
    candidates = [
        set_id
        for set_id, template in EQUIPMENT_SETS.items()
        if int(template["tier"]) in {target_tier, max(1, target_tier - 1)}
    ]
    weights = [
        75 if int(EQUIPMENT_SETS[set_id]["tier"]) == target_tier else 25
        for set_id in candidates
    ]
    return RNG.choices(candidates, weights=weights, k=1)[0]


def _roll_attack_loot(user_doc: dict, boss: dict):
    boss_tier = _boss_tier(boss)
    is_super = bool(boss.get("is_super", False))
    loot_multiplier = 5 if is_super else 1

    old_xp = min(MAX_PLAYER_XP, int(user_doc.get("xp", 0) or 0))
    base_xp = (
        (2 + ceil(boss_tier / 2))
        * loot_multiplier
        * BOSS_XP_MULTIPLIER
    )
    xp_gain = capped_xp_gain(user_doc, base_xp)
    new_xp = min(MAX_PLAYER_XP, old_xp + xp_gain)
    old_level = player_level_from_xp(old_xp)
    new_level = player_level_from_xp(new_xp)

    inc: dict[str, int] = {
        "stats.boss_hits": 1,
    }
    if xp_gain:
        inc["xp"] = xp_gain
        inc["stats.boss_xp"] = xp_gain

    set_values: dict[str, object] = {}
    lines = (
        [f"⭐ Nhận <b>+{xp_gain} XP</b>."]
        if xp_gain
        else ["⭐ Đã đạt cấp tối đa, không nhận thêm XP."]
    )

    coin_chance = min(
        0.80 if is_super else 0.55,
        (
            BOSS_COIN_DROP_BASE_CHANCE
            + boss_tier * BOSS_COIN_DROP_TIER_CHANCE
        )
        * (2.0 if is_super else 1.0),
    )
    if RNG.random() < coin_chance:
        coin_drop = (
            RNG.randint(75 * boss_tier, 175 * boss_tier)
            * loot_multiplier
        )
        inc["coins"] = inc.get("coins", 0) + coin_drop
        inc["stats.boss_coin_drops"] = (
            inc.get("stats.boss_coin_drops", 0) + coin_drop
        )
        lines.append(f"💰 Boss rơi <b>{format_number(coin_drop)} xu</b>.")

    set_id = _equipment_set_for_boss(boss_tier)
    template = EQUIPMENT_SETS[set_id]
    owned_sets = user_doc.get("equipment_sets", {})
    if not isinstance(owned_sets, dict):
        owned_sets = {}

    full_set_chance = min(
        0.006 if is_super else 0.0012,
        (
            BOSS_SET_DROP_BASE_CHANCE
            + boss_tier * BOSS_SET_DROP_TIER_CHANCE
        )
        * (5.0 if is_super else 1.0),
    )
    part_chance = min(
        0.15 if is_super else 0.04,
        (
            BOSS_PART_DROP_BASE_CHANCE
            + boss_tier * BOSS_PART_DROP_TIER_CHANCE
        )
        * (4.0 if is_super else 1.0),
    )

    if RNG.random() < full_set_chance:
        if set_id not in owned_sets:
            set_values[f"equipment_sets.{set_id}"] = new_set_state(set_id)
            if not user_doc.get("equipped_set"):
                set_values["equipped_set"] = set_id
            inc["stats.boss_set_drops"] = (
                inc.get("stats.boss_set_drops", 0) + 1
            )
            lines.append(
                f"🎁 <b>SIÊU HIẾM:</b> Rơi nguyên "
                f"<b>{escape(template['name'])}</b>!"
            )
        else:
            salvage = max(5_000, int(template["price"]) // 3)
            inc["coins"] = inc.get("coins", 0) + salvage
            inc["stats.boss_coin_drops"] = (
                inc.get("stats.boss_coin_drops", 0) + salvage
            )
            lines.append(
                f"♻️ Trùng {escape(template['name'])}; quy đổi "
                f"<b>{format_number(salvage)} xu</b>."
            )
    elif RNG.random() < part_chance:
        part = RNG.choice(BOSS_PART_TYPES)
        if set_id in owned_sets:
            salvage = max(1_000, int(template["price"]) // 40)
            inc["coins"] = inc.get("coins", 0) + salvage
            inc["stats.boss_coin_drops"] = (
                inc.get("stats.boss_coin_drops", 0) + salvage
            )
            lines.append(
                f"♻️ Rơi {EQUIPMENT_PART_LABELS[part]} "
                f"{escape(template['name'])} nhưng đã sở hữu set; "
                f"đổi <b>{format_number(salvage)} xu</b>."
            )
        else:
            current_parts = user_doc.get("equipment_parts", {})
            if not isinstance(current_parts, dict):
                current_parts = {}
            current_set_parts = current_parts.get(set_id, {})
            if not isinstance(current_set_parts, dict):
                current_set_parts = {}

            deltas = {name: 0 for name in BOSS_PART_TYPES}
            deltas[part] += 1
            inc["stats.boss_part_drops"] = (
                inc.get("stats.boss_part_drops", 0) + 1
            )
            lines.append(
                f"🧩 Rơi <b>{EQUIPMENT_PART_LABELS[part]} "
                f"{escape(template['name'])}</b>."
            )

            projected = {
                name: max(
                    0,
                    int(current_set_parts.get(name, 0) or 0)
                    + deltas[name],
                )
                for name in BOSS_PART_TYPES
            }
            if all(projected[name] >= 1 for name in BOSS_PART_TYPES):
                for name in BOSS_PART_TYPES:
                    deltas[name] -= 1
                set_values[f"equipment_sets.{set_id}"] = new_set_state(set_id)
                if not user_doc.get("equipped_set"):
                    set_values["equipped_set"] = set_id
                inc["stats.boss_set_drops"] = (
                    inc.get("stats.boss_set_drops", 0) + 1
                )
                lines.append(
                    f"🛠 Đủ Mũ + Giáp + Vũ khí: tự động ghép thành "
                    f"<b>{escape(template['name'])}</b>."
                )

            for name, delta in deltas.items():
                if delta:
                    path = f"equipment_parts.{set_id}.{name}"
                    inc[path] = inc.get(path, 0) + delta

    if new_level > old_level:
        old_max_hp = player_max_hp_for_level(old_level)
        new_max_hp = player_max_hp_for_level(new_level)
        hp_gain = new_max_hp - old_max_hp
        current_hp, _, _ = player_hp_state(user_doc)

        set_values["max_hp"] = new_max_hp
        if current_hp > 0:
            set_values["hp"] = min(new_max_hp, current_hp + hp_gain)

        lines.append(
            f"🆙 Lên cấp <b>{new_level}/{MAX_PLAYER_LEVEL}</b>! "
            f"❤️ HP tối đa <b>{format_number(new_max_hp)}</b> · "
            f"⚔️ Tấn công <b>{format_number(player_attack_for_level(new_level))}</b> · "
            f"🛡 Phòng thủ <b>{format_number(player_defense_for_level(new_level))}</b> · "
            f"💨 Né <b>{player_dodge_for_level(new_level) * 100:.2f}%</b> · "
            f"💚 Hồi <b>{format_number(player_hp_regen_for_level(new_level))} HP/3s</b>."
        )

    inc = {key: value for key, value in inc.items() if value != 0}
    return inc, set_values, lines


def _select_boss():
    return RNG.choices(
        BOSS_TEMPLATES,
        weights=[boss["weight"] for boss in BOSS_TEMPLATES],
        k=1,
    )[0]


def _normalize_boss_key(value: str) -> str:
    normalized = normalize("NFKD", value.strip().lower())
    ascii_text = "".join(char for char in normalized if not combining(char))
    return "_".join(
        part
        for part in ascii_text.replace("-", " ").replace("_", " ").split()
        if part
    )


def _find_boss(raw: str):
    key = _normalize_boss_key(raw)
    for template in BOSS_TEMPLATES:
        if key in {
            _normalize_boss_key(template["id"]),
            _normalize_boss_key(template["name"]),
        }:
            return template
    return None



async def _active_bosses(bosses, chat_id: int) -> list[dict]:
    # Trả về toàn bộ boss còn hoạt động trong nhóm, đồng thời xử lý hết hạn.
    now = time()
    query = {
        "$or": [{"chat_id": int(chat_id)}, {"_id": int(chat_id)}],
        "status": "active",
    }
    rows: list[dict] = []
    cursor = bosses.find(query).sort("summoned_at", 1)
    async for boss in cursor:
        expires_at = boss.get("expires_at")
        if expires_at is None:
            expires_at = float(boss.get("summoned_at", now) or now) + BOSS_LIFETIME
            await bosses.update_one(
                {"_id": boss["_id"], "status": "active"},
                {"$set": {"expires_at": expires_at}},
            )
            boss["expires_at"] = expires_at
        if float(expires_at or 0) <= now:
            await bosses.update_one(
                {"_id": boss["_id"], "status": "active"},
                {"$set": {"status": "expired", "expired_at": now}},
            )
            continue
        rows.append(boss)
    return rows


async def _resolve_active_boss(
    bosses,
    chat_id: int,
    selector: str = "",
    *,
    super_only: bool = False,
):
    rows = await _active_bosses(bosses, chat_id)
    if super_only:
        rows = [row for row in rows if bool(row.get("is_super", False))]
    if not rows:
        return None
    raw = selector.strip()
    if not raw:
        return rows[0]
    key = _normalize_boss_key(raw)
    for boss in rows:
        candidates = {
            _normalize_boss_key(str(boss.get("instance_id", ""))),
            _normalize_boss_key(str(boss.get("boss_id", ""))),
            _normalize_boss_key(str(boss.get("name", ""))),
        }
        if key in candidates:
            return boss
    return None


async def _expire_boss_if_needed(bosses, chat_id: int):
    # Tương thích với auto-boss cũ: trả về boss hoạt động lâu nhất.
    return await _resolve_active_boss(bosses, chat_id)



def _boss_catalog_text() -> str:
    lines = [
        "👹 <b>Danh sách boss có thể chỉ định</b>",
        "",
        "Boss thường: HP x50; tấn công và phòng thủ x250.",
        "Boss siêu cấp: chỉ số x200 · tỉ lệ xuất hiện 30%.",
        "Kho thưởng mọi boss còn 50%; mọi boss xuyên 80% giáp.",
        "Có thể gọi nhiều boss; tất cả tồn tại tối đa 60 phút.",
        "Dưới 50% HP: cuồng nộ x3 công/phòng và giảm 50% giáp người đánh.",
        "",
        "Gọi ngẫu nhiên: <code>/goiboss</code> — "
        f"{format_number(BOSS_RANDOM_SUMMON_COST)} xu",
        "Gọi chỉ định: <code>/goiboss boss_id</code> — "
        f"{format_number(BOSS_TARGETED_SUMMON_COST)} xu",
        "",
    ]
    for index, template in enumerate(BOSS_TEMPLATES, start=1):
        lines.append(
            f"{index}. {template['emoji']} "
            f"<b>{escape(template['name'])}</b>\n"
            f"   Cấp: <b>{index}</b> · "
            f"ID: <code>{template['id']}</code> · "
            f"HP thường "
            f"{format_number(int(template['hp']) * NORMAL_BOSS_STAT_MULTIPLIER)} · "
            f"thưởng thường "
            f"{format_number(int(template['reward']) * NORMAL_BOSS_STAT_MULTIPLIER * BOSS_REWARD_RATE)} xu"
        )
    return "\n".join(lines)


def _group_required(message) -> bool:
    return message.chat.type != ChatType.PRIVATE


def _merge_chance(target_tier: int, donor_tier: int) -> int:
    chance = MERGE_BASE_CHANCE + (donor_tier - target_tier) * 7
    return max(MERGE_MIN_CHANCE, min(MERGE_MAX_CHANCE, chance))


def repair_cost_for_tier(tier: int) -> int:
    capped = max(1, min(8, int(tier)))
    return min(10_000_000, REPAIR_COST_BY_TIER[capped])


async def auto_repair_equipped_after_boss(collection, user_id: int) -> list[str]:
    user_doc = await collection.find_one({"_id": int(user_id)})
    if not user_doc or not bool(user_doc.get("auto_repair_enabled", False)):
        return []

    set_id = user_doc.get("equipped_set")
    if not isinstance(set_id, str):
        return []
    template = EQUIPMENT_SETS.get(set_id)
    stats = effective_set_stats(user_doc, set_id)
    if template is None or stats is None or bool(stats.get("indestructible", False)):
        return []

    total_cost = 0
    set_values: dict[str, object] = {"updated_at": time()}
    increments: dict[str, int] = {}
    repaired_lines: list[str] = []
    tier = int(template["tier"])
    repair_cost = repair_cost_for_tier(tier)

    for part, label in (("armor", "Giáp"), ("weapon", "Vũ khí")):
        current = int(stats[f"{part}_durability"])
        current_max = int(stats[f"{part}_max_durability"])
        owned = bool(stats[f"{part}_owned"])
        if owned and current >= current_max:
            continue

        nominal_max = int(stats[f"{part}_nominal_max"])
        old_max_penalty = int(stats[f"{part}_max_penalty"])
        max_penalty_cap = int(nominal_max * REPAIR_MAX_PENALTY_RATE)
        requested_loss = max(
            1,
            ceil(nominal_max * REPAIR_MAX_DURABILITY_LOSS_RATE),
        )
        durability_loss = min(
            requested_loss,
            max(0, max_penalty_cap - old_max_penalty),
        )
        new_max_penalty = old_max_penalty + durability_loss
        new_max = max(1, nominal_max - new_max_penalty)
        prefix = f"equipment_sets.{set_id}.{part}"
        set_values[f"{prefix}_owned"] = True
        set_values[f"{prefix}_durability"] = new_max
        set_values[f"{prefix}_max_penalty"] = new_max_penalty
        increments[f"{prefix}_repairs"] = 1
        increments[f"stats.equipment_{part}_repairs"] = 1
        total_cost += repair_cost
        repaired_lines.append(
            f"{label} {format_number(new_max)}/{format_number(new_max)}"
        )

    if not repaired_lines:
        return []

    if await reserve_coins(collection, user_id, total_cost) is None:
        await collection.update_one(
            {"_id": int(user_id)},
            {"$set": {"auto_repair_enabled": False, "updated_at": time()}},
        )
        return [
            f"⛔ Auto sửa đã tự tắt: cần {format_number(total_cost)} xu."
        ]

    try:
        await collection.update_one(
            {"_id": int(user_id)},
            {"$set": set_values, "$inc": increments},
        )
    except Exception:
        await add_coins(collection, user_id, total_cost)
        raise

    return [
        f"🔧 Auto sửa: {', '.join(repaired_lines)} · "
        f"phí {format_number(total_cost)} xu."
    ]


def _owned_sets_text(user_doc: dict) -> str:
    sets = user_doc.get("equipment_sets", {})
    equipped = user_doc.get("equipped_set")
    if not isinstance(sets, dict) or not sets:
        return (
            "Chưa sở hữu set nào.\n"
            "Tân thủ mặc áo phông, quần short và dùng tay không x1.00."
        )

    lines = []
    for set_id, template in EQUIPMENT_SETS.items():
        if set_id not in sets:
            continue
        stats = effective_set_stats(user_doc, set_id)
        marker = "✅" if set_id == equipped else "▫️"
        armor_text = (
            f"{format_number(stats['armor_durability'])}/"
            f"{format_number(stats['armor_max_durability'])}"
            if stats["armor_owned"]
            else "ĐÃ MẤT"
        )
        weapon_text = (
            f"{format_number(stats['weapon_durability'])}/"
            f"{format_number(stats['weapon_max_durability'])}"
            if stats["weapon_owned"]
            else "ĐÃ MẤT"
        )
        lines.append(
            f"{marker} <code>{set_id}</code> — "
            f"<b>{escape(template['name'])}</b>\n"
            f"   🛡 Giáp {armor_text} · "
            f"bảo vệ {stats['protection']}% · "
            f"sửa {stats['armor_repairs']} lần\n"
            f"   ⚔️ Vũ khí {weapon_text} · "
            f"tấn công x{stats['attack']:.2f} · "
            f"sửa {stats['weapon_repairs']} lần\n"
            f"   🧬 Hợp nhất +{stats['merge_level']}/{MAX_EQUIPMENT_MERGE_LEVEL}"
        )
    return "\n".join(lines)


@new_task
@entertainment_guard
async def equipment_shop(_, message):
    lines = [
        "🛒 <b>Cửa hàng set trang bị</b>",
        "Thứ tự: Nhôm → Đồng → Bạc → Sắt → Vàng → Kim Cương → Graphine → Graphine Tối Thượng.",
    ]
    for set_id, item in EQUIPMENT_SETS.items():
        lines.append(
            f"\n<code>{set_id}</code> — <b>{escape(item['name'])}</b>\n"
            f"Giá: {format_number(item['price'])} xu\n"
            f"Độ bền giáp/vũ khí: {format_number(item['durability'])} · "
            f"Bảo vệ: {item['protection']}%\n"
            f"Sát thương: x{item['attack']:.2f} · "
            f"Chí mạng: {item['crit'] * 100:.0f}%\n"
            f"{escape(item['description'])}"
        )
    lines.append(
        "\nMua: <code>/muatrangbi sat</code>\n"
        "Sử dụng một set: <code>/trangbi sat</code>\n"
        "Xem set đã có: <code>/trangbi</code>\n"
        "Hợp nhất set không dùng vào set đang dùng: <code>/hopnhat bac</code>\n"
        "Sửa set đang dùng: <code>/suachua giap</code> hoặc <code>/suachua vukhi</code>"
    )
    await send_message(message, "\n".join(lines))


@new_task
@entertainment_guard
async def buy_equipment(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(message, "Cách dùng: <code>/muatrangbi sat</code>")
        return

    set_id = parts[1].strip().lower()
    item = EQUIPMENT_SETS.get(set_id)
    if item is None:
        await send_message(message, "❌ Set trang bị không tồn tại.")
        return

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        replacing = set_id in user_doc.get("equipment_sets", {})
        if await reserve_coins(
            collection,
            message.from_user.id,
            int(item["price"]),
        ) is None:
            await send_message(
                message,
                f"❌ Cần <b>{format_number(item['price'])} xu</b> để mua.",
            )
            return

        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$set": {
                    f"equipment_sets.{set_id}": new_set_state(set_id),
                    "updated_at": time(),
                }
            },
        )

    action = "mua mới và thay thế" if replacing else "mua"
    await send_message(
        message,
        f"✅ Đã {action} <b>{escape(item['name'])}</b> với "
        f"<b>{format_number(item['price'])} xu</b>.\n"
        "Giáp và vũ khí được khôi phục về trạng thái mới.",
    )


@new_task
@entertainment_guard
async def equip_item(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return
    parts = (message.text or "").split()
    user_doc = await ensure_message_user(collection, message)
    if user_doc is None:
        return

    if len(parts) == 1:
        await send_message(
            message,
            "🧰 <b>Kho set trang bị</b>\n\n"
            + _owned_sets_text(user_doc)
            + "\n\n✅ Chỉ một set có thể được sử dụng cùng lúc.",
        )
        return
    if len(parts) != 2:
        await send_message(message, "Cách dùng: <code>/trangbi sat</code>")
        return

    set_id = parts[1].strip().lower()
    item = EQUIPMENT_SETS.get(set_id)
    if item is None:
        await send_message(message, "❌ Set trang bị không tồn tại.")
        return
    if set_state(user_doc, set_id) is None:
        await send_message(message, "❌ Mày chưa sở hữu set này.")
        return

    await collection.update_one(
        {"_id": message.from_user.id},
        {"$set": {"equipped_set": set_id, "updated_at": time()}},
    )
    stats = effective_set_stats(user_doc, set_id)
    broken_parts = []
    if not stats["armor_active"]:
        broken_parts.append("áo giáp hỏng")
    if not stats["weapon_active"]:
        broken_parts.append("vũ khí hỏng")
    broken = (
        " ⚠️ " + ", ".join(broken_parts) + "; mua mới hoặc hợp nhất để khôi phục."
        if broken_parts
        else ""
    )
    await send_message(
        message,
        f"✅ Đã sử dụng <b>{escape(item['name'])}</b>. Chỉ set này đang hoạt động.{broken}",
    )


@new_task
@entertainment_guard
async def merge_equipment(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(
            message,
            "Cách dùng: <code>/hopnhat bac</code>\n"
            "Set nguyên liệu bắt buộc thấp hơn set đang dùng đúng 1 bậc "
            "và bị tiêu hao dù thành công hay thất bại.",
        )
        return

    donor_id = parts[1].strip().lower()
    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        target_id = user_doc.get("equipped_set")
        if not isinstance(target_id, str):
            await send_message(
                message,
                "❌ Chưa có set đang sử dụng. Dùng /trangbi trước.",
            )
            return
        if donor_id == target_id:
            await send_message(
                message,
                "❌ Không thể dùng chính set đang sử dụng làm nguyên liệu.",
            )
            return

        target_template = EQUIPMENT_SETS.get(target_id)
        donor_template = EQUIPMENT_SETS.get(donor_id)
        target_state = set_state(user_doc, target_id)
        donor_state = set_state(user_doc, donor_id)
        if target_template is None or target_state is None:
            await send_message(message, "❌ Set đang sử dụng không hợp lệ.")
            return
        if donor_template is None or donor_state is None:
            await send_message(message, "❌ Không sở hữu set nguyên liệu này.")
            return

        target_tier = int(target_template["tier"])
        donor_tier = int(donor_template["tier"])
        if donor_tier != target_tier - 1:
            await send_message(
                message,
                "❌ Set nguyên liệu phải thấp hơn set chính đúng "
                "<b>1 bậc</b>.",
            )
            return

        merge_level = max(
            0,
            int(target_state.get("merge_level", 0) or 0),
        )
        if merge_level >= MAX_EQUIPMENT_MERGE_LEVEL:
            await send_message(
                message,
                f"❌ Set này đã đạt cấp hợp nhất tối đa <b>+{MAX_EQUIPMENT_MERGE_LEVEL}</b>.",
            )
            return

        chance = _merge_chance(target_tier, donor_tier)
        success = RNG.randint(1, 100) <= chance
        donor_stats = effective_set_stats(user_doc, donor_id)
        target_base_durability = int(target_template["durability"])
        target_base_protection = int(target_template["protection"])

        update: dict = {
            "$unset": {f"equipment_sets.{donor_id}": ""},
            "$set": {"updated_at": time()},
            "$inc": {
                (
                    f"equipment_sets.{target_id}.successful_merges"
                    if success
                    else f"equipment_sets.{target_id}.failed_merges"
                ): 1,
                (
                    "stats.equipment_merge_success"
                    if success
                    else "stats.equipment_merge_failed"
                ): 1,
            },
        }

        armor_gain = weapon_gain = protection_gain = 0
        if success:
            max_bonus_cap = target_base_durability * 2
            protection_bonus_cap = target_base_protection
            old_armor_bonus = max(
                0,
                int(target_state.get("armor_durability_bonus", 0) or 0),
            )
            old_weapon_bonus = max(
                0,
                int(target_state.get("weapon_durability_bonus", 0) or 0),
            )
            old_protection_bonus = max(
                0,
                int(target_state.get("protection_bonus", 0) or 0),
            )

            armor_gain = min(
                max(
                    1,
                    round(int(donor_stats["armor_nominal_max"]) * 0.25),
                ),
                max(0, max_bonus_cap - old_armor_bonus),
            )
            weapon_gain = min(
                max(
                    1,
                    round(int(donor_stats["weapon_nominal_max"]) * 0.25),
                ),
                max(0, max_bonus_cap - old_weapon_bonus),
            )
            protection_gain = min(
                max(
                    1,
                    round(int(donor_stats["protection"]) * 0.12),
                ),
                max(0, protection_bonus_cap - old_protection_bonus),
            )

            new_armor_bonus = old_armor_bonus + armor_gain
            new_weapon_bonus = old_weapon_bonus + weapon_gain
            new_protection_bonus = old_protection_bonus + protection_gain
            new_armor_max = target_base_durability + new_armor_bonus
            new_weapon_max = target_base_durability + new_weapon_bonus

            update["$set"].update(
                {
                    f"equipment_sets.{target_id}.armor_owned": True,
                    f"equipment_sets.{target_id}.weapon_owned": True,
                    f"equipment_sets.{target_id}.armor_durability_bonus": new_armor_bonus,
                    f"equipment_sets.{target_id}.weapon_durability_bonus": new_weapon_bonus,
                    f"equipment_sets.{target_id}.protection_bonus": new_protection_bonus,
                    f"equipment_sets.{target_id}.armor_max_penalty": 0,
                    f"equipment_sets.{target_id}.weapon_max_penalty": 0,
                    f"equipment_sets.{target_id}.protection_penalty": 0,
                    f"equipment_sets.{target_id}.attack_penalty": 0.0,
                    f"equipment_sets.{target_id}.armor_durability": new_armor_max,
                    f"equipment_sets.{target_id}.weapon_durability": new_weapon_max,
                }
            )
            update["$inc"][
                f"equipment_sets.{target_id}.merge_level"
            ] = 1

        await collection.update_one(
            {"_id": message.from_user.id},
            update,
        )

    if success:
        await send_message(
            message,
            f"🧬 <b>Hợp nhất thành công!</b> Tỉ lệ: <b>{chance}%</b>\n\n"
            f"Set chính: <b>{escape(target_template['name'])}</b>\n"
            f"Set nguyên liệu đã mất: "
            f"<b>{escape(donor_template['name'])}</b>\n"
            f"🛡 Tăng giới hạn bền giáp: "
            f"<b>+{format_number(armor_gain)}</b>\n"
            f"⚔️ Tăng giới hạn bền vũ khí: "
            f"<b>+{format_number(weapon_gain)}</b>\n"
            f"🛡 Tăng bảo vệ: <b>+{protection_gain} điểm</b>\n"
            f"🧬 Cấp hợp nhất: <b>+{merge_level + 1}/{MAX_EQUIPMENT_MERGE_LEVEL}</b>\n"
            "Giáp và vũ khí được khôi phục đầy đủ; hao mòn vĩnh viễn "
            "trước đó đã được xóa.",
        )
    else:
        await send_message(
            message,
            f"💥 <b>Hợp nhất thất bại!</b> Tỉ lệ: <b>{chance}%</b>\n\n"
            "Set chính không mất chỉ số, nhưng set nguyên liệu "
            f"<b>{escape(donor_template['name'])}</b> đã bị phá hủy.",
        )


@new_task
@entertainment_guard
async def repair_equipment(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(
            message,
            "Cách dùng:\n"
            "<code>/suachua giap</code> — sửa áo giáp đang dùng\n"
            "<code>/suachua vukhi</code> — sửa vũ khí đang dùng\n\n"
            "Phí sửa từ 1.000.000 đến 10.000.000 xu theo tier. "
            "Sửa không làm mất thuộc tính, nhưng giới hạn độ bền "
            "tiếp tục hao mòn vĩnh viễn.",
        )
        return

    raw_part = parts[1].strip().lower()
    armor_aliases = {
        "giap",
        "áo",
        "ao",
        "aogiap",
        "ao_giap",
        "armor",
    }
    weapon_aliases = {
        "vukhi",
        "vũkhí",
        "vu_khi",
        "vk",
        "weapon",
    }
    if raw_part in armor_aliases:
        part = "armor"
    elif raw_part in weapon_aliases:
        part = "weapon"
    else:
        await send_message(
            message,
            "❌ Chỉ chấp nhận <b>giap</b> hoặc <b>vukhi</b>.",
        )
        return

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        set_id = user_doc.get("equipped_set")
        if not isinstance(set_id, str):
            await send_message(message, "❌ Chưa có set đang sử dụng.")
            return
        template = EQUIPMENT_SETS.get(set_id)
        state = set_state(user_doc, set_id)
        stats = effective_set_stats(user_doc, set_id)
        if template is None or state is None or stats is None:
            await send_message(
                message,
                "❌ Set đang sử dụng không hợp lệ.",
            )
            return

        owned_key = f"{part}_owned"
        if not bool(stats[owned_key]):
            await send_message(
                message,
                "❌ Bộ phận này đã biến mất khi độ bền về 0. "
                "Chỉ có thể khôi phục bằng cách mua mới set hoặc "
                "hợp nhất thành công.",
            )
            return

        current = int(stats[f"{part}_durability"])
        current_max = int(stats[f"{part}_max_durability"])
        if current >= current_max:
            await send_message(
                message,
                "✅ Trang bị vẫn đầy độ bền, chưa cần sửa.",
            )
            return

        repair_cost = repair_cost_for_tier(int(template["tier"]))
        if await reserve_coins(
            collection,
            message.from_user.id,
            repair_cost,
        ) is None:
            await send_message(
                message,
                f"❌ Cần <b>{format_number(repair_cost)} xu</b> "
                "để sửa bộ phận này.",
            )
            return

        nominal_max = int(stats[f"{part}_nominal_max"])
        old_max_penalty = int(stats[f"{part}_max_penalty"])
        max_penalty_cap = int(
            nominal_max * REPAIR_MAX_PENALTY_RATE
        )
        requested_loss = max(
            1,
            ceil(
                nominal_max
                * REPAIR_MAX_DURABILITY_LOSS_RATE
            ),
        )
        durability_loss = min(
            requested_loss,
            max(0, max_penalty_cap - old_max_penalty),
        )
        new_max_penalty = old_max_penalty + durability_loss
        new_max = max(1, nominal_max - new_max_penalty)
        now = time()

        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$set": {
                    f"equipment_sets.{set_id}.{part}_durability": new_max,
                    f"equipment_sets.{set_id}.{part}_max_penalty": new_max_penalty,
                    "updated_at": now,
                },
                "$inc": {
                    f"equipment_sets.{set_id}.{part}_repairs": 1,
                    f"stats.equipment_{part}_repairs": 1,
                },
            },
        )

    part_name = "áo giáp" if part == "armor" else "vũ khí"
    await send_message(
        message,
        f"🔧 <b>Đã sửa {part_name} "
        f"{escape(template['name'])}</b>\n\n"
        f"💰 Chi phí: <b>{format_number(repair_cost)} xu</b>\n"
        f"🔩 Độ bền: <b>{format_number(new_max)}/"
        f"{format_number(new_max)}</b>\n"
        f"📉 Giới hạn độ bền mất vĩnh viễn: "
        f"<b>-{format_number(durability_loss)}</b>\n"
        "✅ Tấn công, chí mạng và bảo vệ không bị giảm.",
    )


@new_task
@entertainment_guard
async def summon_boss(_, message):
    collection = await require_game_collection(message)
    bosses = boss_collection()
    if collection is None or bosses is None or await require_user(message) is None:
        return

    parts = (message.text or "").split(maxsplit=1)
    raw_selector = parts[1].strip() if len(parts) > 1 else ""
    selector_key = _normalize_boss_key(raw_selector)

    if selector_key in {"list", "ds", "danh_sach", "danhsach"}:
        await send_message(message, _boss_catalog_text())
        return

    if selector_key in {"", "random", "rand", "ngau_nhien", "ngaunhien"}:
        template = _select_boss()
        summon_cost = BOSS_RANDOM_SUMMON_COST
        summon_mode = "Ngẫu nhiên"
    else:
        template = _find_boss(raw_selector)
        if template is None:
            await send_message(
                message,
                "❌ Không tìm thấy boss được chỉ định.\n\n"
                + _boss_catalog_text(),
            )
            return
        summon_cost = BOSS_TARGETED_SUMMON_COST
        summon_mode = "Chỉ định"

    super_chance = (
        SUPER_BOSS_RANDOM_CHANCE
        if summon_mode == "Ngẫu nhiên"
        else SUPER_BOSS_TARGETED_CHANCE
    )
    is_super = RNG.random() < super_chance
    boss_tier = _boss_tier(template)
    stat_multiplier = (
        SUPER_BOSS_STAT_MULTIPLIER
        if is_super
        else NORMAL_BOSS_STAT_MULTIPLIER
    )
    reward_multiplier = stat_multiplier
    combat_multiplier = (
        1 if is_super else NORMAL_BOSS_COMBAT_MULTIPLIER
    )

    base_attack_min = 80 + boss_tier * 35
    base_attack_max = 120 + boss_tier * 55
    base_defense = 40 + boss_tier * 20

    boss_hp = int(template["hp"]) * stat_multiplier
    boss_reward = max(
        1,
        int(round(int(template["reward"]) * reward_multiplier * BOSS_REWARD_RATE)),
    )
    boss_attack_min = base_attack_min * stat_multiplier * combat_multiplier
    boss_attack_max = base_attack_max * stat_multiplier * combat_multiplier
    boss_defense = base_defense * stat_multiplier * combat_multiplier

    if not _group_required(message):
        await send_message(message, "❌ Boss chỉ có thể được gọi trong nhóm.")
        return

    async with chat_lock(message.chat.id):

        await ensure_message_user(collection, message)
        if await reserve_coins(
            collection,
            message.from_user.id,
            summon_cost,
        ) is None:
            await send_message(
                message,
                f"❌ Cần <b>{format_number(summon_cost)} xu</b> "
                f"để gọi boss theo chế độ {summon_mode.lower()}.",
            )
            return

        now = time()
        instance_id = (
            f"{int(now * 1000):x}{RNG.randrange(16**5):05x}"[-12:]
        )
        document = {
            "_id": f"{message.chat.id}:{instance_id}",
            "chat_id": message.chat.id,
            "instance_id": instance_id,
            "boss_id": template["id"],
            "tier": boss_tier,
            "name": template["name"],
            "emoji": template["emoji"],
            "hp": boss_hp,
            "max_hp": boss_hp,
            "reward": boss_reward,
            "attack_min": boss_attack_min,
            "attack_max": boss_attack_max,
            "defense": boss_defense,
            "is_super": is_super,
            "wear_min": BOSS_WEAR_MIN,
            "wear_max": BOSS_WEAR_MAX,
            "armor_penetration": BOSS_ARMOR_PENETRATION,
            "status": "active",
            "summon_mode": summon_mode.lower(),
            "summon_cost": summon_cost,
            "summoner_id": message.from_user.id,
            "summoned_at": now,
            "expires_at": now + BOSS_LIFETIME,
            "enraged": False,
            "damage": {},
        }
        try:
            await bosses.insert_one(document)
        except Exception:
            await add_coins(collection, message.from_user.id, summon_cost)
            raise

    super_title = " 🌌 <b>SIÊU CẤP</b>" if is_super else ""
    lifetime_text = "60 phút"
    execute_text = (
        f"\n💳 Dùng <code>/ketlieuboss</code> để trả "
        f"<b>{format_number(SUPER_BOSS_EXECUTION_COST)} xu</b> kết liễu ngay; "
        f"chỉ nhận 5% phần thưởng."
        if is_super
        else ""
    )
    await send_message(
        message,
        f"{template['emoji']} <b>{escape(template['name'])} xuất hiện!</b>"
        f"{super_title}\n\n"
        f"🎯 Kiểu gọi: <b>{summon_mode}</b>\n"
        f"💸 Phí triệu hồi: <b>{format_number(summon_cost)} xu</b>\n"
        f"❤️ HP: <b>{format_number(boss_hp)}</b>\n"
        f"⚔️ Tấn công: <b>{format_number(boss_attack_min)}–"
        f"{format_number(boss_attack_max)}</b>\n"
        f"🛡 Phòng thủ: <b>{format_number(boss_defense)}</b>\n"
        f"💰 Kho thưởng: <b>{format_number(boss_reward)} xu</b>\n"
        f"⏳ Tồn tại: <b>{lifetime_text}</b>\n"
        f"🆔 Mã trận: <code>{instance_id}</code>\n"
        f"⚔️ Đánh: <code>/danhboss {instance_id}</code>."
        f"{execute_text}",
    )


@new_task
@entertainment_guard
async def boss_status(_, message):
    bosses = boss_collection()
    if bosses is None:
        await send_message(message, "❌ MongoDB chưa sẵn sàng.")
        return
    if not _group_required(message):
        await send_message(message, "❌ Lệnh này chỉ dùng trong nhóm.")
        return

    rows = await _active_bosses(bosses, message.chat.id)
    if not rows:
        await send_message(
            message,
            "Không có boss hoạt động. Dùng <code>/goiboss</code> để gọi ngẫu nhiên "
            "hoặc <code>/goiboss list</code> để xem danh sách.",
        )
        return

    lines = [
        f"👹 <b>Boss đang hoạt động: {len(rows)}</b>",
        "Dùng <code>/danhboss mã_trận</code> để chọn mục tiêu.",
    ]
    for index, boss in enumerate(rows[:10], start=1):
        hp = max(0, int(boss.get("hp", 0) or 0))
        max_hp = max(1, int(boss.get("max_hp", 1) or 1))
        enraged = bool(boss.get("enraged", False)) or (
            hp <= max_hp * BOSS_ENRAGE_THRESHOLD
        )
        combat_multiplier = BOSS_ENRAGE_MULTIPLIER if enraged else 1
        remain = max(0, int(float(boss.get("expires_at", time())) - time()))
        instance_id = str(boss.get("instance_id") or boss["_id"])
        super_text = " · 🌌 SIÊU CẤP" if bool(boss.get("is_super", False)) else ""
        rage_text = " · 🔥 CUỒNG NỘ" if enraged else ""
        damage = boss.get("damage", {})
        lines.append(
            f"\n{index}. {boss.get('emoji', '👹')} <b>{escape(str(boss.get('name', 'Boss')))}</b>"
            f"{super_text}{rage_text}\n"
            f"   Mã: <code>{escape(instance_id)}</code> · "
            f"HP {format_number(hp)}/{format_number(max_hp)}\n"
            f"   Công {format_number(int(boss.get('attack_min', 0)) * combat_multiplier)}–"
            f"{format_number(int(boss.get('attack_max', 0)) * combat_multiplier)} · "
            f"Thủ {format_number(int(boss.get('defense', 0)) * combat_multiplier)}\n"
            f"   Xuyên giáp 80% · Thưởng {format_number(int(boss.get('reward', 0)))} xu\n"
            f"   Còn {remain // 60}p {remain % 60}s · "
            f"{len(damage) if isinstance(damage, dict) else 0} người tham chiến"
        )
    if len(rows) > 10:
        lines.append(f"\n… và {len(rows) - 10} boss khác.")
    await send_message(message, "\n".join(lines))



async def _distribute_boss_rewards(collection, boss: dict, last_hitter: int):
    damage_map = {
        int(user_id): int(value)
        for user_id, value in boss.get("damage", {}).items()
        if int(value) > 0
    }
    if not damage_map:
        return []

    total_damage = sum(damage_map.values())
    pool = int(boss["reward"])
    distributed = []
    for user_id, damage in damage_map.items():
        base_share = int(round(pool * 0.90 * damage / total_damage))
        last_hit_bonus = int(round(pool * 0.10)) if user_id == last_hitter else 0
        reward = max(1, base_share + last_hit_bonus)
        inc = {"coins": reward, "stats.boss_rewards": reward}
        if user_id == last_hitter:
            inc["stats.boss_kills"] = 1
        await collection.update_one(
            {"_id": user_id},
            {"$inc": inc, "$set": {"updated_at": time()}},
            upsert=True,
        )
        distributed.append((user_id, reward, damage))
    return sorted(distributed, key=lambda row: row[2], reverse=True)


@new_task
@entertainment_guard
async def attack_boss(_, message):
    collection = await require_game_collection(message)
    bosses = boss_collection()
    if collection is None or bosses is None or await require_user(message) is None:
        return
    if not _group_required(message):
        await send_message(message, "❌ Boss chỉ có trong nhóm.")
        return

    parts = (message.text or "").split(maxsplit=1)
    boss_selector = parts[1].strip() if len(parts) > 1 else ""

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return

        now = time()
        hp, max_hp, respawn_remaining = player_hp_state(user_doc)
        revive_line = ""
        if hp <= 0:
            if respawn_remaining > 0:
                await send_message(
                    message,
                    f"💀 Mày đã hết máu. Chờ <b>{respawn_remaining // 60} phút "
                    f"{respawn_remaining % 60} giây</b> để hồi sinh.",
                )
                return
            hp = max_hp
            revive_line = f"✨ Đã hồi sinh với <b>{format_number(max_hp)} HP</b>.\n"

        boss = await _resolve_active_boss(bosses, message.chat.id, boss_selector)
        if not boss:
            hint = " Dùng <code>/boss</code> để xem mã trận." if boss_selector else ""
            await send_message(message, "Không tìm thấy boss hoạt động." + hint)
            return

        fused = disciple_fusion_active(user_doc)
        disciple = disciple_state(user_doc)
        disciple_active = disciple is not None and disciple_is_alive(user_doc)
        gear = equipped_set_stats(user_doc)
        attack_multiplier = gear["attack"] if gear else 1.0
        crit_chance = gear["crit"] if gear else 0.0
        if fused:
            crit_chance = min(
                MAX_EQUIPMENT_CRIT,
                crit_chance + float(disciple_equipment_stats(user_doc)["crit"]),
            )
        raw_master_damage = int(
            round(player_attack(user_doc) * attack_multiplier * RNG.uniform(0.85, 1.15))
        )
        master_critical = RNG.random() < crit_chance
        if master_critical:
            raw_master_damage = int(round(raw_master_damage * 2.0))

        boss_hp_before = max(0, int(boss.get("hp", 0) or 0))
        boss_max_hp = max(1, int(boss.get("max_hp", 1) or 1))
        was_enraged = bool(boss.get("enraged", False)) or (
            boss_hp_before <= boss_max_hp * BOSS_ENRAGE_THRESHOLD
        )
        boss_defense = max(0, int(boss.get("defense", 0) or 0))
        if was_enraged:
            boss_defense *= BOSS_ENRAGE_MULTIPLIER

        player_penetration = max(
            0.0,
            min(1.0, disciple_armor_penetration_bonus(user_doc)),
        )
        effective_boss_defense = int(round(boss_defense * (1.0 - player_penetration)))

        def damage_after_defense(raw_value: int) -> int:
            return max(
                1,
                ceil(
                    raw_value
                    * BOSS_DEFENSE_SCALE
                    / (BOSS_DEFENSE_SCALE + effective_boss_defense)
                ),
            )

        master_damage = damage_after_defense(raw_master_damage)
        disciple_damage = 0
        raw_disciple_damage = 0
        disciple_critical = False
        if disciple_active and not fused:
            disciple_gear = disciple_equipment_stats(user_doc)
            raw_disciple_damage = int(
                round(disciple_attack_value(user_doc) * RNG.uniform(0.85, 1.15))
            )
            disciple_critical = RNG.random() < float(disciple_gear["crit"])
            if disciple_critical:
                raw_disciple_damage = int(round(raw_disciple_damage * 2.0))
            disciple_damage = damage_after_defense(raw_disciple_damage)

        total_damage = master_damage + disciple_damage
        charm_triggered = bool(
            disciple_active
            and str(disciple.get("gender")) == "female"
            and RNG.random() < FEMALE_CHARM_CHANCE
        )
        set_values = {
            "last_hitter": message.from_user.id,
            "last_attack_at": now,
        }
        if charm_triggered:
            set_values["charmed_until"] = now + FEMALE_CHARM_SECONDS
            set_values["charmed_by"] = message.from_user.id

        updated = await bosses.find_one_and_update(
            {"_id": boss["_id"], "status": "active", "hp": {"$gt": 0}},
            {
                "$inc": {
                    "hp": -total_damage,
                    f"damage.{message.from_user.id}": total_damage,
                },
                "$set": set_values,
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            await send_message(message, "Boss vừa bị hạ bởi người khác.")
            return

        enraged = int(updated["hp"]) > 0 and (
            int(updated["hp"]) <= int(updated["max_hp"]) * BOSS_ENRAGE_THRESHOLD
        )
        rage_just_started = enraged and not bool(updated.get("enraged", False))
        if rage_just_started:
            await bosses.update_one(
                {"_id": updated["_id"], "status": "active"},
                {"$set": {"enraged": True, "enraged_at": now}},
            )
            updated["enraged"] = True

        loot_inc, loot_set, loot_lines = _roll_attack_loot(user_doc, updated)
        if "max_hp" in loot_set:
            max_hp = int(loot_set["max_hp"])
        if "hp" in loot_set:
            hp = int(loot_set["hp"])

        user_update: dict = {
            "$set": {
                "updated_at": now,
                "hp": hp,
                "max_hp": max_hp,
                "hp_regen_at": now,
            },
            "$inc": {
                "stats.boss_damage": total_damage,
                "stats.disciple_boss_damage": disciple_damage,
                "stats.disciple_charms": 1 if charm_triggered else 0,
                **loot_inc,
            },
        }
        user_update["$set"].update(loot_set)

        retaliation_lines: list[str] = []
        wear_lines: list[str] = []
        rage_lines: list[str] = []
        disciple_lines: list[str] = []
        if rage_just_started:
            rage_lines.append(
                "🔥 <b>BOSS CUỒNG NỘ!</b> Công và phòng thủ x3; "
                "giáp của người tham chiến bị giảm 50% trong trận này."
            )
        if charm_triggered:
            disciple_lines.append(
                f"💘 Đệ tử Nữ đã mê hoặc boss trong <b>{FEMALE_CHARM_SECONDS} giây</b>."
            )
        if player_penetration > 0:
            disciple_lines.append(
                f"🗡 Đệ tử Nam tăng xuyên giáp của cả hai lên "
                f"<b>{player_penetration * 100:.0f}%</b>."
            )

        if int(updated["hp"]) > 0:
            charmed = float(updated.get("charmed_until", 0) or 0) > now
            if charmed:
                retaliation_lines.append("💘 Boss đang bị mê hoặc nên không thể phản đòn.")
            else:
                target_disciple = bool(disciple_active and not fused and RNG.random() < 0.50)
                attack_min = int(updated.get("attack_min", 80 + _boss_tier(updated) * 35))
                attack_max = int(updated.get("attack_max", 120 + _boss_tier(updated) * 55))
                if enraged:
                    attack_min *= BOSS_ENRAGE_MULTIPLIER
                    attack_max *= BOSS_ENRAGE_MULTIPLIER
                raw_target_damage = RNG.randint(attack_min, attack_max)
                rage_armor_factor = 1.0 - BOSS_ENRAGE_ARMOR_REDUCTION if enraged else 1.0
                penetration = float(updated.get("armor_penetration", BOSS_ARMOR_PENETRATION) or BOSS_ARMOR_PENETRATION)
                penetration = max(0.0, min(1.0, penetration))

                if target_disciple:
                    d_hp, d_max_hp, _ = disciple_hp_state(user_doc)
                    d_dodge = disciple_dodge(user_doc)
                    if RNG.random() < d_dodge:
                        retaliation_lines.append(
                            f"🧑‍🎓 Đệ tử né đòn thành công! Tỉ lệ né <b>{d_dodge * 100:.2f}%</b>."
                        )
                    else:
                        d_defense = disciple_defense(user_doc)
                        d_protection = int(disciple_equipment_stats(user_doc)["protection"])
                        effective_defense = d_defense * rage_armor_factor * (1.0 - penetration)
                        effective_protection = d_protection * rage_armor_factor * (1.0 - penetration)
                        after_defense = raw_target_damage * 1_000 / (1_000 + effective_defense)
                        hp_damage = max(1, ceil(after_defense * (1.0 - effective_protection / 100.0)))
                        new_d_hp = max(0, d_hp - hp_damage)
                        user_update["$set"]["disciple.hp"] = new_d_hp
                        user_update["$set"]["disciple.max_hp"] = d_max_hp
                        retaliation_lines.append(
                            f"👹 Boss phản đòn đệ tử <b>{format_number(hp_damage)} sát thương</b>."
                        )
                        retaliation_lines.append(
                            f"🧑‍🎓 HP đệ tử còn <b>{format_number(new_d_hp)}/{format_number(d_max_hp)}</b>."
                        )
                        if new_d_hp <= 0:
                            user_update["$set"]["disciple.dead_until"] = now + DISCIPLE_RESPAWN_SECONDS
                            user_update["$inc"]["stats.disciple_deaths"] = 1
                            retaliation_lines.append(
                                "💀 Đệ tử đã gục ngã và sẽ hồi sinh sau <b>1 phút</b>."
                            )
                        else:
                            user_update["$set"]["disciple.dead_until"] = 0
                else:
                    dodge = player_dodge(user_doc)
                    if RNG.random() < dodge:
                        retaliation_lines.append(
                            f"💨 <b>NÉ ĐÒN THÀNH CÔNG!</b> Tỉ lệ né: <b>{dodge * 100:.2f}%</b>."
                        )
                        user_update["$set"]["dead_until"] = 0
                    else:
                        defense = player_defense(user_doc)
                        protection = int(gear["protection"]) if gear is not None and gear["armor_active"] else 0
                        if fused:
                            protection = min(
                                95,
                                protection + int(disciple_equipment_stats(user_doc)["protection"]),
                            )
                        effective_defense = defense * rage_armor_factor * (1.0 - penetration)
                        effective_protection = protection * rage_armor_factor * (1.0 - penetration)
                        after_defense = raw_target_damage * 1_000 / (1_000 + effective_defense)
                        hp_damage = max(1, ceil(after_defense * (1.0 - effective_protection / 100.0)))
                        new_hp = max(0, hp - hp_damage)
                        user_update["$set"]["hp"] = new_hp
                        retaliation_lines.append(
                            f"👹 Boss phản đòn <b>{format_number(hp_damage)} sát thương</b>."
                        )
                        if enraged:
                            retaliation_lines.append(
                                "🔥 Cuồng nộ: công boss x3 và giáp người chơi chỉ còn 50% hiệu lực."
                            )
                        retaliation_lines.append(
                            f"🛡 Phòng thủ {format_number(defense)} · bảo vệ trang bị {protection}% · "
                            f"boss xuyên giáp {penetration * 100:.0f}%."
                        )
                        retaliation_lines.append(
                            f"❤️ HP còn <b>{format_number(new_hp)}/{format_number(max_hp)}</b>."
                        )
                        if new_hp <= 0:
                            user_update["$set"]["dead_until"] = now + PLAYER_RESPAWN_SECONDS
                            user_update["$inc"]["stats.deaths"] = user_update["$inc"].get("stats.deaths", 0) + 1
                            retaliation_lines.append(
                                "💀 Mày đã gục ngã; cần chờ <b>1 phút</b> để đánh boss tiếp."
                            )
                        else:
                            user_update["$set"]["dead_until"] = 0

                        if gear is not None and not bool(gear.get("indestructible", False)):
                            if gear["armor_active"]:
                                armor_wear = RNG.randint(BOSS_WEAR_MIN, BOSS_WEAR_MAX)
                                new_armor_durability = max(0, int(gear["armor_durability"]) - armor_wear)
                                user_update["$set"][f"equipment_sets.{gear['id']}.armor_durability"] = new_armor_durability
                                wear_lines.append(
                                    f"🛡 Giáp mất <b>{armor_wear}</b> bền "
                                    f"({format_number(new_armor_durability)}/{format_number(gear['armor_max_durability'])})"
                                )
                                if new_armor_durability == 0:
                                    user_update["$set"][f"equipment_sets.{gear['id']}.armor_owned"] = False
                                    wear_lines.append("💥 Áo giáp đã biến mất.")
                            if gear["weapon_active"]:
                                weapon_wear = RNG.randint(BOSS_WEAR_MIN, BOSS_WEAR_MAX)
                                new_weapon_durability = max(0, int(gear["weapon_durability"]) - weapon_wear)
                                user_update["$set"][f"equipment_sets.{gear['id']}.weapon_durability"] = new_weapon_durability
                                wear_lines.append(
                                    f"⚔️ Vũ khí mất <b>{weapon_wear}</b> bền "
                                    f"({format_number(new_weapon_durability)}/{format_number(gear['weapon_max_durability'])})"
                                )
                                if new_weapon_durability == 0:
                                    user_update["$set"][f"equipment_sets.{gear['id']}.weapon_owned"] = False
                                    wear_lines.append("💥 Vũ khí đã biến mất.")
        else:
            user_update["$set"]["dead_until"] = 0

        await collection.update_one({"_id": message.from_user.id}, user_update)
        wear_lines.extend(
            await auto_repair_equipped_after_boss(collection, message.from_user.id)
        )

    master_crit_text = " 💥 <b>CHÍ MẠNG</b>" if master_critical else ""
    disciple_crit_text = " 💥 <b>CHÍ MẠNG</b>" if disciple_critical else ""
    raw_total = raw_master_damage + raw_disciple_damage
    defense_text = (
        f"🛡 Phòng thủ boss hấp thụ <b>{format_number(max(0, raw_total - total_damage))}</b> sát thương."
        if boss_defense
        else ""
    )
    combat_lines = [
        revive_line.rstrip(),
        f"⚔️ Sư phụ gây <b>{format_number(master_damage)}</b> sát thương.{master_crit_text}",
        (
            f"🧑‍🎓 Đệ tử gây <b>{format_number(disciple_damage)}</b> sát thương.{disciple_crit_text}"
            if disciple_damage
            else ""
        ),
        ("✨ Đang hợp thể: chỉ số đệ tử đã cộng trực tiếp vào sư phụ." if fused else ""),
        defense_text,
        *disciple_lines,
        *rage_lines,
        *loot_lines,
        *retaliation_lines,
        *wear_lines,
    ]
    combat_text = "\n".join(line for line in combat_lines if line)

    if int(updated["hp"]) > 0:
        rage_suffix = " · 🔥 CUỒNG NỘ" if enraged else ""
        await send_message(
            message,
            combat_text
            + "\n"
            + f"❤️ Boss còn <b>{format_number(updated['hp'])}/{format_number(updated['max_hp'])}</b> HP{rage_suffix}.\n"
            f"🆔 Mã trận: <code>{escape(str(updated.get('instance_id', updated['_id'])))}</code>",
        )
        return

    defeated = await bosses.find_one_and_update(
        {"_id": updated["_id"], "status": "active", "hp": {"$lte": 0}},
        {
            "$set": {
                "status": "defeated",
                "defeated_at": time(),
                "hp": 0,
                "enraged": False,
                "charmed_until": 0,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if defeated is None:
        return

    rewards = await _distribute_boss_rewards(collection, defeated, message.from_user.id)
    reward_lines = [
        f"{index}. <code>{user_id}</code> — {format_number(reward)} xu · "
        f"{format_number(total_user_damage)} sát thương"
        for index, (user_id, reward, total_user_damage) in enumerate(rewards[:10], start=1)
    ]
    super_text = (
        "\n🌌 Đây là boss siêu cấp; kho thưởng đã được giảm còn 50%."
        if bool(defeated.get("is_super", False))
        else ""
    )
    await send_message(
        message,
        combat_text
        + "\n\n"
        + f"🏆 <b>{escape(defeated['name'])} đã bị tiêu diệt!</b>{super_text}\n\n"
        f"Người kết liễu: <b>{escape(message.from_user.first_name or str(message.from_user.id))}</b>\n"
        f"Kho thưởng: <b>{format_number(defeated['reward'])} xu</b>\n"
        "🔥 Hiệu ứng cuồng nộ, giảm giáp và mê hoặc đã biến mất.\n\n"
        f"<b>Phân phối phần thưởng</b>\n" + "\n".join(reward_lines),
    )

@new_task
@entertainment_guard
async def training_dummy(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return

        gear = equipped_set_stats(user_doc)
        attack_multiplier = gear["attack"] if gear else 1.0
        crit_chance = gear["crit"] if gear else 0.0
        damage = max(
            1,
            int(
                round(
                    player_attack(user_doc)
                    * attack_multiplier
                    * RNG.uniform(0.90, 1.10)
                )
            ),
        )
        critical = RNG.random() < crit_chance
        if critical:
            damage *= 2

        base_xp = (
            DUMMY_BASE_XP
            * NORMAL_GAME_REWARD_XP_MULTIPLIER
            * DUMMY_ACTIVITY_XP_MULTIPLIER
        )
        xp_gain = capped_xp_gain(user_doc, base_xp)
        activity_base_coins = (
            RNG.randint(DUMMY_COIN_MIN, DUMMY_COIN_MAX)
            * NORMAL_GAME_REWARD_XP_MULTIPLIER
        )
        coin_gain = dummy_coin_reward(
            user_doc,
            activity_base_coins * DUMMY_ACTIVITY_COIN_MULTIPLIER,
        )
        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$inc": {
                    "coins": coin_gain,
                    "xp": xp_gain,
                    "stats.dummy_hits": 1,
                    "stats.dummy_damage": damage,
                    "stats.dummy_xp": xp_gain,
                    "stats.dummy_coins": coin_gain,
                },
                "$set": {"updated_at": time()},
            },
        )

    crit_text = " 💥 CHÍ MẠNG" if critical else ""
    buff_text = (
        " · bùa x2 tiền đã áp dụng"
        if coin_gain > int(round(activity_base_coins * DUMMY_ACTIVITY_COIN_MULTIPLIER))
        else ""
    )
    await send_message(
        message,
        "🎯 <b>Bù nhìn rơm bất tử</b>\n\n"
        f"👊 Gây <b>{format_number(damage)}</b> sát thương"
        f"{crit_text}.\n"
        f"⭐ Nhận <b>+{xp_gain} XP</b>.\n"
        f"💰 Nhận <b>{format_number(coin_gain)} xu</b>{buff_text}.\n"
        "Thưởng xu bằng x1,5 hoạt động giải trí cũ; "
        "bùa x2 tiền chỉ có hiệu lực tại bù nhìn.",
    )


@new_task
@entertainment_guard
async def execute_super_boss(_, message):
    collection = await require_game_collection(message)
    bosses = boss_collection()
    if collection is None or bosses is None or await require_user(message) is None:
        return
    if not _group_required(message):
        await send_message(message, "❌ Lệnh này chỉ dùng trong nhóm.")
        return

    parts = (message.text or "").split(maxsplit=1)
    selector = parts[1].strip() if len(parts) > 1 else ""

    async with chat_lock(message.chat.id):
        boss = await _resolve_active_boss(
            bosses,
            message.chat.id,
            selector,
            super_only=True,
        )
        if not boss:
            await send_message(
                message,
                "❌ Không tìm thấy boss siêu cấp hoạt động. "
                "Dùng <code>/boss</code> để xem mã trận.",
            )
            return

        await ensure_message_user(collection, message)
        reserved = await reserve_coins(
            collection,
            message.from_user.id,
            SUPER_BOSS_EXECUTION_COST,
        )
        if reserved is None:
            await send_message(
                message,
                f"❌ Cần <b>{format_number(SUPER_BOSS_EXECUTION_COST)} xu</b> "
                "để kết liễu boss siêu cấp.",
            )
            return

        defeated = await bosses.find_one_and_update(
            {
                "_id": boss["_id"],
                "status": "active",
                "is_super": True,
            },
            {
                "$set": {
                    "status": "paid_defeated",
                    "hp": 0,
                    "enraged": False,
                    "paid_defeated_at": time(),
                    "paid_defeated_by": message.from_user.id,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if defeated is None:
            await add_coins(
                collection,
                message.from_user.id,
                SUPER_BOSS_EXECUTION_COST,
            )
            await send_message(
                message,
                "❌ Boss đã bị xử lý trước đó; 50.000.000 xu đã được hoàn lại.",
            )
            return

        paid_reward = max(
            1,
            int(
                int(defeated["reward"])
                * SUPER_BOSS_PAID_REWARD_RATE
            ),
        )
        await add_coins(collection, message.from_user.id, paid_reward)

    await send_message(
        message,
        f"💳 <b>{escape(message.from_user.first_name or str(message.from_user.id))}</b> "
        f"đã trả <b>{format_number(SUPER_BOSS_EXECUTION_COST)} xu</b> "
        f"để kết liễu ngay {defeated['emoji']} "
        f"<b>{escape(defeated['name'])}</b>.\n\n"
        f"🎁 Phần thưởng trả phí: <b>{format_number(paid_reward)} xu</b> "
        f"(5% kho thưởng đã giảm 50%).\n"
        "🔥 Hiệu ứng cuồng nộ và giảm giáp đã biến mất.\n"
        "⚠️ Không phân phối kho thưởng theo sát thương và không rơi trang bị.",
    )
