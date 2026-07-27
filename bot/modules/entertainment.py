from __future__ import annotations

from asyncio import Lock
from html import escape
from json import load
from pathlib import Path
from secrets import SystemRandom
from time import time
from typing import Any

from pymongo import DESCENDING

from ..helper.ext_utils.bot_utils import new_task
from ..helper.ext_utils.db_handler import database
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    MAX_PLAYER_LEVEL,
    NORMAL_GAME_REWARD_XP_MULTIPLIER,
    capped_xp_gain,
    ensure_user,
    game_luck_factor,
    normal_game_coin_reward,
    player_level,
    player_level_from_xp,
    player_xp_progress,
)


RNG = SystemRandom()
DATA_DIR = Path(__file__).resolve().parent.parent / "game_data"

RARITY_WEIGHTS = {
    "common": 48.0,
    "uncommon": 27.0,
    "rare": 15.0,
    "epic": 7.0,
    "legendary": 2.5,
    "mythic": 0.5,
}

RARITY_LABELS = {
    "common": "Thường",
    "uncommon": "Không thường",
    "rare": "Hiếm",
    "epic": "Sử thi",
    "legendary": "Huyền thoại",
    "mythic": "Thần thoại",
}

RARITY_EMOJIS = {
    "common": "⚪",
    "uncommon": "🟢",
    "rare": "🔵",
    "epic": "🟣",
    "legendary": "🟠",
    "mythic": "🔴",
}

RARITY_XP = {
    "common": 5,
    "uncommon": 9,
    "rare": 16,
    "epic": 30,
    "legendary": 60,
    "mythic": 120,
}

QUALITY_TABLE = [
    ("Kém", 0.70, 15),
    ("Bình thường", 1.00, 55),
    ("Tốt", 1.25, 22),
    ("Hoàn hảo", 1.70, 8),
]

FISH_COOLDOWN = 60
MINE_COOLDOWN = 60

_user_locks: dict[int, Lock] = {}


def _load_json(filename: str) -> list[dict[str, Any]]:
    with (DATA_DIR / filename).open("r", encoding="utf-8") as file:
        data = load(file)
    if not isinstance(data, list):
        raise TypeError(f"{filename} must contain a JSON array")
    return data


FRESHWATER_FISH = _load_json("freshwater_fish.json")
SALTWATER_FISH = _load_json("saltwater_fish.json")
NONMETAL_MINERALS = _load_json("nonmetal_minerals.json")
METAL_MINERALS = _load_json("metal_minerals.json")


def _format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _format_decimal(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _display_name(message) -> str:
    user = message.from_user
    full_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    ).strip()
    if full_name:
        return full_name
    if user.username:
        return f"@{user.username}"
    return str(user.id)


def _pick_weighted(options: list[Any], weights: list[float]) -> Any:
    return RNG.choices(options, weights=weights, k=1)[0]


def _pick_loot(pool: list[dict[str, Any]], user_doc: dict[str, Any]) -> dict[str, Any]:
    available_rarities = [
        rarity
        for rarity in RARITY_WEIGHTS
        if any(item["rarity"] == rarity for item in pool)
    ]
    luck = game_luck_factor(user_doc)
    weights = []
    for rarity in available_rarities:
        weight = RARITY_WEIGHTS[rarity]
        if rarity in {"rare", "epic", "legendary", "mythic"}:
            weight *= luck
        elif rarity == "uncommon":
            weight *= 1.0 + (luck - 1.0) * 0.5
        weights.append(weight)
    rarity = _pick_weighted(available_rarities, weights)
    candidates = [item for item in pool if item["rarity"] == rarity]
    return RNG.choice(candidates)


def _skewed_value(minimum: float, maximum: float) -> tuple[float, float]:
    # Most catches are near the lower half, while exceptional sizes stay possible.
    ratio = RNG.random() ** 2.2
    value = minimum + (maximum - minimum) * ratio
    return round(value, 3), ratio


def _pick_quality(user_doc: dict[str, Any]) -> tuple[str, float]:
    luck = game_luck_factor(user_doc)
    weights = []
    for name, _, weight in QUALITY_TABLE:
        adjusted = float(weight)
        if name == "Hoàn hảo":
            adjusted *= luck
        elif name == "Tốt":
            adjusted *= 1.0 + (luck - 1.0) * 0.75
        weights.append(adjusted)
    name, multiplier, _ = _pick_weighted(QUALITY_TABLE, weights)
    return name, multiplier


def _purity_label(purity: int) -> str:
    if purity >= 97:
        return "Hoàn hảo"
    if purity >= 85:
        return "Cao cấp"
    if purity >= 60:
        return "Tinh khiết"
    if purity >= 40:
        return "Thô"
    return "Tạp chất cao"


def _remaining_seconds(last_used: float | int | None, cooldown: int) -> int:
    if not last_used:
        return 0
    remaining = cooldown - int(time() - float(last_used))
    return max(0, remaining)


def _game_collection():
    if database.db is None:
        return None
    return database.db.game_users


async def _require_collection(message):
    collection = _game_collection()
    if collection is None:
        await send_message(
            message,
            "❌ Mini-game cần MongoDB để lưu tiền, kho đồ và thời gian hồi.\n"
            "Hãy cấu hình <code>DATABASE_URL</code> rồi khởi động lại bot.",
        )
    return collection


async def _get_user(collection, message) -> dict[str, Any]:
    return await ensure_user(collection, message.from_user)


def _fish_location(raw: str | None) -> tuple[str, list[dict[str, Any]]]:
    value = (raw or "").strip().lower()
    if value in {"river", "freshwater", "song", "sông", "nuocngot", "nướcngọt"}:
        return "Nước ngọt", FRESHWATER_FISH
    if value in {"sea", "saltwater", "bien", "biển", "nuocman", "nướcmặn"}:
        return "Nước mặn", SALTWATER_FISH
    return RNG.choice(
        [
            ("Nước ngọt", FRESHWATER_FISH),
            ("Nước mặn", SALTWATER_FISH),
        ]
    )


def _mine_location(raw: str | None) -> tuple[str, list[dict[str, Any]]]:
    value = (raw or "").strip().lower()
    if value in {"nonmetal", "phi-kim", "phikim", "phi_kim"}:
        return "Mỏ phi kim", NONMETAL_MINERALS
    if value in {"metal", "kim-loai", "kimloai", "kim_loại", "kimloại"}:
        return "Mỏ kim loại", METAL_MINERALS
    return RNG.choice(
        [
            ("Mỏ phi kim", NONMETAL_MINERALS),
            ("Mỏ kim loại", METAL_MINERALS),
        ]
    )


@new_task
async def fish(_, message):
    collection = await _require_collection(message)
    if collection is None or message.from_user is None:
        return

    user_id = message.from_user.id
    lock = _user_locks.setdefault(user_id, Lock())

    async with lock:
        user = await _get_user(collection, message)
        remaining = _remaining_seconds(
            user.get("cooldowns", {}).get("fish"),
            FISH_COOLDOWN,
        )
        if remaining:
            await send_message(
                message,
                f"⏳ Cần chờ <b>{remaining} giây</b> mới được câu tiếp.",
            )
            return

        parts = (message.text or "").split(maxsplit=1)
        location, pool = _fish_location(parts[1] if len(parts) > 1 else None)
        item = _pick_loot(pool, user)
        weight, size_ratio = _skewed_value(
            float(item["min_weight"]),
            float(item["max_weight"]),
        )
        quality_name, quality_multiplier = _pick_quality(user)

        size_multiplier = 0.75 + size_ratio * 1.50
        value = max(
            1,
            round(
                float(item["base_value"])
                * size_multiplier
                * quality_multiplier
            ),
        )
        value *= NORMAL_GAME_REWARD_XP_MULTIPLIER
        value = normal_game_coin_reward(user, value)
        rarity = item["rarity"]
        xp = capped_xp_gain(
            user,
            RARITY_XP[rarity] * NORMAL_GAME_REWARD_XP_MULTIPLIER,
        )
        now = time()

        # Cá được quy đổi thành xu ngay lập tức và không lưu vào kho đồ.
        await collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "display_name": _display_name(message),
                    "username": message.from_user.username or "",
                    "updated_at": now,
                    "cooldowns.fish": now,
                    "last_fish.name": item["name"],
                    "last_fish.rarity": rarity,
                    "last_fish.weight": weight,
                    "last_fish.value": value,
                    "last_fish.caught_at": now,
                },
                "$inc": {
                    "coins": value,
                    "xp": xp,
                    "stats.fish_count": 1,
                    "stats.fish_value": value,
                },
                "$unset": {
                    "inventory.fish": "",
                },
            },
        )

        action = "Ghi nhận cá bảo tồn" if item["protected"] else "Câu được"
        money_label = "Thưởng bảo tồn" if item["protected"] else "Đã bán tự động"
        text = (
            f"🎣 <b>{location}</b>\n\n"
            f"{RARITY_EMOJIS[rarity]} {action}: <b>{escape(item['name'])}</b>\n"
            f"🔬 Tên khoa học: <i>{escape(item['scientific_name'])}</i>\n"
            f"✨ Độ hiếm: <b>{RARITY_LABELS[rarity]}</b>\n"
            f"⚖️ Trọng lượng: <b>{_format_decimal(weight)} kg</b>\n"
            f"🏅 Chất lượng: <b>{quality_name}</b>\n"
            f"💰 {money_label}: <b>{_format_number(value)} xu</b>\n"
            f"⭐ Kinh nghiệm: <b>+{xp} XP</b>"
        )
        await send_message(message, text)


@new_task
async def mine(_, message):
    collection = await _require_collection(message)
    if collection is None or message.from_user is None:
        return

    user_id = message.from_user.id
    lock = _user_locks.setdefault(user_id, Lock())

    async with lock:
        user = await _get_user(collection, message)
        remaining = _remaining_seconds(
            user.get("cooldowns", {}).get("mine"),
            MINE_COOLDOWN,
        )
        if remaining:
            await send_message(
                message,
                f"⏳ Cần chờ <b>{remaining} giây</b> mới được đào tiếp.",
            )
            return

        parts = (message.text or "").split(maxsplit=1)
        location, pool = _mine_location(parts[1] if len(parts) > 1 else None)
        item = _pick_loot(pool, user)
        mass, mass_ratio = _skewed_value(
            float(item["min_mass"]),
            float(item["max_mass"]),
        )
        purity = RNG.randint(20, 100)

        mass_multiplier = 0.75 + mass_ratio * 1.50
        purity_multiplier = 0.45 + purity / 100
        value = max(
            1,
            round(
                float(item["base_value"])
                * mass_multiplier
                * purity_multiplier
            ),
        )
        value *= NORMAL_GAME_REWARD_XP_MULTIPLIER
        value = normal_game_coin_reward(user, value)
        rarity = item["rarity"]
        xp = capped_xp_gain(
            user,
            RARITY_XP[rarity] * NORMAL_GAME_REWARD_XP_MULTIPLIER,
        )
        now = time()
        path = f"inventory.minerals.{item['id']}"

        await collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "display_name": _display_name(message),
                    "username": message.from_user.username or "",
                    "updated_at": now,
                    "cooldowns.mine": now,
                    f"{path}.name": item["name"],
                    f"{path}.formula": item["formula"],
                    f"{path}.category": item["category"],
                    f"{path}.rarity": rarity,
                    f"{path}.last_value": value,
                    f"{path}.last_mass": mass,
                    f"{path}.last_purity": purity,
                },
                "$inc": {
                    "coins": value,
                    "xp": xp,
                    "stats.mine_count": 1,
                    "stats.mine_value": value,
                    f"{path}.quantity": 1,
                    f"{path}.total_value": value,
                },
                "$max": {
                    f"{path}.best_value": value,
                    f"{path}.max_mass": mass,
                    f"{path}.max_purity": purity,
                },
            },
        )

        primary_metal = item.get("primary_metal")
        metal_line = (
            f"\n🔩 Kim loại chính: <b>{escape(primary_metal)}</b>"
            if primary_metal
            else ""
        )
        text = (
            f"⛏ <b>{location}</b>\n\n"
            f"{RARITY_EMOJIS[rarity]} Đào được: <b>{escape(item['name'])}</b>\n"
            f"🧪 Công thức: <code>{escape(item['formula'])}</code>"
            f"{metal_line}\n"
            f"✨ Độ hiếm: <b>{RARITY_LABELS[rarity]}</b>\n"
            f"⚖️ Khối lượng: <b>{_format_decimal(mass)} kg</b>\n"
            f"🔍 Độ tinh khiết: <b>{purity}% — {_purity_label(purity)}</b>\n"
            f"💰 Phần thưởng: <b>{_format_number(value)} xu</b>\n"
            f"⭐ Kinh nghiệm: <b>+{xp} XP</b>"
        )
        await send_message(message, text)


@new_task
async def game_profile(_, message):
    collection = await _require_collection(message)
    if collection is None or message.from_user is None:
        return

    user = await _get_user(collection, message)
    coins = int(user.get("coins", 0))
    xp = int(user.get("xp", 0))
    level = player_level(user)
    xp_progress, xp_required = player_xp_progress(xp)
    progress_text = (
        "TỐI ĐA"
        if level >= MAX_PLAYER_LEVEL
        else f"{xp_progress}/{xp_required} XP"
    )
    stats = user.get("stats", {})

    text = (
        f"👤 <b>Hồ sơ của {escape(_display_name(message))}</b>\n\n"
        f"💰 Số dư: <b>{_format_number(coins)} xu</b>\n"
        f"⭐ Cấp độ: <b>{level}/{MAX_PLAYER_LEVEL}</b>\n"
        f"📈 Kinh nghiệm: <b>{progress_text}</b>\n\n"
        f"🎣 Số lần câu: <b>{_format_number(int(stats.get('fish_count', 0)))}</b>\n"
        f"🐟 Tổng thưởng câu cá: "
        f"<b>{_format_number(int(stats.get('fish_value', 0)))} xu</b>\n"
        f"⛏ Số lần đào: <b>{_format_number(int(stats.get('mine_count', 0)))}</b>\n"
        f"💎 Tổng thưởng đào mỏ: "
        f"<b>{_format_number(int(stats.get('mine_value', 0)))} xu</b>"
    )
    await send_message(message, text)


def _inventory_lines(
    items: dict[str, dict[str, Any]],
    title: str,
) -> tuple[list[str], int]:
    rarity_order = {
        "mythic": 0,
        "legendary": 1,
        "epic": 2,
        "rare": 3,
        "uncommon": 4,
        "common": 5,
    }
    sorted_items = sorted(
        items.values(),
        key=lambda item: (
            rarity_order.get(item.get("rarity", "common"), 99),
            item.get("name", ""),
        ),
    )

    lines = [f"<b>{title}</b>"]
    total_value = 0
    for item in sorted_items:
        rarity = item.get("rarity", "common")
        quantity = int(item.get("quantity", 0))
        item_total = int(item.get("total_value", 0))
        total_value += item_total
        lines.append(
            f"{RARITY_EMOJIS.get(rarity, '⚪')} "
            f"<b>{escape(str(item.get('name', 'Không rõ')))}</b> "
            f"×{quantity} — tốt nhất {_format_number(int(item.get('best_value', 0)))} xu"
        )
    return lines, total_value


@new_task
async def game_inventory(_, message):
    collection = await _require_collection(message)
    if collection is None or message.from_user is None:
        return

    user = await _get_user(collection, message)
    inventory = user.get("inventory", {})
    parts = (message.text or "").split(maxsplit=1)
    requested = parts[1].strip().lower() if len(parts) > 1 else "all"

    show_fish = requested in {"all", "fish", "ca", "cá"}
    show_minerals = requested in {
        "all",
        "mine",
        "mineral",
        "minerals",
        "quang",
        "quặng",
    }

    if not show_fish and not show_minerals:
        await send_message(
            message,
            "Cách dùng:\n"
            "<code>/inventory</code>\n"
            "<code>/inventory fish</code>\n"
            "<code>/inventory minerals</code>",
        )
        return

    sections: list[str] = []
    grand_total = 0

    if show_fish:
        stats = user.get("stats", {})
        sections.append(
            "<b>🎣 Câu cá tự động bán</b>\n"
            "Cá câu được được đổi thành xu ngay lập tức, không lưu trong kho.\n"
            f"Số cá đã câu: <b>{_format_number(int(stats.get('fish_count', 0)))}</b>\n"
            f"Tổng xu đã nhận: <b>{_format_number(int(stats.get('fish_value', 0)))} xu</b>"
        )

    if show_minerals:
        mineral_items = inventory.get("minerals", {})
        if mineral_items:
            lines, total = _inventory_lines(
                mineral_items,
                "⛏ Bộ sưu tập khoáng sản",
            )
            sections.append("\n".join(lines))
            grand_total += total
        else:
            sections.append(
                "<b>⛏ Bộ sưu tập khoáng sản</b>\nChưa có chiến lợi phẩm."
            )

    text = (
        "\n\n".join(sections)
        + f"\n\n📦 Tổng giá trị khoáng sản đang lưu: <b>{_format_number(grand_total)} xu</b>"
    )
    await send_message(message, text)


@new_task
async def game_top(_, message):
    collection = await _require_collection(message)
    if collection is None:
        return

    rows = []
    cursor = (
        collection.find(
            {},
            {
                "display_name": 1,
                "coins": 1,
                "xp": 1,
            },
        )
        .sort("coins", DESCENDING)
        .limit(10)
    )
    async for row in cursor:
        rows.append(row)

    if not rows:
        await send_message(message, "Chưa có dữ liệu xếp hạng.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Bảng xếp hạng tài sản</b>"]
    for index, row in enumerate(rows, start=1):
        marker = medals[index - 1] if index <= 3 else f"{index}."
        name = escape(str(row.get("display_name") or row["_id"]))
        coins = _format_number(int(row.get("coins", 0)))
        level = player_level_from_xp(int(row.get("xp", 0)))
        lines.append(
            f"{marker} <b>{name}</b> — {coins} xu · cấp {level}"
        )

    await send_message(message, "\n".join(lines))
