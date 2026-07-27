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
    MAX_PLAYER_LEVEL,
    MAX_PLAYER_XP,
    PLAYER_RESPAWN_SECONDS,
    add_coins,
    boss_collection,
    capped_xp_gain,
    chat_lock,
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
BOSS_LIFETIME = 1_800
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
BOSS_ARMOR_PENETRATION = 0.50
BOSS_XP_MULTIPLIER = 10
BOSS_WEAR_MIN = 15
BOSS_WEAR_MAX = 25
DUMMY_BASE_XP = 5
DUMMY_ACTIVITY_XP_MULTIPLIER = 2
MAX_EQUIPMENT_MERGE_LEVEL = 10
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
]


def _boss_tier(boss: dict) -> int:
    boss_id = str(boss.get("boss_id") or boss.get("id") or "")
    for index, template in enumerate(BOSS_TEMPLATES, start=1):
        if template["id"] == boss_id:
            return index
    stored = int(boss.get("tier", 1) or 1)
    return max(1, min(len(BOSS_TEMPLATES), stored))


def _equipment_set_for_boss(boss_tier: int) -> str:
    target_tier = max(1, min(7, ceil(int(boss_tier) / 2)))
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
            f"💚 Hồi <b>{format_number(player_hp_regen_for_level(new_level))} HP/5s</b>."
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



async def _expire_boss_if_needed(bosses, chat_id: int):
    """Đánh dấu boss hết hạn và trả về trạng thái mới nhất."""
    now = time()

    boss = await bosses.find_one({"_id": int(chat_id)})
    if boss is None:
        return None

    if (
        boss.get("status") == "active"
        and not bool(boss.get("is_super", False))
        and float(boss.get("expires_at", 0) or 0) <= now
    ):
        updated = await bosses.find_one_and_update(
            {
                "_id": int(chat_id),
                "status": "active",
                "expires_at": {"$lte": now},
            },
            {
                "$set": {
                    "status": "expired",
                    "expired_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

        if updated is not None:
            return updated

        return await bosses.find_one({"_id": int(chat_id)})

    return boss


def _boss_catalog_text() -> str:
    lines = [
        "👹 <b>Danh sách boss có thể chỉ định</b>",
        "",
        "Boss thường: toàn bộ HP, tấn công, phòng thủ và kho thưởng x50.",
        "Boss siêu cấp: toàn bộ chỉ số x200 · tỉ lệ xuất hiện 30%.",
        "Mọi boss xuyên 50% phòng thủ và bảo vệ trang bị.",
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
            f"{format_number(int(template['reward']) * NORMAL_BOSS_STAT_MULTIPLIER)} xu"
        )
    return "\n".join(lines)


def _group_required(message) -> bool:
    return message.chat.type != ChatType.PRIVATE


def _merge_chance(target_tier: int, donor_tier: int) -> int:
    chance = MERGE_BASE_CHANCE + (donor_tier - target_tier) * 7
    return max(MERGE_MIN_CHANCE, min(MERGE_MAX_CHANCE, chance))


def repair_cost_for_tier(tier: int) -> int:
    capped = max(1, min(7, int(tier)))
    return min(10_000_000, REPAIR_COST_BY_TIER[capped])

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
            f"   🧬 Hợp nhất +{stats['merge_level']}/10"
        )
    return "\n".join(lines)


@new_task
@entertainment_guard
async def equipment_shop(_, message):
    lines = [
        "🛒 <b>Cửa hàng set trang bị</b>",
        "Thứ tự: Nhôm → Đồng → Bạc → Sắt → Vàng → Kim Cương → Graphine.",
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
                "❌ Set này đã đạt cấp hợp nhất tối đa <b>+10</b>.",
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
            f"🧬 Cấp hợp nhất: <b>+{merge_level + 1}/10</b>\n"
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

    base_attack_min = 80 + boss_tier * 35
    base_attack_max = 120 + boss_tier * 55
    base_defense = 40 + boss_tier * 20

    boss_hp = int(template["hp"]) * stat_multiplier
    boss_reward = int(template["reward"]) * reward_multiplier
    boss_attack_min = base_attack_min * stat_multiplier
    boss_attack_max = base_attack_max * stat_multiplier
    boss_defense = base_defense * stat_multiplier

    if not _group_required(message):
        await send_message(message, "❌ Boss chỉ có thể được gọi trong nhóm.")
        return

    async with chat_lock(message.chat.id):
        current = await _expire_boss_if_needed(bosses, message.chat.id)
        if current and current.get("status") == "active":
            await send_message(
                message,
                "❌ Nhóm đang có boss hoạt động. Dùng /boss để xem.",
            )
            return

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
        document = {
            "_id": message.chat.id,
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
            "expires_at": None if is_super else now + BOSS_LIFETIME,
            "damage": {},
        }
        try:
            await bosses.replace_one(
                {"_id": message.chat.id},
                document,
                upsert=True,
            )
        except Exception:
            await add_coins(collection, message.from_user.id, summon_cost)
            raise

    super_title = " 🌌 <b>SIÊU CẤP</b>" if is_super else ""
    lifetime_text = "Vĩnh viễn đến khi bị hạ" if is_super else "30 phút"
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
        f"⚔️ Dùng <code>/danhboss</code> để tấn công."
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

    boss = await _expire_boss_if_needed(bosses, message.chat.id)
    if not boss or boss.get("status") != "active":
        await send_message(
            message,
            "Không có boss hoạt động. Dùng <code>/goiboss</code> để gọi ngẫu nhiên "
            "hoặc <code>/goiboss list</code> để xem danh sách.",
        )
        return

    damage = boss.get("damage", {})
    top = sorted(damage.items(), key=lambda row: row[1], reverse=True)[:3]
    top_lines = [
        f"{index}. <code>{user_id}</code>: {format_number(value)} sát thương"
        for index, (user_id, value) in enumerate(top, start=1)
    ]
    is_super = bool(boss.get("is_super", False))
    if is_super:
        lifetime_text = "Vĩnh viễn đến khi bị hạ"
    else:
        remain = max(0, int(float(boss["expires_at"]) - time()))
        lifetime_text = f"{remain // 60} phút {remain % 60} giây"

    super_line = (
    "🌌 <b>Boss siêu cấp x200</b>\n"
    if is_super
    else "👹 <b>Boss thường x50</b>\n"
)
    execute_line = (
        f"\n💳 Kết liễu trả phí: <code>/ketlieuboss</code> — "
        f"{format_number(SUPER_BOSS_EXECUTION_COST)} xu, nhận 5% thưởng."
        if is_super
        else ""
    )
    await send_message(
        message,
        f"{boss['emoji']} <b>{escape(boss['name'])}</b>\n\n"
        f"{super_line}"
        f"🏷 Cấp boss: <b>{_boss_tier(boss)}</b>\n"
        f"❤️ HP: <b>{format_number(max(0, boss['hp']))}/"
        f"{format_number(boss['max_hp'])}</b>\n"
        f"⚔️ Tấn công: <b>{format_number(boss.get('attack_min', 0))}–"
        f"{format_number(boss.get('attack_max', 0))}</b>\n"
        f"🛡 Phòng thủ: <b>{format_number(boss.get('defense', 0))}</b>\n"
        f"🗡 Xuyên giáp: <b>50%</b>\n"
        f"💰 Kho thưởng: <b>{format_number(boss['reward'])} xu</b>\n"
        f"⏳ Còn: <b>{lifetime_text}</b>\n"
        f"👥 Người tham chiến: <b>{len(damage)}</b>\n\n"
        + (
            "🏅 <b>Top sát thương</b>\n" + "\n".join(top_lines)
            if top_lines
            else "Chưa có ai tấn công."
        )
        + execute_line,
    )


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
            revive_line = (
                f"✨ Đã hồi sinh với <b>{format_number(max_hp)} HP</b>.\n"
            )

        boss = await _expire_boss_if_needed(bosses, message.chat.id)
        if not boss or boss.get("status") != "active":
            await send_message(message, "Không có boss hoạt động.")
            return

        gear = equipped_set_stats(user_doc)
        attack_multiplier = gear["attack"] if gear else 1.0
        crit_chance = gear["crit"] if gear else 0.0
        raw_damage = int(
            round(
                player_attack(user_doc)
                * attack_multiplier
                * RNG.uniform(0.85, 1.15)
            )
        )
        critical = RNG.random() < crit_chance
        if critical:
            raw_damage = int(round(raw_damage * 2.0))

        boss_defense = max(0, int(boss.get("defense", 0) or 0))
        damage = max(
            1,
            ceil(
                raw_damage
                * BOSS_DEFENSE_SCALE
                / (BOSS_DEFENSE_SCALE + boss_defense)
            ),
        )

        updated = await bosses.find_one_and_update(
            {"_id": message.chat.id, "status": "active", "hp": {"$gt": 0}},
            {
                "$inc": {
                    "hp": -damage,
                    f"damage.{message.from_user.id}": damage,
                },
                "$set": {
                    "last_hitter": message.from_user.id,
                    "last_attack_at": now,
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            await send_message(message, "Boss vừa bị hạ bởi người khác.")
            return

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
                "stats.boss_damage": damage,
                **loot_inc,
            },
        }
        user_update["$set"].update(loot_set)

        retaliation_lines = []
        wear_lines = []

        # Boss chỉ phản đòn khi còn sống sau cú đánh.
        if int(updated["hp"]) > 0:
            dodge = player_dodge(user_doc)
            if RNG.random() < dodge:
                retaliation_lines.append(
                    f"💨 <b>NÉ ĐÒN THÀNH CÔNG!</b> Tỉ lệ né: "
                    f"<b>{dodge * 100:.2f}%</b>."
                )
                user_update["$set"]["dead_until"] = 0
            else:
                attack_min = int(
                    updated.get(
                        "attack_min",
                        80 + _boss_tier(updated) * 35,
                    )
                )
                attack_max = int(
                    updated.get(
                        "attack_max",
                        120 + _boss_tier(updated) * 55,
                    )
                )
                raw_player_damage = RNG.randint(attack_min, attack_max)
                defense = player_defense(user_doc)
                protection = (
                    int(gear["protection"])
                    if gear is not None and gear["armor_active"]
                    else 0
                )

                penetration = float(
                    updated.get(
                        "armor_penetration",
                        BOSS_ARMOR_PENETRATION,
                    )
                    or BOSS_ARMOR_PENETRATION
                )
                penetration = max(0.0, min(1.0, penetration))
                effective_defense = max(
                    0.0,
                    defense * (1.0 - penetration),
                )
                effective_protection = max(
                    0.0,
                    protection * (1.0 - penetration),
                )
                after_defense = (
                    raw_player_damage
                    * 1_000
                    / (1_000 + effective_defense)
                )
                hp_damage = max(
                    1,
                    ceil(
                        after_defense
                        * (1.0 - effective_protection / 100.0)
                    ),
                )
                new_hp = max(0, hp - hp_damage)
                user_update["$set"]["hp"] = new_hp

                retaliation_lines.append(
                    f"👹 Boss phản đòn <b>{format_number(hp_damage)} sát thương</b>."
                )
                retaliation_lines.append(
                    f"🛡 Phòng thủ {format_number(defense)} · "
                    f"bảo vệ trang bị {protection}% · "
                    f"boss xuyên giáp {penetration * 100:.0f}%."
                )
                retaliation_lines.append(
                    f"❤️ HP còn <b>{format_number(new_hp)}/"
                    f"{format_number(max_hp)}</b>."
                )

                if new_hp <= 0:
                    user_update["$set"]["dead_until"] = (
                        now + PLAYER_RESPAWN_SECONDS
                    )
                    user_update["$inc"]["stats.deaths"] = (
                        user_update["$inc"].get("stats.deaths", 0) + 1
                    )
                    retaliation_lines.append(
                        "💀 Mày đã gục ngã; cần chờ <b>1 phút</b> "
                        "để đánh boss tiếp."
                    )
                else:
                    user_update["$set"]["dead_until"] = 0

                if gear is not None:
                    if gear["armor_active"]:
                        armor_wear = RNG.randint(
                            BOSS_WEAR_MIN,
                            BOSS_WEAR_MAX,
                        )
                        new_armor_durability = max(
                            0,
                            int(gear["armor_durability"]) - armor_wear,
                        )
                        user_update["$set"][
                            f"equipment_sets.{gear['id']}.armor_durability"
                        ] = new_armor_durability
                        wear_lines.append(
                            f"🛡 Giáp mất <b>{armor_wear}</b> bền "
                            f"({format_number(new_armor_durability)}/"
                            f"{format_number(gear['armor_max_durability'])})"
                        )
                        if new_armor_durability == 0:
                            user_update["$set"][
                                f"equipment_sets.{gear['id']}.armor_owned"
                            ] = False
                            wear_lines.append(
                                "💥 Áo giáp đã biến mất. "
                                "Phần vũ khí còn độ bền vẫn được giữ nguyên."
                            )

                    if gear["weapon_active"]:
                        weapon_wear = RNG.randint(
                            BOSS_WEAR_MIN,
                            BOSS_WEAR_MAX,
                        )
                        new_weapon_durability = max(
                            0,
                            int(gear["weapon_durability"]) - weapon_wear,
                        )
                        user_update["$set"][
                            f"equipment_sets.{gear['id']}.weapon_durability"
                        ] = new_weapon_durability
                        wear_lines.append(
                            f"⚔️ Vũ khí mất <b>{weapon_wear}</b> bền "
                            f"({format_number(new_weapon_durability)}/"
                            f"{format_number(gear['weapon_max_durability'])})"
                        )
                        if new_weapon_durability == 0:
                            user_update["$set"][
                                f"equipment_sets.{gear['id']}.weapon_owned"
                            ] = False
                            wear_lines.append(
                                "💥 Vũ khí đã biến mất. "
                                "Phần áo giáp còn độ bền vẫn được giữ nguyên."
                            )
        else:
            user_update["$set"]["dead_until"] = 0

        await collection.update_one(
            {"_id": message.from_user.id},
            user_update,
        )

    crit_text = " 💥 <b>CHÍ MẠNG</b>" if critical else ""
    defense_text = (
        f"🛡 Phòng thủ boss hấp thụ "
        f"<b>{format_number(max(0, raw_damage - damage))}</b> sát thương."
        if boss_defense
        else ""
    )
    combat_lines = [
        revive_line.rstrip(),
        f"⚔️ Gây <b>{format_number(damage)}</b> sát thương.{crit_text}",
        defense_text,
        *loot_lines,
        *retaliation_lines,
        *wear_lines,
    ]
    combat_text = "\n".join(line for line in combat_lines if line)

    if int(updated["hp"]) > 0:
        await send_message(
            message,
            combat_text
            + "\n"
            + f"❤️ Boss còn <b>{format_number(updated['hp'])}/"
            f"{format_number(updated['max_hp'])}</b> HP.",
        )
        return

    defeated = await bosses.find_one_and_update(
        {
            "_id": message.chat.id,
            "status": "active",
            "hp": {"$lte": 0},
        },
        {
            "$set": {
                "status": "defeated",
                "defeated_at": time(),
                "hp": 0,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if defeated is None:
        return

    rewards = await _distribute_boss_rewards(
        collection,
        defeated,
        message.from_user.id,
    )
    reward_lines = [
        f"{index}. <code>{user_id}</code> — {format_number(reward)} xu · "
        f"{format_number(total_damage)} sát thương"
        for index, (user_id, reward, total_damage) in enumerate(
            rewards[:10],
            start=1,
        )
    ]
    super_text = (
        "\n🌌 Đây là boss siêu cấp: toàn bộ chỉ số và kho thưởng đã được nhân 200."
        if bool(defeated.get("is_super", False))
        else ""
    )
    await send_message(
        message,
        combat_text
        + "\n\n"
        + f"🏆 <b>{escape(defeated['name'])} đã bị tiêu diệt!</b>"
        f"{super_text}\n\n"
        f"Người kết liễu: "
        f"<b>{escape(message.from_user.first_name or str(message.from_user.id))}</b>\n"
        f"Kho thưởng: <b>{format_number(defeated['reward'])} xu</b>\n\n"
        f"<b>Phân phối phần thưởng</b>\n"
        + "\n".join(reward_lines),
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
        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$inc": {
                    "xp": xp_gain,
                    "stats.dummy_hits": 1,
                    "stats.dummy_damage": damage,
                    "stats.dummy_xp": xp_gain,
                },
                "$set": {"updated_at": time()},
            },
        )

    crit_text = " 💥 CHÍ MẠNG" if critical else ""
    await send_message(
        message,
        "🎯 <b>Bù nhìn rơm bất tử</b>\n\n"
        f"👊 Gây <b>{format_number(damage)}</b> sát thương"
        f"{crit_text}.\n"
        f"⭐ Nhận <b>+{xp_gain} XP</b>.\n"
        "Bù nhìn tồn tại vĩnh viễn, không phản công, "
        "không có hồi chiêu và không rơi xu hoặc trang bị.",
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

    async with chat_lock(message.chat.id):
        boss = await bosses.find_one(
            {
                "_id": message.chat.id,
                "status": "active",
            }
        )
        if not boss:
            await send_message(message, "❌ Không có boss hoạt động.")
            return
        if not bool(boss.get("is_super", False)):
            await send_message(
                message,
                "❌ Chỉ có thể trả phí kết liễu boss siêu cấp.",
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
                "_id": message.chat.id,
                "status": "active",
                "is_super": True,
            },
            {
                "$set": {
                    "status": "paid_defeated",
                    "hp": 0,
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
        f"(5% kho thưởng siêu cấp).\n"
        "⚠️ Không phân phối kho thưởng theo sát thương và không rơi trang bị.",
    )
