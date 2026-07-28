from __future__ import annotations

from html import escape
from secrets import SystemRandom
from time import time

from pymongo import ReturnDocument
from pyrogram.enums import ChatMemberStatus, ChatType

from ..helper.ext_utils.bot_utils import new_task, update_user_ldata
from ..core.config_manager import Config
from ..helper.ext_utils.db_handler import database
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    ATTACK_BUFF_PRICE,
    BUFF_SECONDS,
    COIN_BUFF_PRICE,
    DODGE_BUFF_PRICE,
    GAME_LUCK_CHANCE_MULTIPLIER,
    HP_REGEN_TICK_SECONDS,
    LUCK_BUFF_PRICE,
    TRANSFER_LOCK,
    XP_BUFF_PRICE,
    add_coins,
    buff_remaining,
    capped_xp_gain,
    code_collection,
    display_name,
    disciple_summary,
    drop_collection,
    ensure_message_user,
    ensure_user,
    entertainment_enabled,
    entertainment_guard,
    equipment_summary,
    format_number,
    luck_multiplier,
    luck_retry_chance,
    normal_game_coin_reward,
    parse_coin_amount,
    parse_positive_int,
    MAX_PLAYER_LEVEL,
    NORMAL_GAME_REWARD_XP_MULTIPLIER,
    player_attack,
    player_defense,
    player_dodge,
    player_hp_regen,
    player_hp_state,
    player_level,
    player_xp_progress,
    require_game_collection,
    require_user,
    reserve_coins,
    set_entertainment_enabled,
    resolve_target,
    remaining_seconds,
    user_lock,
)


RNG = SystemRandom()
SHIPPER_COOLDOWN = 60
ROCKET_COOLDOWN = 60
DROP_EXPIRE_SECONDS = 600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _roll_tai_xiu() -> tuple[list[int], str, bool]:
    dice = [RNG.randint(1, 6) for _ in range(3)]
    triple = len(set(dice)) == 1
    total = sum(dice)
    result = "tai" if 11 <= total <= 17 else "xiu"
    return dice, result, triple


def _slot_roll() -> tuple[str, float, str]:
    outcome = RNG.choices(
        ["jackpot", "triple", "pair", "cherry", "lose"],
        weights=[1, 19, 180, 200, 600],
        k=1,
    )[0]
    symbols = ["🍒", "🍋", "🍇", "🔔", "💎"]

    if outcome == "jackpot":
        return "7️⃣ 7️⃣ 7️⃣", 100.0, "NỔ HŨ"
    if outcome == "triple":
        symbol = RNG.choice(symbols)
        return f"{symbol} {symbol} {symbol}", 10.0, "Ba biểu tượng"
    if outcome == "pair":
        pair = RNG.choice(symbols)
        other = RNG.choice([value for value in symbols if value != pair])
        values = [pair, pair, other]
        RNG.shuffle(values)
        return " ".join(values), 2.0, "Một cặp"
    if outcome == "cherry":
        values = ["🍒", RNG.choice(symbols), RNG.choice(symbols)]
        RNG.shuffle(values)
        return " ".join(values), 1.5, "Thưởng Cherry"

    while True:
        values = [RNG.choice(symbols) for _ in range(3)]
        if len(set(values)) == 3 and "🍒" not in values:
            return " ".join(values), 0.0, "Trượt"


async def _reserve_bet(collection, message, amount_raw: str):
    user_doc = await ensure_message_user(collection, message)
    if user_doc is None:
        return None, None
    balance = int(user_doc.get("coins", 0))
    try:
        bet = parse_coin_amount(amount_raw, balance)
    except ValueError as exc:
        await send_message(message, f"❌ {escape(str(exc))}")
        return None, None

    reserved = await reserve_coins(collection, message.from_user.id, bet)
    if reserved is None:
        await send_message(
            message,
            f"❌ Không đủ xu. Số dư: <b>{format_number(balance)} xu</b>.",
        )
        return None, None
    return user_doc, bet


async def _finish_bet(
    collection,
    user_id: int,
    *,
    bet: int,
    payout: int,
    win: bool,
) -> None:
    profit = payout - bet
    result_field = "stats.games_won" if win else "stats.games_lost"
    await collection.update_one(
        {"_id": user_id},
        {
            "$inc": {
                "coins": payout,
                "stats.games_played": 1,
                result_field: 1,
                "stats.bet_wagered": bet,
                "stats.bet_profit": profit,
            },
            "$set": {"updated_at": time()},
        },
    )


@new_task
@entertainment_guard
async def tai_xiu(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await send_message(
            message,
            "Cách dùng: <code>/tx tai 1000</code> hoặc "
            "<code>/tx xiu all</code>.",
        )
        return

    choice = parts[1].lower()
    if choice not in {"tai", "tài", "xiu", "xỉu"}:
        await send_message(message, "❌ Lựa chọn phải là tai hoặc xiu.")
        return
    choice = "tai" if choice in {"tai", "tài"} else "xiu"

    async with user_lock(message.from_user.id):
        user_doc, bet = await _reserve_bet(collection, message, parts[2])
        if user_doc is None:
            return

        dice, result, triple = _roll_tai_xiu()
        win = result == choice and not triple
        luck_used = False
        if not win and RNG.random() < luck_retry_chance(user_doc):
            dice, result, triple = _roll_tai_xiu()
            win = result == choice and not triple
            luck_used = True

        payout = bet * 2 if win else 0
        await _finish_bet(
            collection,
            message.from_user.id,
            bet=bet,
            payout=payout,
            win=win,
        )

    total = sum(dice)
    result_label = "Tài" if result == "tai" else "Xỉu"
    special = " — Bão, nhà cái thắng" if triple else ""
    luck_line = "\n🍀 May mắn đã kích hoạt lượt quay lại." if luck_used else ""
    outcome = (
        f"✅ Thắng <b>{format_number(payout - bet)} xu</b>"
        if win
        else f"❌ Thua <b>{format_number(bet)} xu</b>"
    )
    await send_message(
        message,
        "🎲 <b>Tài Xỉu</b>\n\n"
        f"Xúc xắc: <b>{dice[0]} - {dice[1]} - {dice[2]}</b>\n"
        f"Tổng: <b>{total}</b> — {result_label}{special}\n"
        f"{outcome}{luck_line}",
    )


@new_task
@entertainment_guard
async def no_hu(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(
            message,
            "Cách dùng: <code>/nohu 1000</code> hoặc <code>/nohu all</code>.",
        )
        return

    async with user_lock(message.from_user.id):
        user_doc, bet = await _reserve_bet(collection, message, parts[1])
        if user_doc is None:
            return

        symbols, multiplier, label = _slot_roll()
        luck_used = False
        if multiplier == 0 and RNG.random() < luck_retry_chance(user_doc):
            symbols, multiplier, label = _slot_roll()
            luck_used = True

        payout = int(round(bet * multiplier))
        win = payout > bet
        await _finish_bet(
            collection,
            message.from_user.id,
            bet=bet,
            payout=payout,
            win=win,
        )

    net = payout - bet
    result = (
        f"✅ {label}: nhận <b>{format_number(payout)} xu</b> "
        f"(lãi {format_number(net)} xu)"
        if payout
        else f"❌ Trượt: mất <b>{format_number(bet)} xu</b>"
    )
    luck_line = "\n🍀 May mắn đã kích hoạt lượt quay lại." if luck_used else ""
    await send_message(
        message,
        f"🎰 <b>Nổ Hũ</b>\n\n{symbols}\n\n{result}{luck_line}",
    )


@new_task
@entertainment_guard
async def dice_bet(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    parts = (message.text or "").split()
    if len(parts) != 3:
        await send_message(
            message,
            "Cách dùng: <code>/xucxac 4 1000</code> hoặc "
            "<code>/xucxac 4 all</code>.",
        )
        return

    try:
        selected = int(parts[1])
    except ValueError:
        selected = 0
    if selected not in range(1, 7):
        await send_message(message, "❌ Mặt xúc xắc phải từ 1 đến 6.")
        return

    async with user_lock(message.from_user.id):
        user_doc, bet = await _reserve_bet(collection, message, parts[2])
        if user_doc is None:
            return

        rolled = RNG.randint(1, 6)
        win = rolled == selected
        luck_used = False
        if not win and RNG.random() < luck_retry_chance(user_doc):
            rolled = RNG.randint(1, 6)
            win = rolled == selected
            luck_used = True

        payout = int(round(bet * 5.5)) if win else 0
        await _finish_bet(
            collection,
            message.from_user.id,
            bet=bet,
            payout=payout,
            win=win,
        )

    result = (
        f"✅ Trúng số {rolled}, nhận <b>{format_number(payout)} xu</b>."
        if win
        else f"❌ Ra số {rolled}, mất <b>{format_number(bet)} xu</b>."
    )
    luck_line = "\n🍀 May mắn đã kích hoạt lượt lắc lại." if luck_used else ""
    await send_message(
        message,
        f"🎲 <b>Cược Xúc Xắc</b>\n\n"
        f"Mày chọn: <b>{selected}</b>\n{result}{luck_line}",
    )


@new_task
@entertainment_guard
async def shipper_job(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        remaining = remaining_seconds(
            user_doc.get("cooldowns", {}).get("shipper"),
            SHIPPER_COOLDOWN,
        )
        if remaining:
            await send_message(
                message,
                f"⏳ Chờ <b>{remaining} giây</b> để nhận đơn tiếp.",
            )
            return

        reward = RNG.randint(400, 1_800)
        vip = RNG.random() < min(
            0.90,
            0.10 * luck_multiplier(user_doc) * GAME_LUCK_CHANCE_MULTIPLIER,
        )
        if vip:
            reward = int(round(reward * 2.5))
        reward *= NORMAL_GAME_REWARD_XP_MULTIPLIER
        reward = normal_game_coin_reward(user_doc, reward)
        xp = capped_xp_gain(
            user_doc,
            (8 if vip else 4) * NORMAL_GAME_REWARD_XP_MULTIPLIER,
        )

        now = time()
        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$set": {
                    "cooldowns.shipper": now,
                    "updated_at": now,
                },
                "$inc": {
                    "coins": reward,
                    "xp": xp,
                    "stats.shipper_count": 1,
                },
            },
        )

    title = "Đơn VIP" if vip else RNG.choice(
        ["Giao đồ ăn", "Giao tài liệu", "Giao bưu kiện", "Chở hàng nhanh"]
    )
    await send_message(
        message,
        f"🛵 <b>{title}</b>\n\n"
        f"Hoàn thành chuyến giao hàng và nhận "
        f"<b>{format_number(reward)} xu</b>.\n"
        f"⭐ Kinh nghiệm: <b>+{xp} XP</b>.",
    )


@new_task
@entertainment_guard
async def rocket_launch(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        remaining = remaining_seconds(
            user_doc.get("cooldowns", {}).get("rocket"),
            ROCKET_COOLDOWN,
        )
        if remaining:
            await send_message(
                message,
                f"⏳ Chờ <b>{remaining} giây</b> để phóng tiếp.",
            )
            return

        outcome = RNG.choices(
            ["success", "partial", "fail"],
            weights=[60, 25, 15],
            k=1,
        )[0]
        luck_used = False
        if outcome == "fail" and RNG.random() < luck_retry_chance(user_doc):
            outcome = RNG.choices(
                ["success", "partial", "fail"],
                weights=[60, 25, 15],
                k=1,
            )[0]
            luck_used = True

        if outcome == "success":
            reward = RNG.randint(1_000, 4_000)
            label = RNG.choice(
                ["Hạ cánh Mặt Trăng", "Bay quanh Sao Hỏa", "Thu thập đá vũ trụ"]
            )
            xp = 12
        elif outcome == "partial":
            reward = RNG.randint(300, 1_000)
            label = "Tên lửa quay về sớm nhưng vẫn thu được dữ liệu"
            xp = 5
        else:
            reward = 0
            label = "Tên lửa mất tín hiệu"
            xp = 1

        reward *= NORMAL_GAME_REWARD_XP_MULTIPLIER
        reward = normal_game_coin_reward(user_doc, reward)
        xp = capped_xp_gain(
            user_doc,
            xp * NORMAL_GAME_REWARD_XP_MULTIPLIER,
        )
        now = time()
        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$set": {
                    "cooldowns.rocket": now,
                    "updated_at": now,
                },
                "$inc": {
                    "coins": reward,
                    "xp": xp,
                    "stats.rocket_count": 1,
                },
            },
        )

    luck_line = "\n🍀 May mắn đã kích hoạt lần phóng lại." if luck_used else ""
    reward_line = (
        f"Nhận <b>{format_number(reward)} xu</b>."
        if reward
        else "Không nhận được xu."
    )
    xp_line = f"\n⭐ Kinh nghiệm: <b>+{xp} XP</b>."
    await send_message(
        message,
        f"🚀 <b>{label}</b>\n\n{reward_line}{xp_line}{luck_line}",
    )


BUFF_SHOP = {
    "mayman": {
        "aliases": {"mayman", "luck", "lucky"},
        "name": "Bùa x2 May Mắn",
        "emoji": "🍀",
        "price": LUCK_BUFF_PRICE,
        "field": "luck_buff_until",
        "effect": "Nhân đôi hệ số may mắn",
    },
    "exp": {
        "aliases": {"exp", "xp"},
        "name": "Bùa x2 EXP",
        "emoji": "⭐",
        "price": XP_BUFF_PRICE,
        "field": "xp_buff_until",
        "effect": "Nhân đôi EXP nhận được",
    },
    "tien": {
        "aliases": {"tien", "coin", "coins", "money", "xu"},
        "name": "Bùa x2 Tiền",
        "emoji": "💰",
        "price": COIN_BUFF_PRICE,
        "field": "coin_buff_until",
        "effect": "Nhân đôi xu từ bù nhìn, đua vịt và Ma Sói; không áp dụng boss",
    },
    "tancong": {
        "aliases": {"tancong", "attack", "atk"},
        "name": "Bùa x2 Tấn Công",
        "emoji": "⚔️",
        "price": ATTACK_BUFF_PRICE,
        "field": "attack_buff_until",
        "effect": "Nhân đôi tấn công cơ bản",
    },
    "nedon": {
        "aliases": {"nedon", "dodge", "ne"},
        "name": "Bùa x1.5 Né Đòn",
        "emoji": "💨",
        "price": DODGE_BUFF_PRICE,
        "field": "dodge_buff_until",
        "effect": "Nhân 1,5 tỉ lệ né đòn",
    },
}


def _find_buff(raw: str):
    key = raw.strip().lower()
    for buff_id, item in BUFF_SHOP.items():
        if key == buff_id or key in item["aliases"]:
            return buff_id, item
    return None


def _duration_text(seconds: int) -> str:
    if seconds <= 0:
        return "Không hoạt động"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}p"


def _buff_shop_text(user_doc: dict) -> str:
    lines = [
        "🔮 <b>SHOP BÙA 8 GIỜ</b>",
        "",
        "Mua bằng: <code>/shopmayman loại_bùa</code>",
        "",
    ]
    for buff_id, item in BUFF_SHOP.items():
        remaining = buff_remaining(user_doc, item["field"])
        lines.append(
            f"{item['emoji']} <b>{item['name']}</b> — "
            f"<b>{format_number(item['price'])} xu</b>\n"
            f"   ID: <code>{buff_id}</code> · {item['effect']}\n"
            f"   Trạng thái: <b>{_duration_text(remaining)}</b>"
        )
    lines.extend(
        [
            "",
            "Mua lại cùng loại sẽ cộng thêm 8 giờ vào thời gian còn lại.",
            "Có thể kích hoạt nhiều loại bùa cùng lúc.",
        ]
    )
    return "\n".join(lines)


@new_task
@entertainment_guard
async def buy_luck_buff(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    parts = (message.text or "").split()
    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return

        if len(parts) == 1:
            await send_message(message, _buff_shop_text(user_doc))
            return

        if len(parts) != 2:
            await send_message(
                message,
                "Cách dùng: <code>/shopmayman</code> hoặc "
                "<code>/shopmayman exp</code>.",
            )
            return

        found = _find_buff(parts[1])
        if found is None:
            await send_message(
                message,
                "❌ Loại bùa hợp lệ: "
                "<code>mayman</code>, <code>exp</code>, "
                "<code>tien</code>, <code>tancong</code>, "
                "<code>nedon</code>.",
            )
            return

        _, item = found
        if await reserve_coins(
            collection,
            message.from_user.id,
            int(item["price"]),
        ) is None:
            await send_message(
                message,
                f"❌ {item['name']} giá "
                f"<b>{format_number(item['price'])} xu</b>.",
            )
            return

        current_until = float(user_doc.get(item["field"], 0) or 0)
        new_until = max(time(), current_until) + BUFF_SECONDS
        await collection.update_one(
            {"_id": message.from_user.id},
            {
                "$set": {
                    item["field"]: new_until,
                    "updated_at": time(),
                }
            },
        )

    await send_message(
        message,
        f"{item['emoji']} Đã mua <b>{item['name']}</b> với "
        f"<b>{format_number(item['price'])} xu</b>.\n"
        f"⏱ Hiệu lực được cộng thêm: <b>8 giờ</b>.",
    )


@new_task
@entertainment_guard
async def redeem_code(_, message):
    collection = await require_game_collection(message)
    codes = code_collection()
    if collection is None or codes is None or await require_user(message) is None:
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(message, "Cách dùng: <code>/code ABCD123456</code>")
        return

    code = parts[1].strip().upper()
    claimed = await codes.find_one_and_update(
        {
            "_id": code,
            "redeemed_by": {"$ne": message.from_user.id},
        },
        {
            "$addToSet": {"redeemed_by": message.from_user.id},
            "$inc": {"redeem_count": 1},
            "$set": {"last_redeemed_at": time()},
        },
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        exists = await codes.find_one({"_id": code})
        text = (
            "❌ Code không tồn tại."
            if exists is None
            else "❌ Mày đã dùng code này rồi."
        )
        await send_message(message, text)
        return

    amount = int(claimed["amount"])
    try:
        await ensure_message_user(collection, message)
        await add_coins(collection, message.from_user.id, amount)
    except Exception:
        await codes.update_one(
            {"_id": code},
            {
                "$pull": {"redeemed_by": message.from_user.id},
                "$inc": {"redeem_count": -1},
            },
        )
        raise

    await send_message(
        message,
        f"🎁 Nhập code thành công, nhận <b>{format_number(amount)} xu</b>.",
    )


@new_task
@entertainment_guard
async def drop_coins(_, message):
    collection = await require_game_collection(message)
    drops = drop_collection()
    if collection is None or drops is None or await require_user(message) is None:
        return

    if message.chat.type == ChatType.PRIVATE:
        await send_message(message, "❌ /drop chỉ dùng trong nhóm.")
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(message, "Cách dùng: <code>/drop 1000</code>")
        return

    async with user_lock(message.from_user.id):
        user_doc = await ensure_message_user(collection, message)
        if user_doc is None:
            return
        try:
            amount = parse_coin_amount(parts[1], int(user_doc.get("coins", 0)))
        except ValueError as exc:
            await send_message(message, f"❌ {escape(str(exc))}")
            return

        if await reserve_coins(collection, message.from_user.id, amount) is None:
            await send_message(message, "❌ Không đủ xu.")
            return

        sent = await send_message(
            message,
            f"🎁 <b>{escape(display_name(message))}</b> đã thả rương "
            f"<b>{format_number(amount)} xu</b>.\n"
            "Reply tin nhắn này bằng <code>/pickup</code> để nhặt.",
        )
        if not hasattr(sent, "id"):
            await add_coins(collection, message.from_user.id, amount)
            await send_message(
                message,
                "❌ Không tạo được rương, xu đã được hoàn lại.",
            )
            return

        try:
            await drops.insert_one(
                {
                    "_id": f"{message.chat.id}:{sent.id}",
                    "chat_id": message.chat.id,
                    "message_id": sent.id,
                    "amount": amount,
                    "creator_id": message.from_user.id,
                    "created_at": time(),
                    "expires_at": time() + DROP_EXPIRE_SECONDS,
                    "claimed_by": None,
                }
            )
        except Exception:
            await add_coins(collection, message.from_user.id, amount)
            await sent.edit("❌ Rương lỗi, xu đã được hoàn lại.")
            raise


@new_task
@entertainment_guard
async def pickup_drop(_, message):
    collection = await require_game_collection(message)
    drops = drop_collection()
    if collection is None or drops is None or await require_user(message) is None:
        return

    reply = message.reply_to_message
    if reply is None:
        await send_message(
            message,
            "❌ Hãy reply đúng tin nhắn rương bằng /pickup.",
        )
        return

    key = f"{message.chat.id}:{reply.id}"
    drop = await drops.find_one({"_id": key})
    if drop is None:
        await send_message(message, "❌ Tin nhắn này không phải rương xu.")
        return
    if int(drop["creator_id"]) == message.from_user.id:
        await send_message(message, "❌ Người thả rương không thể tự nhặt.")
        return

    claimed = await drops.find_one_and_update(
        {
            "_id": key,
            "claimed_by": None,
            "expires_at": {"$gt": time()},
        },
        {
            "$set": {
                "claimed_by": message.from_user.id,
                "claimed_at": time(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        current = await drops.find_one({"_id": key})
        if current and float(current.get("expires_at", 0)) <= time():
            await send_message(message, "⌛ Rương đã hết hạn.")
        else:
            await send_message(message, "❌ Rương đã có người nhặt.")
        return

    await ensure_message_user(collection, message)
    await add_coins(collection, message.from_user.id, int(claimed["amount"]))
    await send_message(
        message,
        f"🎉 Nhặt được <b>{format_number(int(claimed['amount']))} xu</b>.",
    )


@new_task
@entertainment_guard
async def pay_coins(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    parts = (message.text or "").split()
    reply = message.reply_to_message

    if reply and reply.from_user and len(parts) == 2:
        amount_raw = parts[1]
        target = (
            reply.from_user.id,
            reply.from_user.first_name or str(reply.from_user.id),
        )
        await ensure_user(collection, reply.from_user)
    elif len(parts) == 3:
        amount_raw = parts[2]
        target = await resolve_target(collection, message, parts[1])
        if target is None:
            return
    else:
        await send_message(
            message,
            "Cách dùng: <code>/pay @user 1000</code> hoặc "
            "reply <code>/pay 1000</code>.",
        )
        return

    target_id, target_name = target
    if target_id == message.from_user.id:
        await send_message(message, "❌ Không thể chuyển xu cho chính mình.")
        return

    async with TRANSFER_LOCK:
        sender = await ensure_message_user(collection, message)
        if sender is None:
            return
        try:
            amount = parse_coin_amount(
                amount_raw,
                int(sender.get("coins", 0)),
            )
        except ValueError as exc:
            await send_message(message, f"❌ {escape(str(exc))}")
            return

        if await reserve_coins(
            collection,
            message.from_user.id,
            amount,
        ) is None:
            await send_message(message, "❌ Không đủ xu.")
            return
        try:
            await add_coins(collection, int(target_id), amount)
        except Exception:
            await add_coins(collection, message.from_user.id, amount)
            raise

    await send_message(
        message,
        f"💸 Đã chuyển <b>{format_number(amount)} xu</b> cho "
        f"<b>{escape(str(target_name))}</b>.",
    )


@new_task
@entertainment_guard
async def account_stats(_, message):
    collection = await require_game_collection(message)
    if collection is None or await require_user(message) is None:
        return

    user_doc = await ensure_message_user(collection, message)
    if user_doc is None:
        return
    stats = user_doc.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}

    played = int(stats.get("games_played", 0) or 0)
    won = int(stats.get("games_won", 0) or 0)
    lost = int(stats.get("games_lost", 0) or 0)
    win_rate = (won / played * 100.0) if played else 0.0
    xp = int(user_doc.get("xp", 0) or 0)
    level = player_level(user_doc)
    xp_progress, xp_required = player_xp_progress(xp)
    level_progress = (
        "TỐI ĐA"
        if level >= MAX_PLAYER_LEVEL
        else f"{format_number(xp_progress)}/{format_number(xp_required)} XP"
    )
    attack = player_attack(user_doc)
    defense = player_defense(user_doc)
    dodge = player_dodge(user_doc)
    hp, max_hp, respawn_remaining = player_hp_state(user_doc)
    hp_regen = player_hp_regen(user_doc)
    hp_status = (
        f"Đang hồi sinh, còn {respawn_remaining // 60}p "
        f"{respawn_remaining % 60}s"
        if respawn_remaining
        else "Sẵn sàng chiến đấu"
    )
    multiplier = luck_multiplier(user_doc)
    active_buff_lines = []
    for item in BUFF_SHOP.values():
        remaining = buff_remaining(user_doc, item["field"])
        if remaining:
            active_buff_lines.append(
                f"{item['emoji']} {item['name']}: "
                f"<b>{_duration_text(remaining)}</b>"
            )
    buff_text = (
        "\n".join(active_buff_lines)
        if active_buff_lines
        else "Không có bùa đang hoạt động"
    )

    await send_message(
        message,
        f"📊 <b>Thống kê của {escape(display_name(message))}</b>\n\n"
        f"💰 Số dư: <b>{format_number(int(user_doc.get('coins', 0)))} xu</b>\n"
        f"⭐ Cấp độ: <b>{level}/{MAX_PLAYER_LEVEL}</b> — {level_progress}\n"
        f"⚔️ Tấn công cơ bản: <b>{format_number(attack)}</b>\n"
        f"🛡 Phòng thủ cơ bản: <b>{format_number(defense)}</b>\n"
        f"💨 Né đòn: <b>{dodge * 100:.2f}%</b>\n"
        f"❤️ HP: <b>{format_number(hp)}/{format_number(max_hp)}</b> — {hp_status}\n"
        f"💚 Hồi HP: <b>{format_number(hp_regen)} HP/"
        f"{HP_REGEN_TICK_SECONDS} giây</b>\n\n"
        f"🔮 <b>Bùa đang hoạt động</b>\n{buff_text}\n\n"
        f"{equipment_summary(user_doc)}\n\n"
        f"{disciple_summary(user_doc)}\n\n"
        f"👹 <b>Thống kê boss</b>\n"
        f"Đòn đánh: <b>{format_number(int(stats.get('boss_hits', 0)))}</b>\n"
        f"Sát thương: <b>{format_number(int(stats.get('boss_damage', 0)))}</b>\n"
        f"Boss kết liễu: <b>{format_number(int(stats.get('boss_kills', 0)))}</b>\n"
        f"Xu chia thưởng: <b>{format_number(int(stats.get('boss_rewards', 0)))} xu</b>\n"
        f"XP từ boss: <b>{format_number(int(stats.get('boss_xp', 0)))}</b>\n"
        f"Xu rơi khi đánh: <b>{format_number(int(stats.get('boss_coin_drops', 0)))} xu</b>\n"
        f"Mảnh trang bị: <b>{format_number(int(stats.get('boss_part_drops', 0)))}</b> · "
        f"Set nguyên: <b>{format_number(int(stats.get('boss_set_drops', 0)))}</b>\n\n"
        f"🎯 <b>Thống kê bù nhìn</b>\n"
        f"Số lượt: <b>{format_number(int(stats.get('dummy_hits', 0)))}</b>\n"
        f"Sát thương: <b>{format_number(int(stats.get('dummy_damage', 0)))}</b>\n"
        f"XP: <b>{format_number(int(stats.get('dummy_xp', 0)))}</b> · "
        f"Xu: <b>{format_number(int(stats.get('dummy_coins', 0)))} xu</b>\n\n"
        f"🦆 <b>Thống kê đua vịt</b>\n"
        f"Số lượt: <b>{format_number(int(stats.get('duck_races', 0)))}</b> · "
        f"Kỷ lục: <b>{format_number(int(stats.get('duck_best_distance', 0)))} m</b>\n"
        f"Tổng quãng đường: <b>{format_number(int(stats.get('duck_distance', 0)))} m</b>\n"
        f"Xu: <b>{format_number(int(stats.get('duck_coins', 0)))} xu</b> · "
        f"EXP: <b>{format_number(int(stats.get('duck_xp', 0)))}</b>\n\n"
        f"🐺 <b>Thống kê Ma Sói</b>\n"
        f"Số ván: <b>{format_number(int(stats.get('werewolf_games', 0)))}</b> · "
        f"Chiến thắng: <b>{format_number(int(stats.get('werewolf_wins', 0)))}</b>\n"
        f"Thắng phe Sói: <b>{format_number(int(stats.get('werewolf_wolf_wins', 0)))}</b> · "
        f"Thắng phe Dân: <b>{format_number(int(stats.get('werewolf_village_wins', 0)))}</b>\n"
        f"Xu: <b>{format_number(int(stats.get('werewolf_coins', 0)))} xu</b> · "
        f"EXP: <b>{format_number(int(stats.get('werewolf_xp', 0)))}</b>\n\n"
        f"🎰 <b>Thống kê cược</b>\n"
        f"Ván cược: <b>{format_number(played)}</b> · "
        f"Thắng <b>{format_number(won)}</b> · Thua <b>{format_number(lost)}</b>\n"
        f"Tỉ lệ thắng: <b>{win_rate:.2f}%</b> · "
        f"Lãi/lỗ: <b>{format_number(int(stats.get('bet_profit', 0)))} xu</b>\n\n"
        f"📜 <b>Dữ liệu hoạt động cũ</b>\n"
        f"Câu cá: <b>{format_number(int(stats.get('fish_count', 0)))}</b> · "
        f"Đào mỏ: <b>{format_number(int(stats.get('mine_count', 0)))}</b> · "
        f"Shipper: <b>{format_number(int(stats.get('shipper_count', 0)))}</b> · "
        f"Rocket: <b>{format_number(int(stats.get('rocket_count', 0)))}</b>\n"
        f"☠️ Số lần bị hạ: <b>{format_number(int(stats.get('deaths', 0)))}</b>\n"
        f"🍀 Hệ số may mắn: <b>x{multiplier:.2f}</b> · "
        f"Hệ số trò chơi: <b>x{GAME_LUCK_CHANCE_MULTIPLIER:.0f}</b>",
    )



async def _can_manage_entertainment(client, message, chat_id: int | None) -> bool:
    user = message.from_user
    if user is None:
        return False
    if int(user.id) == int(Config.OWNER_ID):
        return True
    if chat_id is None:
        return False
    try:
        member = await client.get_chat_member(int(chat_id), int(user.id))
    except Exception:
        return False
    return member.status in {
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
    }


@new_task
async def toggle_entertainment(client, message):
    parts = (message.text or "").split()
    actions_on = {"on", "bat", "bật", "enable"}
    actions_off = {"off", "tat", "tắt", "disable"}
    actions_status = {"status", "trangthai", "trạngthái"}
    valid_actions = actions_on | actions_off | actions_status

    global_scope = len(parts) >= 2 and parts[1].strip().lower() == "global"
    if global_scope:
        action = parts[2].strip().lower() if len(parts) >= 3 else "status"
        if len(parts) > 3 or action not in valid_actions:
            await send_message(
                message,
                "Cách dùng: <code>/giaitri global on|off|status</code>.",
            )
            return
        target_chat_id = None
        scope_name = "toàn bộ bot"
    else:
        action = parts[1].strip().lower() if len(parts) >= 2 else "status"
        if action not in valid_actions or len(parts) > 3:
            await send_message(
                message,
                "Cách dùng:\n"
                "<code>/giaitri on|off|status</code> — nhóm hiện tại\n"
                "<code>/giaitri on|off|status group_id</code> — nhóm được chỉ định\n"
                "<code>/giaitri global on|off|status</code> — toàn bộ bot [Owner]",
            )
            return
        if len(parts) == 3:
            try:
                target_chat_id = int(parts[2])
            except ValueError:
                await send_message(message, "❌ Group ID không hợp lệ.")
                return
        elif message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            target_chat_id = int(message.chat.id)
        elif int(message.from_user.id) == int(Config.OWNER_ID):
            target_chat_id = None
            global_scope = True
        else:
            await send_message(
                message,
                "❌ Hãy dùng lệnh trong nhóm hoặc thêm group_id.",
            )
            return
        scope_name = "toàn bộ bot" if target_chat_id is None else f"nhóm <code>{target_chat_id}</code>"

    if global_scope and int(message.from_user.id) != int(Config.OWNER_ID):
        await send_message(message, "❌ Chỉ Owner được thay đổi trạng thái toàn bộ bot.")
        return
    if not await _can_manage_entertainment(client, message, target_chat_id):
        await send_message(
            message,
            "❌ Chỉ Owner hoặc quản trị viên của nhóm được dùng lệnh này.",
        )
        return

    if action in actions_status:
        enabled = await entertainment_enabled(target_chat_id)
        verb = "hiện đang"
    else:
        enabled = action in actions_on
        await set_entertainment_enabled(enabled, target_chat_id)
        verb = "đã được đặt thành"

    await send_message(
        message,
        f"🎮 Khu vực giải trí của {scope_name} {verb} "
        f"<b>{'BẬT' if enabled else 'TẮT'}</b>.\n"
        + (
            "Bảng lệnh hỗ trợ giải trí sẽ được hiển thị trong /help."
            if enabled
            else "Bảng lệnh hỗ trợ giải trí cũng đã bị ẩn khỏi /help."
        ),
    )

@new_task
async def create_code(_, message):
    collection = await require_game_collection(message)
    codes = code_collection()
    if collection is None or codes is None:
        return

    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(message, "Cách dùng: <code>/crecode 10000</code>")
        return
    try:
        amount = parse_positive_int(parts[1])
    except ValueError as exc:
        await send_message(message, f"❌ {escape(str(exc))}")
        return

    for _ in range(10):
        code = "".join(RNG.choice(CODE_ALPHABET) for _ in range(10))
        if await codes.find_one({"_id": code}) is None:
            break
    else:
        await send_message(message, "❌ Không tạo được mã duy nhất, thử lại.")
        return

    await codes.insert_one(
        {
            "_id": code,
            "amount": amount,
            "created_by": message.from_user.id,
            "created_at": time(),
            "redeemed_by": [],
            "redeem_count": 0,
        }
    )
    await send_message(
        message,
        f"✅ Code mới: <code>{code}</code>\n"
        f"Giá trị: <b>{format_number(amount)} xu</b>.\n"
        "Mỗi tài khoản dùng được một lần.",
    )


@new_task
async def delete_code(_, message):
    codes = code_collection()
    if codes is None:
        await send_message(message, "❌ MongoDB chưa sẵn sàng.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await send_message(
            message,
            "Cách dùng: <code>/delcode ABCD123456</code>",
        )
        return
    result = await codes.delete_one({"_id": parts[1].strip().upper()})
    await send_message(
        message,
        "✅ Đã xóa code."
        if result.deleted_count
        else "❌ Không tìm thấy code.",
    )


def _admin_target_and_amount(message, parts):
    reply = message.reply_to_message
    if reply and reply.from_user and len(parts) == 2:
        return None, parts[1], reply.from_user
    if len(parts) == 3:
        return parts[1], parts[2], None
    return None, None, None


@new_task
async def set_coins(_, message):
    collection = await require_game_collection(message)
    if collection is None:
        return
    parts = (message.text or "").split()
    target_raw, amount_raw, reply_user = _admin_target_and_amount(
        message,
        parts,
    )
    if amount_raw is None:
        await send_message(
            message,
            "Cách dùng: <code>/setcoins @user 1000</code>, "
            "<code>/setcoins all 1000</code> hoặc "
            "reply <code>/setcoins 1000</code>.",
        )
        return
    try:
        amount = int(amount_raw.replace(".", "").replace(",", ""))
    except ValueError:
        await send_message(message, "❌ Số xu phải là số nguyên không âm.")
        return
    if amount < 0:
        await send_message(message, "❌ Số xu không được âm.")
        return

    if reply_user is not None:
        await ensure_user(collection, reply_user)
        target = (
            reply_user.id,
            reply_user.first_name or str(reply_user.id),
        )
    else:
        target = await resolve_target(
            collection,
            message,
            target_raw,
            allow_all=True,
        )
        if target is None:
            return

    target_id, target_name = target
    if target_id == "all":
        result = await collection.update_many(
            {},
            {"$set": {"coins": amount, "updated_at": time()}},
        )
        await send_message(
            message,
            f"✅ Đã đặt số dư <b>{format_number(amount)} xu</b> cho "
            f"<b>{result.modified_count}</b> tài khoản.",
        )
        return

    await collection.update_one(
        {"_id": int(target_id)},
        {"$set": {"coins": amount, "updated_at": time()}},
        upsert=True,
    )
    await send_message(
        message,
        f"✅ Đã đặt số dư của <b>{escape(str(target_name))}</b> thành "
        f"<b>{format_number(amount)} xu</b>.",
    )


@new_task
async def gift_coins(_, message):
    collection = await require_game_collection(message)
    if collection is None:
        return
    parts = (message.text or "").split()
    target_raw, amount_raw, reply_user = _admin_target_and_amount(
        message,
        parts,
    )
    if amount_raw is None:
        await send_message(
            message,
            "Cách dùng: <code>/giftcoins @user 1000</code> hoặc "
            "reply <code>/giftcoins 1000</code>.",
        )
        return
    try:
        amount = parse_positive_int(amount_raw)
    except ValueError as exc:
        await send_message(message, f"❌ {escape(str(exc))}")
        return

    if reply_user is not None:
        await ensure_user(collection, reply_user)
        target = (
            reply_user.id,
            reply_user.first_name or str(reply_user.id),
        )
    else:
        target = await resolve_target(collection, message, target_raw)
        if target is None:
            return

    target_id, target_name = target
    await add_coins(collection, int(target_id), amount)
    await send_message(
        message,
        f"✅ Đã bơm <b>{format_number(amount)} xu</b> cho "
        f"<b>{escape(str(target_name))}</b>.",
    )


@new_task
async def set_luck(_, message):
    collection = await require_game_collection(message)
    if collection is None:
        return
    parts = (message.text or "").split()
    target_raw, percent_raw, reply_user = _admin_target_and_amount(
        message,
        parts,
    )
    if percent_raw is None:
        await send_message(
            message,
            "Cách dùng: <code>/lucky @user 25</code> hoặc "
            "reply <code>/lucky 25</code>.",
        )
        return
    try:
        percent = int(percent_raw)
    except ValueError:
        percent = -1
    if not 0 <= percent <= 100:
        await send_message(message, "❌ Tỉ lệ phải từ 0 đến 100%.")
        return

    if reply_user is not None:
        await ensure_user(collection, reply_user)
        target = (
            reply_user.id,
            reply_user.first_name or str(reply_user.id),
        )
    else:
        target = await resolve_target(collection, message, target_raw)
        if target is None:
            return

    target_id, target_name = target
    await collection.update_one(
        {"_id": int(target_id)},
        {"$set": {"luck_admin_percent": percent, "updated_at": time()}},
        upsert=True,
    )
    await send_message(
        message,
        f"🍀 Đã đặt may mắn của <b>{escape(str(target_name))}</b> thành "
        f"<b>+{percent}%</b>.",
    )


@new_task
async def reset_luck(_, message):
    collection = await require_game_collection(message)
    if collection is None:
        return
    parts = (message.text or "").split()
    reply = message.reply_to_message
    if reply and reply.from_user and len(parts) == 1:
        await ensure_user(collection, reply.from_user)
        target = (
            reply.from_user.id,
            reply.from_user.first_name or str(reply.from_user.id),
        )
    elif len(parts) == 2:
        target = await resolve_target(collection, message, parts[1])
        if target is None:
            return
    else:
        await send_message(
            message,
            "Cách dùng: <code>/unlucky @user</code> hoặc "
            "reply <code>/unlucky</code>.",
        )
        return

    target_id, target_name = target
    await collection.update_one(
        {"_id": int(target_id)},
        {"$set": {"luck_admin_percent": 0, "updated_at": time()}},
        upsert=True,
    )
    await send_message(
        message,
        f"✅ Đã đưa may mắn của <b>{escape(str(target_name))}</b> "
        "về bình thường.",
    )


def _parse_group_id(message, parts) -> int:
    if len(parts) >= 2:
        return int(parts[1])
    return int(message.chat.id)


@new_task
async def allow_group(_, message):
    parts = (message.text or "").split()
    try:
        group_id = _parse_group_id(message, parts)
    except ValueError:
        await send_message(message, "❌ Group ID không hợp lệ.")
        return

    update_user_ldata(group_id, "AUTH", True)
    await database.update_user_data(group_id)
    await send_message(message, f"✅ Đã duyệt nhóm <code>{group_id}</code>.")


@new_task
async def delete_group(_, message):
    parts = (message.text or "").split()
    try:
        group_id = _parse_group_id(message, parts)
    except ValueError:
        await send_message(message, "❌ Group ID không hợp lệ.")
        return

    update_user_ldata(group_id, "AUTH", False)
    await database.update_user_data(group_id)
    await send_message(message, f"✅ Đã gỡ quyền nhóm <code>{group_id}</code>.")
