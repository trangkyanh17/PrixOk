from __future__ import annotations

from html import escape
from time import time
from unicodedata import combining, normalize

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    BUFF_SECONDS,
    EQUIPMENT_SETS,
    MAX_EQUIPMENT_CRIT,
    buff_remaining,
    ensure_message_user,
    entertainment_guard,
    format_number,
    new_disciple_state,
    new_set_state,
    player_hp_state,
    player_max_hp,
    require_game_collection,
    require_user,
    reserve_coins,
    user_lock,
)
from .game_duck import DUCK_BOATS
from .game_disciple import DISCIPLE_SHOP_ITEMS, random_disciple_gender
from .game_economy import BUFF_SHOP


def _normalize_item(value: str) -> str:
    normalized = normalize("NFKD", value.strip().lower())
    ascii_text = "".join(char for char in normalized if not combining(char))
    return "_".join(
        part
        for part in ascii_text.replace("-", " ").replace("_", " ").split()
        if part
    )


def _find_item(raw: str):
    key = _normalize_item(raw)
    for item_id, item in EQUIPMENT_SETS.items():
        if key in {_normalize_item(item_id), _normalize_item(str(item["name"]))}:
            return "equipment", item_id, item

    for item_id, item in BUFF_SHOP.items():
        candidates = {
            _normalize_item(item_id),
            _normalize_item(str(item["name"])),
            *(_normalize_item(alias) for alias in item.get("aliases", set())),
        }
        if key in candidates:
            return "buff", item_id, item

    for item_id, item in DUCK_BOATS.items():
        if key in {_normalize_item(item_id), _normalize_item(str(item["name"]))}:
            return "duck_boat", item_id, item

    for item_id, item in DISCIPLE_SHOP_ITEMS.items():
        candidates = {
            _normalize_item(item_id),
            _normalize_item(str(item["name"])),
            *(_normalize_item(alias) for alias in item.get("aliases", set())),
        }
        if key in candidates:
            return "disciple_item", item_id, item
    return None


def _duration_text(seconds: int) -> str:
    if seconds <= 0:
        return "Không hoạt động"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}p"


@new_task
@entertainment_guard
async def game_shop(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return
    user_doc = await ensure_message_user(collection, message)
    if user_doc is None:
        return

    lines = [
        "🛒 <b>SHOP TỔNG HỢP</b>",
        "Mua bằng <code>/buy tên_vật_phẩm</code>.",
        "",
        "🧰 <b>SET TRANG BỊ</b>",
    ]
    for item_id, item in EQUIPMENT_SETS.items():
        crit = min(MAX_EQUIPMENT_CRIT, float(item["crit"])) * 100
        lines.append(
            f"\n<code>{item_id}</code> — <b>{escape(str(item['name']))}</b>\n"
            f"Giá {format_number(int(item['price']))} xu · "
            f"sát thương x{float(item['attack']):.2f} · "
            f"chí mạng {crit:.0f}% · bảo vệ {int(item['protection'])}% · "
            f"độ bền {format_number(int(item['durability']))}"
        )

    lines.extend(["", "🔮 <b>BÙA 8 GIỜ</b>"])
    for item_id, item in BUFF_SHOP.items():
        remaining = buff_remaining(user_doc, str(item["field"]))
        lines.append(
            f"\n<code>{item_id}</code> — {item['emoji']} "
            f"<b>{escape(str(item['name']))}</b>\n"
            f"Giá {format_number(int(item['price']))} xu · "
            f"{escape(str(item['effect']))}\n"
            f"Trạng thái: <b>{_duration_text(remaining)}</b>"
        )

    lines.extend(["", "🦆 <b>THUYỀN ĐUA VỊT</b>"])
    owned = user_doc.get("duck_boats", {})
    if not isinstance(owned, dict):
        owned = {}
    selected = str(user_doc.get("equipped_duck_boat") or "thuyen_vit_go")
    for item_id, item in DUCK_BOATS.items():
        price = int(item["price"])
        price_text = "Miễn phí" if price <= 0 else f"{format_number(price)} xu"
        owned_text = (
            " · <b>Đang dùng</b>"
            if selected == item_id
            else " · Đã sở hữu"
            if item_id == "thuyen_vit_go" or item_id in owned
            else ""
        )
        lines.append(
            f"\n<code>{item_id}</code> — <b>{escape(str(item['name']))}</b>{owned_text}\n"
            f"Giá {price_text} · quãng đường "
            f"{format_number(int(item['min_distance']))}–"
            f"{format_number(int(item['max_distance']))} m"
        )

    lines.extend(["", "🧑‍🎓 <b>ĐỆ TỬ VÀ HỢP THỂ</b>"])
    for item_id, item in DISCIPLE_SHOP_ITEMS.items():
        owned_text = ""
        if item_id == "thuoc_hop_the" and bool(user_doc.get("fusion_potion_owned", False)):
            owned_text = " · <b>Đã sở hữu</b>"
        if item_id == "de_tu" and isinstance(user_doc.get("disciple"), dict):
            owned_text = " · Mua lại sẽ đổi đệ tử"
        lines.append(
            f"\n<code>{item_id}</code> — <b>{escape(str(item['name']))}</b>{owned_text}\n"
            f"Giá {format_number(int(item['price']))} xu · "
            f"{escape(str(item['description']))}"
        )

    lines.append(
        "\nVí dụ: <code>/buy graphine_toi_thuong</code>, "
        "<code>/buy de_tu</code>, <code>/buy thuoc_hop_the</code>."
    )
    await send_message(message, "\n".join(lines))


@new_task
@entertainment_guard
async def buy_item(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await send_message(
            message,
            "Cách dùng: <code>/buy tên_vật_phẩm</code>. Dùng <code>/shop</code> để xem ID.",
        )
        return

    found = _find_item(parts[1])
    if found is None:
        await send_message(
            message,
            "❌ Không tìm thấy vật phẩm. Dùng <code>/shop</code> để xem danh sách.",
        )
        return

    kind, item_id, item = found
    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return

        if kind == "equipment":
            price = int(item["price"])
            if await reserve_coins(collection, message.from_user.id, price) is None:
                await send_message(
                    message,
                    f"❌ Cần <b>{format_number(price)} xu</b> để mua "
                    f"{escape(str(item['name']))}.",
                )
                return
            replacing = item_id in user_doc.get("equipment_sets", {})
            values = {
                f"equipment_sets.{item_id}": new_set_state(item_id),
                "updated_at": time(),
            }
            if not user_doc.get("equipped_set"):
                values["equipped_set"] = item_id
            await collection.update_one(
                {"_id": message.from_user.id},
                {"$set": values},
            )
            action = "mua mới và thay thế" if replacing else "mua"
            await send_message(
                message,
                f"✅ Đã {action} <b>{escape(str(item['name']))}</b> với "
                f"<b>{format_number(price)} xu</b>. Giáp và vũ khí được khôi phục đầy đủ.",
            )
            return

        if kind == "disciple_item":
            price = int(item["price"])
            if item_id == "thuoc_hop_the" and bool(user_doc.get("fusion_potion_owned", False)):
                await send_message(
                    message,
                    "🧪 Mày đã sở hữu Thuốc Hợp Thể Vĩnh Viễn. "
                    "Dùng <code>/thuoc</code> sau khi có đệ tử.",
                )
                return
            if await reserve_coins(collection, message.from_user.id, price) is None:
                await send_message(
                    message,
                    f"❌ Cần <b>{format_number(price)} xu</b> để mua "
                    f"{escape(str(item['name']))}.",
                )
                return
            now = time()
            if item_id == "de_tu":
                replacing = isinstance(user_doc.get("disciple"), dict)
                gender = random_disciple_gender()
                disciple = new_disciple_state(user_doc, gender)
                temp_doc = dict(user_doc)
                temp_doc["disciple"] = disciple
                temp_doc["disciple_fusion_until"] = 0
                temp_doc["disciple_fusion_permanent"] = False
                current_hp, current_max_hp, _ = player_hp_state(user_doc)
                new_max_hp = player_max_hp(temp_doc)
                new_hp = min(
                    new_max_hp,
                    current_hp + max(0, new_max_hp - current_max_hp),
                )
                await collection.update_one(
                    {"_id": message.from_user.id},
                    {
                        "$set": {
                            "disciple": disciple,
                            "disciple_fusion_until": 0,
                            "disciple_fusion_permanent": False,
                            "hp": new_hp,
                            "max_hp": new_max_hp,
                            "hp_regen_at": now,
                            "updated_at": now,
                        },
                        "$inc": {
                            "stats.disciple_purchases": 1,
                            "stats.disciple_replacements": 1 if replacing else 0,
                        },
                    },
                )
                gender_name = "Nam" if gender == "male" else "Nữ"
                special = (
                    "xuyên giáp +50% cho cả hai và HP +20%"
                    if gender == "male"
                    else "sư phụ +30% tấn công và 30% mê hoặc boss trong 5 giây"
                )
                action = "đổi sang" if replacing else "nhận"
                await send_message(
                    message,
                    f"🧑‍🎓 Đã {action} đệ tử <b>{gender_name}</b> với "
                    f"<b>{format_number(price)} xu</b>.\n"
                    f"Đặc tính: <b>{special}</b>.\n"
                    "Xem bằng <code>/detu</code>.",
                )
                return

            await collection.update_one(
                {"_id": message.from_user.id},
                {
                    "$set": {
                        "fusion_potion_owned": True,
                        "fusion_potion_purchased_at": now,
                        "updated_at": now,
                    }
                },
            )
            await send_message(
                message,
                f"🧪 Đã mua <b>{escape(str(item['name']))}</b> với "
                f"<b>{format_number(price)} xu</b>. Vật phẩm tồn tại vĩnh viễn "
                "và không giới hạn lượt dùng. Kích hoạt bằng <code>/thuoc</code>.",
            )
            return

        if kind == "buff":
            price = int(item["price"])
            if await reserve_coins(collection, message.from_user.id, price) is None:
                await send_message(
                    message,
                    f"❌ {escape(str(item['name']))} giá "
                    f"<b>{format_number(price)} xu</b>.",
                )
                return
            field = str(item["field"])
            current_until = float(user_doc.get(field, 0) or 0)
            new_until = max(time(), current_until) + BUFF_SECONDS
            await collection.update_one(
                {"_id": message.from_user.id},
                {"$set": {field: new_until, "updated_at": time()}},
            )
            await send_message(
                message,
                f"{item['emoji']} Đã mua <b>{escape(str(item['name']))}</b> với "
                f"<b>{format_number(price)} xu</b>. Hiệu lực cộng thêm <b>8 giờ</b>.",
            )
            return

        owned = user_doc.get("duck_boats", {})
        if not isinstance(owned, dict):
            owned = {}
        already_owned = item_id == "thuyen_vit_go" or item_id in owned
        price = int(item["price"])
        if not already_owned and price > 0:
            if await reserve_coins(collection, message.from_user.id, price) is None:
                await send_message(
                    message,
                    f"❌ Cần <b>{format_number(price)} xu</b> để mua "
                    f"{escape(str(item['name']))}.",
                )
                return

        values = {
            "equipped_duck_boat": item_id,
            "updated_at": time(),
        }
        if item_id != "thuyen_vit_go":
            values[f"duck_boats.{item_id}"] = {"owned_at": time()}
        await collection.update_one(
            {"_id": message.from_user.id},
            {"$set": values},
        )
        action = "Đã chọn" if already_owned else "Đã mua và chọn"
        price_line = "" if already_owned else f" với <b>{format_number(price)} xu</b>"
        await send_message(
            message,
            f"✅ {action} <b>{escape(str(item['name']))}</b>{price_line}. "
            "Thuyền này sẽ được dùng cho lần <code>/duavit</code> tiếp theo.",
        )
