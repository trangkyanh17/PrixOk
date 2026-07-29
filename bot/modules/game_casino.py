from __future__ import annotations

from asyncio import Lock
from secrets import SystemRandom

from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.telegram_helper.message_utils import send_message

from .game_common import (
    add_coins,
    entertainment_enabled,
    format_coins,
    get_coins,
    get_db,
    get_user,
    raw_name,
    take_coins,
)


RNG = SystemRandom()
USER_LOCKS: dict[int, Lock] = {}

TAI_XIU_CHOICES = {
    "tai": "TÀI",
    "tài": "TÀI",
    "xiu": "XỈU",
    "xỉu": "XỈU",
}


def _user_lock(user_id: int) -> Lock:
    return USER_LOCKS.setdefault(int(user_id), Lock())


def _parse_bet(token: str, balance: int) -> int:
    value = token.strip().lower()
    if value in {"all", "tatca", "tấtcả"}:
        if balance <= 0:
            raise ValueError("Ví của bạn đang hết xu.")
        return balance

    normalized = (
        value.replace(".", "")
        .replace(",", "")
        .replace("_", "")
        .removesuffix("xu")
        .strip()
    )
    if not normalized.isdigit():
        raise ValueError(
            "Mức cược phải là số nguyên dương hoặc <code>all</code>."
        )

    amount = int(normalized)
    if amount <= 0:
        raise ValueError("Mức cược phải lớn hơn 0.")
    if amount > balance:
        raise ValueError(
            "Không đủ xu. Số dư hiện tại: "
            f"<b>{format_coins(balance)}</b>."
        )
    return amount


async def _reserve_bet(message, token: str) -> int | None:
    user = message.from_user
    if user is None:
        await send_message(
            message,
            "❌ Lệnh này chỉ dùng bằng tài khoản người dùng.",
        )
        return None

    document = await get_user(user.id, raw_name(user))
    balance = int(document.get("coins", 0))
    try:
        bet = _parse_bet(token, balance)
    except ValueError as error:
        await send_message(message, f"❌ {error}")
        return None

    reserved = await take_coins(user.id, bet, raw_name(user))
    if not reserved:
        current = await get_coins(user.id)
        await send_message(
            message,
            "❌ Số dư đã thay đổi hoặc không đủ xu. "
            f"Hiện có <b>{format_coins(current)}</b>.",
        )
        return None
    return bet


async def _record_casino(
    user_id: int,
    *,
    won: bool,
    bet: int,
    net: int,
    game: str,
) -> None:
    db = await get_db()
    increments = {
        "casino_games": 1,
        "casino_wagered": int(bet),
        "casino_net": int(net),
        f"{game}_games": 1,
    }
    if won:
        increments["casino_wins"] = 1
        increments[f"{game}_wins"] = 1
    else:
        increments["casino_losses"] = 1
        increments[f"{game}_losses"] = 1

    await db.game_users.update_one(
        {"_id": int(user_id)},
        {"$inc": increments},
    )


@new_task
async def tai_xiu(_, message):
    if not await entertainment_enabled(message.chat.id):
        return

    arguments = (message.text or "").split()
    if len(arguments) != 3:
        await send_message(
            message,
            "🎲 <b>TÀI XỈU</b>\n\n"
            "Cú pháp:\n"
            "<code>/tx tai 5000</code>\n"
            "<code>/tx xiu all</code>\n\n"
            "Có thể dùng <code>/taixiu</code> thay cho "
            "<code>/tx</code>.",
        )
        return

    choice = TAI_XIU_CHOICES.get(arguments[1].lower())
    if choice is None:
        await send_message(
            message,
            "❌ Chỉ được chọn <code>tai</code> hoặc "
            "<code>xiu</code>.",
        )
        return

    user = message.from_user
    if user is None:
        return

    async with _user_lock(user.id):
        bet = await _reserve_bet(message, arguments[2])
        if bet is None:
            return

        dice = [RNG.randint(1, 6) for _ in range(3)]
        total = sum(dice)
        result = "XỈU" if total <= 10 else "TÀI"
        won = choice == result

        if won:
            payout = bet * 2
            balance = await add_coins(user.id, payout, raw_name(user))
            net = bet
        else:
            payout = 0
            balance = await get_coins(user.id)
            net = -bet

        await _record_casino(
            user.id,
            won=won,
            bet=bet,
            net=net,
            game="tai_xiu",
        )

    outcome = "✅ <b>THẮNG</b>" if won else "❌ <b>THUA</b>"
    money_line = (
        f"💵 Nhận: <b>{format_coins(payout)}</b> "
        f"(lãi {format_coins(net)})"
        if won
        else f"💸 Mất: <b>{format_coins(bet)}</b>"
    )
    await send_message(
        message,
        "🎲 <b>TÀI XỈU</b>\n\n"
        f"🎯 Bạn chọn: <b>{choice}</b>\n"
        f"🎰 Xúc xắc: <b>{dice[0]} • {dice[1]} • {dice[2]}</b>\n"
        f"➕ Tổng điểm: <b>{total}</b> → <b>{result}</b>\n"
        f"💰 Mức cược: <b>{format_coins(bet)}</b>\n"
        f"{outcome}\n"
        f"{money_line}\n"
        f"👛 Số dư: <b>{format_coins(balance)}</b>",
    )


@new_task
async def xuc_xac(_, message):
    if not await entertainment_enabled(message.chat.id):
        return

    arguments = (message.text or "").split()
    if len(arguments) != 3:
        await send_message(
            message,
            "🎯 <b>XÚC XẮC ĐOÁN SỐ</b>\n\n"
            "Cú pháp:\n"
            "<code>/xucxac 4 5000</code>\n"
            "<code>/xucxac 6 all</code>\n\n"
            "Chọn một số từ 1 đến 6. Đoán đúng nhận "
            "tổng cộng x6 tiền cược.",
        )
        return

    try:
        choice = int(arguments[1])
    except ValueError:
        choice = 0
    if choice not in range(1, 7):
        await send_message(
            message,
            "❌ Số dự đoán phải từ <code>1</code> đến "
            "<code>6</code>.",
        )
        return

    user = message.from_user
    if user is None:
        return

    async with _user_lock(user.id):
        bet = await _reserve_bet(message, arguments[2])
        if bet is None:
            return

        rolled = RNG.randint(1, 6)
        won = choice == rolled
        if won:
            payout = bet * 6
            balance = await add_coins(user.id, payout, raw_name(user))
            net = bet * 5
        else:
            payout = 0
            balance = await get_coins(user.id)
            net = -bet

        await _record_casino(
            user.id,
            won=won,
            bet=bet,
            net=net,
            game="dice",
        )

    outcome = "✅ <b>ĐOÁN ĐÚNG</b>" if won else "❌ <b>ĐOÁN SAI</b>"
    money_line = (
        f"💵 Nhận: <b>{format_coins(payout)}</b> "
        f"(lãi {format_coins(net)})"
        if won
        else f"💸 Mất: <b>{format_coins(bet)}</b>"
    )
    await send_message(
        message,
        "🎯 <b>XÚC XẮC ĐOÁN SỐ</b>\n\n"
        f"🔮 Bạn chọn: <b>{choice}</b>\n"
        f"🎲 Kết quả: <b>{rolled}</b>\n"
        f"💰 Mức cược: <b>{format_coins(bet)}</b>\n"
        f"{outcome}\n"
        f"{money_line}\n"
        f"👛 Số dư: <b>{format_coins(balance)}</b>",
    )
