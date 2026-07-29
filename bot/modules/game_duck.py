from __future__ import annotations

import asyncio
from html import escape
from random import choice, randint, random
from time import time

from bot import LOGGER
from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.telegram_helper.message_utils import edit_message, send_message

from .game_common import (
    add_coins,
    entertainment_enabled,
    format_coins,
    get_user,
    raw_name,
    record_duck,
    touch_duck,
)

COOLDOWN = 60  # giây giữa 2 lượt đua
ROUNDS = 6
TICK = 2  # giây mỗi vòng
TRACK_WIDTH = 20
FULL_DISTANCE = 150  # mốc để vẽ thanh tiến trình
COIN_PER_METER = 10
RANK_BONUS = (600, 300, 150, 0)

RIVALS = ("Vịt Bơ", "Vịt Cồ", "Vịt Xiêm")

GOOD_EVENTS = (
    ("🌬️ Gió xuôi thổi mạnh", 9),
    ("🌊 Bắt được dòng nước xiết", 7),
    ("💨 Vịt sung sức đạp nước", 6),
)
BAD_EVENTS = (
    ("🪨 Đâm phải đá ngầm", -7),
    ("🪸 Vướng lưới bèo", -6),
    ("🌀 Bị xoáy nước cuốn ngược", -8),
)
STOP_EVENTS = (
    "🐌 Vịt mải mê ăn ốc",
    "😴 Vịt ngủ quên trên thuyền",
    "🍃 Thuyền mắc cạn bãi bùn",
)


def _bar(distance: int) -> str:
    filled = int(min(distance, FULL_DISTANCE) / FULL_DISTANCE * TRACK_WIDTH)
    filled = max(0, min(TRACK_WIDTH, filled))
    return "█" * filled + "░" * (TRACK_WIDTH - filled)


def _board(racers: list[dict], round_index: int, note: str) -> str:
    header = (
        "🏁 <b>ĐUA THUYỀN VỊT</b> 🏁\n"
        f"Vòng {round_index}/{ROUNDS}\n"
    )
    lines = []
    for racer in sorted(racers, key=lambda item: -item["distance"]):
        label = escape(racer["label"][:10].ljust(10))
        lines.append(f"{racer['icon']} {label}|{_bar(racer['distance'])}| {racer['distance']:>3}m")
    body = "<pre>" + "\n".join(lines) + "</pre>"
    return f"{header}\n{body}\n{note}"


async def _safe_edit(message, text) -> None:
    try:
        await edit_message(message, text)
    except Exception as error:
        LOGGER.warning(f"duck: không sửa được tin nhắn ({error})")


@new_task
async def duck_race(_, message):
    chat_id = message.chat.id
    if not await entertainment_enabled(chat_id):
        return

    user = message.from_user
    if user is None:
        return

    name = raw_name(user)
    document = await get_user(user.id, name)

    waiting = COOLDOWN - (time() - float(document.get("last_duck", 0) or 0))
    if waiting > 0:
        seconds = int(waiting) + 1
        minutes, seconds = divmod(seconds, 60)
        left = f"{minutes} phút {seconds} giây" if minutes else f"{seconds} giây"
        await send_message(
            message,
            f"🦆 Vịt của bạn còn đang nghỉ mệt.\nQuay lại sau <b>{left}</b> nhé.",
        )
        return

    await touch_duck(user.id)

    racers = [{"label": name, "icon": "🦆", "distance": 0, "is_player": True}]
    for rival in RIVALS:
        racers.append({"label": rival, "icon": "🐤", "distance": 0, "is_player": False})

    player = racers[0]
    note = "🚩 Chuẩn bị xuất phát..."
    sent = await send_message(message, _board(racers, 0, note))
    if isinstance(sent, str) or sent is None:
        return

    for round_index in range(1, ROUNDS + 1):
        await asyncio.sleep(TICK)

        for racer in racers:
            if racer["is_player"]:
                continue
            racer["distance"] += randint(6, 24)

        step = randint(8, 24)
        roll = random()
        if roll < 0.10:
            note = f"{choice(STOP_EVENTS)} — vòng này đứng yên!"
            step = 0
        elif roll < 0.28:
            event, modifier = choice(GOOD_EVENTS)
            step += modifier
            note = f"{event} (+{modifier}m)"
        elif roll < 0.46:
            event, modifier = choice(BAD_EVENTS)
            step = max(0, step + modifier)
            note = f"{event} ({modifier}m)"
        else:
            note = "🚣 Vịt đang chèo đều tay..."

        player["distance"] += step
        await _safe_edit(sent, _board(racers, round_index, note))

    order = sorted(racers, key=lambda item: -item["distance"])
    rank = order.index(player) + 1
    distance = player["distance"]

    base = distance * COIN_PER_METER
    bonus = RANK_BONUS[min(rank, len(RANK_BONUS)) - 1]
    reward = base + bonus
    balance = await add_coins(user.id, reward, name)
    await record_duck(user.id, distance, reward)

    if distance >= 120:
        comment = "🔥 Cú về đích cực đỉnh!"
    elif distance >= 80:
        comment = "👍 Một chặng đua đàng hoàng."
    elif distance >= 40:
        comment = "😅 Vịt hơi đuối nước."
    else:
        comment = "💀 Thuyền gần như đứng yên..."

    result = (
        _board(racers, ROUNDS, "🏁 Về đích!")
        + "\n\n"
        + f"<b>Kết quả của bạn</b>\n"
        + f"📏 Quãng đường: <b>{distance}m</b> — hạng <b>{rank}/{len(racers)}</b>\n"
        + f"💵 Thưởng quãng đường: {format_coins(base)}\n"
        + (f"🏆 Thưởng thứ hạng: {format_coins(bonus)}\n" if bonus else "")
        + f"💰 Tổng nhận: <b>{format_coins(reward)}</b>\n"
        + f"👛 Số dư: {format_coins(balance)}\n\n"
        + comment
    )
    await _safe_edit(sent, result)
