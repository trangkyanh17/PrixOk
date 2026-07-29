from __future__ import annotations

from datetime import datetime
from html import escape

from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.telegram_helper.message_utils import send_message

from .game_common import (
    COIN_NAME,
    START_COINS,
    coin_rank,
    entertainment_enabled,
    format_coins,
    get_user,
    mention,
    raw_name,
    set_coins,
    set_entertainment,
    top_users,
    top_winrate,
)

MEDALS = ("🥇", "🥈", "🥉")
MIN_RANKED_GAMES = 5


def _target_user(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    return message.from_user


@new_task
async def werewolf_top(_, message):
    if not await entertainment_enabled(message.chat.id):
        return

    users = await top_winrate(10, MIN_RANKED_GAMES)
    note = f"Chỉ tính người đã chơi từ {MIN_RANKED_GAMES} ván trở lên."
    if not users:
        users = await top_winrate(10, 1)
        note = "Chưa ai đủ số ván tối thiểu, đang xếp theo mọi người chơi."
    if not users:
        await send_message(message, "Chưa có ai chơi ma sói cả.")
        return

    lines = ["🐺 <b>BẢNG XẾP HẠNG MA SÓI</b>\n"]
    for index, document in enumerate(users):
        badge = MEDALS[index] if index < 3 else f"<code>{index + 1}.</code>"
        name = escape(document.get("name") or "") or "Ẩn danh"
        wins = int(document.get("wins", 0))
        losses = int(document.get("losses", 0))
        rate = float(document.get("rate", 0)) * 100
        streak = int(document.get("best_streak", 0))
        lines.append(
            f"{badge} {mention(document['_id'], name)} — <b>{rate:.0f}%</b>"
            f"  ({wins}T/{losses}B, chuỗi tốt nhất {streak})"
        )
    lines.append(f"\n<i>{note}</i>")
    await send_message(message, "\n".join(lines))


@new_task
async def profile(_, message):
    if not await entertainment_enabled(message.chat.id):
        return

    target = _target_user(message)
    if target is None:
        return

    document = await get_user(target.id, raw_name(target))
    name = escape(document.get("name") or "") or escape(raw_name(target))

    coins = int(document.get("coins", 0))
    wins = int(document.get("wins", 0))
    losses = int(document.get("losses", 0))
    games = wins + losses
    rate = f"{wins * 100 / games:.0f}%" if games else "—"
    money_rank = await coin_rank(target.id)

    rank_note = f"chưa đủ {MIN_RANKED_GAMES} ván để xếp hạng"
    if games >= MIN_RANKED_GAMES:
        ranked = await top_winrate(100, MIN_RANKED_GAMES)
        position = next(
            (
                index + 1
                for index, entry in enumerate(ranked)
                if entry["_id"] == target.id
            ),
            None,
        )
        rank_note = f"hạng <b>#{position}</b>" if position else "ngoài top 100"

    duck_races = int(document.get("duck_races", 0))
    duck_best = int(document.get("duck_best", 0))
    duck_earned = int(document.get("duck_earned", 0))

    created = document.get("created_at")
    joined = (
        datetime.fromtimestamp(float(created)).strftime("%d/%m/%Y")
        if created
        else "không rõ"
    )

    text = (
        f"🧾 <b>HỒ SƠ NGƯỜI CHƠI</b>\n"
        f"👤 {mention(target.id, name)}\n"
        f"<code>ID: {target.id}</code>\n\n"
        f"👛 <b>Ví</b>\n"
        f"• Số dư: <b>{format_coins(coins)}</b> (hạng #{money_rank})\n\n"
        f"🐺 <b>Ma sói</b>\n"
        f"• Số ván: <b>{games}</b> ({wins} thắng / {losses} thua)\n"
        f"• Tỉ lệ thắng: <b>{rate}</b> — {rank_note}\n"
        f"• Chuỗi thắng hiện tại: <b>{document.get('streak', 0)}</b>\n"
        f"• Chuỗi thắng cao nhất: <b>{document.get('best_streak', 0)}</b>\n\n"
        f"🦆 <b>Đua vịt</b>\n"
        f"• Số lượt đua: <b>{duck_races}</b>\n"
        f"• Đi xa nhất: <b>{duck_best}m</b>\n"
        f"• Tổng thưởng: <b>{format_coins(duck_earned)}</b>\n\n"
        f"📅 Tham gia: {joined}"
    )
    await send_message(message, text)


@new_task
async def wallet(_, message):
    if not await entertainment_enabled(message.chat.id):
        return

    target = _target_user(message)
    if target is None:
        return

    document = await get_user(target.id, raw_name(target))
    name = escape(document.get("name") or "") or escape(raw_name(target))
    wins = int(document.get("wins", 0))
    losses = int(document.get("losses", 0))
    total = wins + losses
    rate = f"{wins * 100 / total:.0f}%" if total else "—"

    text = (
        f"👛 <b>Ví của {mention(target.id, name)}</b>\n\n"
        f"💰 Số dư: <b>{format_coins(document.get('coins', 0))}</b>\n"
        f"🏆 Ma sói: {wins} thắng / {losses} thua (tỉ lệ {rate})\n"
        f"🔥 Chuỗi thắng hiện tại: <b>{document.get('streak', 0)}</b>\n"
        f"⭐ Chuỗi thắng cao nhất: {document.get('best_streak', 0)}"
    )
    await send_message(message, text)


@new_task
async def game_top(_, message):
    if not await entertainment_enabled(message.chat.id):
        return

    users = await top_users(10)
    if not users:
        await send_message(message, "Chưa có ai chơi cả.")
        return

    lines = [f"🏅 <b>BẢNG XẾP HẠNG {COIN_NAME.upper()}</b>\n"]
    for index, document in enumerate(users):
        badge = MEDALS[index] if index < 3 else f"<code>{index + 1}.</code>"
        name = escape(document.get("name") or "") or "Ẩn danh"
        lines.append(
            f"{badge} {mention(document['_id'], name)} — "
            f"<b>{format_coins(document.get('coins', 0))}</b>"
        )
    await send_message(message, "\n".join(lines))


@new_task
async def admin_set_coins(_, message):
    arguments = message.text.split()
    target_id = None
    amount = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        if len(arguments) >= 2:
            amount = arguments[1]
    elif len(arguments) >= 3:
        target_id = arguments[1]
        amount = arguments[2]

    try:
        target_id = int(target_id)
        amount = int(amount)
    except (TypeError, ValueError):
        await send_message(
            message,
            "Cú pháp: <code>/setxu &lt;user_id&gt; &lt;số xu&gt;</code>\n"
            "Hoặc reply tin nhắn của người đó: <code>/setxu &lt;số xu&gt;</code>",
        )
        return

    balance = await set_coins(target_id, amount)
    await send_message(
        message,
        f"✅ Đã đặt số dư của <code>{target_id}</code> thành <b>{format_coins(balance)}</b>.",
    )


@new_task
async def toggle_games(_, message):
    arguments = message.text.split()
    if len(arguments) < 2 or arguments[1].lower() not in ("on", "off"):
        await send_message(message, "Cú pháp: <code>/game on</code> hoặc <code>/game off</code>")
        return

    enabled = arguments[1].lower() == "on"
    await set_entertainment(message.chat.id, enabled)
    await send_message(
        message,
        "✅ Đã bật khu giải trí trong nhóm này."
        if enabled
        else "🚫 Đã tắt khu giải trí trong nhóm này.",
    )


@new_task
async def game_help(_, message):
    if not await entertainment_enabled(message.chat.id):
        return

    text = f"""🎮 <b>KHU GIẢI TRÍ</b>

<b>💰 Tiền tệ</b>
Đơn vị: <b>{COIN_NAME}</b>. Người mới nhận {format_coins(START_COINS)}.
<code>/vi</code> — xem ví nhanh (reply để xem ví người khác)
<code>/hoso</code> — hồ sơ đầy đủ: ví, ma sói, đua vịt
<code>/bangxu</code> — bảng xếp hạng giàu nhất
<code>/topmasoi</code> — bảng xếp hạng tỉ lệ thắng ma sói

<b>🦆 Đua vịt — miễn phí</b>
<code>/duavit</code> — thả thuyền vịt ra đua.
Thuyền đi càng xa thì thưởng càng nhiều, đi ngắn thì thưởng ít.
Về nhất/nhì/ba có thêm tiền thưởng. Nghỉ 1 phút giữa 2 lượt.

<b>🐺 Ma sói — có cược</b>
<code>/masoi</code> — mở ván với mức cược mặc định
<code>/masoi 5000</code> — mở ván với mức cược tự chọn
<code>/masoi huy</code> — hủy ván đang mở (chủ ván hoặc admin)

Chỉ có 2 phe: <b>Sói</b> và <b>Dân</b>.
Đêm sói cắn, ngày cả làng bỏ phiếu treo cổ.
Sói thắng khi số sói ≥ số dân; dân thắng khi diệt hết sói.
Mỗi người đặt cược khi vào ván. Phe thua mất cược, phe thắng chia toàn bộ tiền cược của phe thua.
🔥 <b>Chuỗi thắng càng cao, tiền nhận càng nhiều</b> (tối đa +100%)."""
    await send_message(message, text)
