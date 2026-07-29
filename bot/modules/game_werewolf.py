from __future__ import annotations

import asyncio
from html import escape
from random import choice, shuffle

from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import LOGGER
from bot.core.config_manager import Config
from bot.core.telegram_manager import TgClient
from bot.helper.ext_utils.bot_utils import new_task
from bot.helper.telegram_helper.message_utils import send_message

from .game_common import (
    add_coins,
    entertainment_enabled,
    format_coins,
    get_coins,
    mention,
    raw_name,
    record_result,
    take_coins,
)

MIN_PLAYERS = 4
MAX_PLAYERS = 15
JOIN_SECONDS = 90
NIGHT_SECONDS = 60
DAY_SECONDS = 90
DEFAULT_BET = 1_000
MIN_BET = 100
MAX_BET = 200_000
STREAK_STEP = 0.10  # +10% mỗi mốc chuỗi thắng
STREAK_CAP = 10  # tối đa +100%

WOLF = "soi"
HUMAN = "dan"

GAMES: dict[int, "Game"] = {}


class Player:
    __slots__ = ("id", "name", "role", "alive")

    def __init__(self, user_id: int, name: str):
        self.id = user_id
        self.name = name
        self.role = HUMAN
        self.alive = True

    @property
    def tag(self) -> str:
        return mention(self.id, escape(self.name))


class Game:
    def __init__(self, chat_id: int, host_id: int, host_name: str, bet: int):
        self.chat_id = chat_id
        self.host_id = host_id
        self.host_name = host_name
        self.bet = bet
        self.players: dict[int, Player] = {}
        self.votes: dict[int, int] = {}
        self.phase = "join"
        self.day = 0
        self.remaining = JOIN_SECONDS
        self.event = asyncio.Event()
        self.message = None
        self.task = None
        self.settled = False

    def alive_players(self) -> list[Player]:
        return [player for player in self.players.values() if player.alive]

    def alive_wolves(self) -> list[Player]:
        return [player for player in self.alive_players() if player.role == WOLF]

    def alive_humans(self) -> list[Player]:
        return [player for player in self.alive_players() if player.role != WOLF]


def _role_label(role: str) -> str:
    return "🐺 Sói" if role == WOLF else "👤 Dân làng"


async def _send(chat_id: int, text: str, keyboard=None):
    try:
        return await TgClient.bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as error:
        LOGGER.error(f"masoi: gửi tin nhắn lỗi ({error})")
        return None


async def _edit(message, text: str, keyboard=None) -> None:
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
    except Exception:
        pass


async def _wait(game: Game, seconds: int) -> bool:
    game.event = asyncio.Event()
    try:
        await asyncio.wait_for(game.event.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


def _lobby_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Tham gia", callback_data="ws join"),
                InlineKeyboardButton("🚪 Rời ván", callback_data="ws leave"),
            ],
            [
                InlineKeyboardButton("▶️ Bắt đầu", callback_data="ws start"),
                InlineKeyboardButton("❌ Hủy ván", callback_data="ws cancel"),
            ],
        ]
    )


def _target_keyboard(game: Game, action: str, allow_skip: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for player in game.alive_players():
        row.append(
            InlineKeyboardButton(player.name[:14], callback_data=f"ws {action} {player.id}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if allow_skip:
        rows.append([InlineKeyboardButton("🤐 Phiếu trắng", callback_data=f"ws {action} 0")])
    rows.append([InlineKeyboardButton("🎭 Xem vai trò của tôi", callback_data="ws role")])
    return InlineKeyboardMarkup(rows)


def _lobby_text(game: Game, remaining: int) -> str:
    roster = "\n".join(
        f"{index}. {player.tag}"
        for index, player in enumerate(game.players.values(), start=1)
    ) or "<i>Chưa có ai.</i>"
    return (
        "🐺 <b>MA SÓI</b> 🐺\n\n"
        f"Chủ ván: {mention(game.host_id, escape(game.host_name))}\n"
        f"💰 Cược: <b>{format_coins(game.bet)}</b> mỗi người\n"
        f"👥 Người chơi ({len(game.players)}/{MAX_PLAYERS}):\n{roster}\n\n"
        f"⏳ Còn <b>{remaining}</b> giây để tham gia "
        f"(cần tối thiểu {MIN_PLAYERS} người)."
    )


async def _refresh_lobby(game: Game, remaining: int | None = None) -> None:
    if game.phase != "join":
        return
    if remaining is not None:
        game.remaining = remaining
    await _edit(game.message, _lobby_text(game, game.remaining), _lobby_keyboard())


async def _refund_all(game: Game) -> None:
    if game.settled or game.bet <= 0:
        return
    for player in game.players.values():
        try:
            await add_coins(player.id, game.bet, player.name)
        except Exception as error:
            LOGGER.error(f"masoi: hoàn tiền lỗi cho {player.id} ({error})")


def _tally(votes: dict[int, int]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for target in votes.values():
        counts[target] = counts.get(target, 0) + 1
    return counts


def _pick(counts: dict[int, int], force: bool) -> int | None:
    if not counts:
        return None
    best = max(counts.values())
    leaders = [target for target, count in counts.items() if count == best]
    if 0 in leaders:
        return None
    if len(leaders) > 1:
        return choice(leaders) if force else None
    return leaders[0]


async def _join_phase(game: Game) -> None:
    game.remaining = JOIN_SECONDS
    game.message = await _send(
        game.chat_id, _lobby_text(game, game.remaining), _lobby_keyboard()
    )
    while game.remaining > 0:
        step = min(30, game.remaining)
        if await _wait(game, step):
            return
        game.remaining -= step
        if game.phase != "join":
            return
        if game.remaining > 0:
            await _refresh_lobby(game)


async def _assign_roles(game: Game) -> None:
    identifiers = list(game.players)
    shuffle(identifiers)
    wolf_count = max(1, len(identifiers) // 4)
    for index, user_id in enumerate(identifiers):
        game.players[user_id].role = WOLF if index < wolf_count else HUMAN

    await _edit(game.message, _lobby_text(game, 0), None)
    text = (
        "🎬 <b>VÁN ĐẤU BẮT ĐẦU!</b>\n\n"
        f"👥 {len(game.players)} người chơi — trong đó có <b>{wolf_count} con sói</b>.\n"
        f"💰 Tổng tiền cược: <b>{format_coins(game.bet * len(game.players))}</b>\n\n"
        "Bấm nút bên dưới để xem vai trò của riêng bạn (chỉ mình bạn thấy)."
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎭 Xem vai trò của tôi", callback_data="ws role")]]
    )
    await _send(game.chat_id, text, keyboard)
    await asyncio.sleep(8)


async def _night_phase(game: Game) -> None:
    game.day += 1
    game.phase = "night"
    game.votes = {}
    wolves = len(game.alive_wolves())
    text = (
        f"🌙 <b>ĐÊM {game.day}</b>\n\n"
        f"Cả làng đi ngủ. <b>{wolves}</b> con sói đang chọn con mồi...\n"
        "Chỉ sói còn sống mới bấm được nút bên dưới, "
        "người khác bấm sẽ không có tác dụng.\n\n"
        f"⏳ {NIGHT_SECONDS} giây."
    )
    game.message = await _send(game.chat_id, text, _target_keyboard(game, "kill"))
    await _wait(game, NIGHT_SECONDS)

    game.phase = "resolve"
    victim_id = _pick(_tally(game.votes), force=True)
    if victim_id:
        victim = game.players[victim_id]
        victim.alive = False
        result = (
            f"🌄 <b>Trời sáng ngày {game.day}.</b>\n\n"
            f"☠️ {victim.tag} đã bị sói cắn chết.\n"
            f"Vai trò: <b>{_role_label(victim.role)}</b>"
        )
    else:
        result = (
            f"🌄 <b>Trời sáng ngày {game.day}.</b>\n\n"
            "😌 Đêm nay bầy sói không thống nhất được — không ai thiệt mạng."
        )
    await _send(game.chat_id, result)


async def _day_phase(game: Game) -> None:
    game.phase = "day"
    game.votes = {}
    alive = game.alive_players()
    text = (
        f"☀️ <b>NGÀY {game.day} — HỌP LÀNG</b>\n\n"
        f"Còn <b>{len(alive)}</b> người sống. Hãy tranh luận rồi bỏ phiếu treo cổ.\n"
        "Người bị nhiều phiếu nhất sẽ bị treo cổ. Hòa phiếu thì không ai chết.\n\n"
        f"⏳ {DAY_SECONDS} giây."
    )
    game.message = await _send(game.chat_id, text, _target_keyboard(game, "vote", True))
    await _wait(game, DAY_SECONDS)

    game.phase = "resolve"
    counts = _tally(game.votes)
    lines = []
    for target, count in sorted(counts.items(), key=lambda item: -item[1]):
        label = "Phiếu trắng" if target == 0 else escape(game.players[target].name)
        lines.append(f"• {label}: <b>{count}</b> phiếu")
    board = "\n".join(lines) if lines else "<i>Không ai bỏ phiếu.</i>"

    hanged_id = _pick(counts, force=False)
    if hanged_id:
        hanged = game.players[hanged_id]
        hanged.alive = False
        verdict = (
            f"⚖️ {hanged.tag} bị dân làng treo cổ.\n"
            f"Vai trò: <b>{_role_label(hanged.role)}</b>"
        )
    else:
        verdict = "🤝 Không đủ phiếu thống nhất — hôm nay không ai bị treo cổ."

    await _send(game.chat_id, f"📊 <b>KẾT QUẢ BỎ PHIẾU</b>\n\n{board}\n\n{verdict}")


async def _settle(game: Game, winning_side: str) -> None:
    game.settled = True
    game.phase = "over"

    winners = [player for player in game.players.values() if player.role == winning_side]
    losers = [player for player in game.players.values() if player.role != winning_side]
    pot = game.bet * len(losers)
    share = pot // len(winners) if winners else 0

    if winning_side == WOLF:
        header = "🐺 <b>BẦY SÓI CHIẾN THẮNG!</b>"
    else:
        header = "👥 <b>DÂN LÀNG CHIẾN THẮNG!</b>"

    lines = [header, ""]
    lines.append("<b>Danh sách vai trò</b>")
    for player in game.players.values():
        status = "" if player.alive else " (đã chết)"
        lines.append(f"• {player.tag} — {_role_label(player.role)}{status}")

    lines.append("")
    lines.append(f"💰 Tiền cược phe thua: <b>{format_coins(pot)}</b>")
    lines.append("")
    lines.append("<b>Phần thưởng</b>")

    for player in winners:
        try:
            streak = await record_result(player.id, True)
        except Exception as error:
            LOGGER.error(f"masoi: ghi kết quả lỗi ({error})")
            streak = 1
        multiplier = 1 + STREAK_STEP * min(max(streak - 1, 0), STREAK_CAP)
        gain = int(share * multiplier)
        await add_coins(player.id, game.bet + gain, player.name)
        bonus_note = f" 🔥 chuỗi {streak} (x{multiplier:.1f})" if streak > 1 else ""
        lines.append(f"✅ {player.tag}: +{format_coins(gain)}{bonus_note}")

    for player in losers:
        try:
            await record_result(player.id, False)
        except Exception as error:
            LOGGER.error(f"masoi: ghi kết quả lỗi ({error})")
        lines.append(f"❌ {player.tag}: -{format_coins(game.bet)}")

    await _send(game.chat_id, "\n".join(lines))


async def _check_end(game: Game) -> bool:
    wolves = game.alive_wolves()
    humans = game.alive_humans()
    if not wolves:
        await _settle(game, HUMAN)
        return True
    if len(wolves) >= len(humans):
        await _settle(game, WOLF)
        return True
    return False


async def _run_game(game: Game) -> None:
    try:
        await _join_phase(game)

        if len(game.players) < MIN_PLAYERS:
            await _refund_all(game)
            await _edit(game.message, _lobby_text(game, 0), None)
            await _send(
                game.chat_id,
                f"❌ Không đủ người chơi (cần tối thiểu {MIN_PLAYERS}). "
                "Toàn bộ tiền cược đã được hoàn lại.",
            )
            return

        await _assign_roles(game)

        while True:
            await _night_phase(game)
            if await _check_end(game):
                return
            await asyncio.sleep(3)
            await _day_phase(game)
            if await _check_end(game):
                return
            await asyncio.sleep(3)
    except asyncio.CancelledError:
        await _refund_all(game)
        await _send(game.chat_id, "🛑 Ván ma sói đã bị hủy. Tiền cược được hoàn lại.")
        raise
    except Exception as error:
        LOGGER.error(f"masoi: ván lỗi ({error})")
        await _refund_all(game)
        await _send(game.chat_id, "⚠️ Ván gặp lỗi nên bị hủy. Tiền cược đã được hoàn lại.")
    finally:
        GAMES.pop(game.chat_id, None)


@new_task
async def werewolf_command(_, message):
    chat_id = message.chat.id
    if not await entertainment_enabled(chat_id):
        return
    user = message.from_user
    if user is None:
        return
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await send_message(message, "🐺 Ma sói chỉ chơi được trong nhóm.")
        return

    arguments = message.text.split()
    option = arguments[1].lower() if len(arguments) > 1 else ""

    if option in ("huy", "hủy", "cancel", "stop"):
        game = GAMES.get(chat_id)
        if game is None:
            await send_message(message, "Không có ván ma sói nào đang chạy.")
            return
        owner = getattr(Config, "OWNER_ID", 0)
        if user.id not in (game.host_id, owner):
            await send_message(message, "Chỉ chủ ván mới hủy được.")
            return
        if game.task:
            game.task.cancel()
        return

    if chat_id in GAMES:
        await send_message(message, "Nhóm này đang có một ván ma sói rồi.")
        return

    bet = DEFAULT_BET
    if option.isdigit():
        bet = max(MIN_BET, min(MAX_BET, int(option)))

    name = raw_name(user)
    if not await take_coins(user.id, bet, name):
        balance = await get_coins(user.id)
        await send_message(
            message,
            f"💸 Bạn cần {format_coins(bet)} để mở ván nhưng chỉ có {format_coins(balance)}.\n"
            "Chơi <code>/duavit</code> để kiếm thêm nhé.",
        )
        return

    game = Game(chat_id, user.id, name, bet)
    game.players[user.id] = Player(user.id, name)
    GAMES[chat_id] = game
    game.task = asyncio.create_task(_run_game(game))


@new_task
async def werewolf_callback(_, query):
    parts = (query.data or "").split()
    if len(parts) < 2:
        await query.answer()
        return

    action = parts[1]
    value = parts[2] if len(parts) > 2 else ""
    user = query.from_user
    game = GAMES.get(query.message.chat.id)

    if game is None:
        await query.answer("Ván này đã kết thúc rồi.", show_alert=True)
        return

    if action == "join":
        if game.phase != "join":
            await query.answer("Ván đã bắt đầu, không vào được nữa.", show_alert=True)
            return
        if user.id in game.players:
            await query.answer("Bạn đã ở trong ván rồi.", show_alert=True)
            return
        if len(game.players) >= MAX_PLAYERS:
            await query.answer("Ván đã đủ người.", show_alert=True)
            return
        name = raw_name(user)
        if not await take_coins(user.id, game.bet, name):
            balance = await get_coins(user.id)
            await query.answer(
                f"Không đủ xu! Cần {game.bet:,} nhưng bạn chỉ có {balance:,}.",
                show_alert=True,
            )
            return
        game.players[user.id] = Player(user.id, name)
        await query.answer(f"Đã vào ván, trừ {game.bet:,} xu tiền cược.")
        await _refresh_lobby(game)
        if len(game.players) >= MAX_PLAYERS:
            game.event.set()
        return

    if action == "leave":
        if game.phase != "join":
            await query.answer("Ván đã bắt đầu, không rời được.", show_alert=True)
            return
        if user.id not in game.players:
            await query.answer("Bạn không ở trong ván.", show_alert=True)
            return
        if user.id == game.host_id:
            await query.answer("Chủ ván không rời được, hãy dùng nút Hủy ván.", show_alert=True)
            return
        game.players.pop(user.id, None)
        await add_coins(user.id, game.bet)
        await query.answer("Đã rời ván và hoàn tiền cược.")
        await _refresh_lobby(game)
        return

    if action == "start":
        if game.phase != "join":
            await query.answer("Ván đã bắt đầu.", show_alert=True)
            return
        if user.id != game.host_id:
            await query.answer("Chỉ chủ ván mới bắt đầu được.", show_alert=True)
            return
        if len(game.players) < MIN_PLAYERS:
            await query.answer(f"Cần tối thiểu {MIN_PLAYERS} người.", show_alert=True)
            return
        await query.answer("Bắt đầu!")
        game.event.set()
        return

    if action == "cancel":
        if user.id != game.host_id and user.id != getattr(Config, "OWNER_ID", 0):
            await query.answer("Chỉ chủ ván mới hủy được.", show_alert=True)
            return
        await query.answer("Đã hủy ván.")
        if game.task:
            game.task.cancel()
        return

    if action == "role":
        player = game.players.get(user.id)
        if player is None:
            await query.answer("Bạn không ở trong ván này.", show_alert=True)
            return
        if game.phase == "join":
            await query.answer("Ván chưa bắt đầu, chưa có vai trò.", show_alert=True)
            return
        if player.role == WOLF:
            mates = [
                other.name
                for other in game.players.values()
                if other.role == WOLF and other.id != user.id
            ]
            extra = f"Đồng bọn: {', '.join(mates)}" if mates else "Bạn là con sói duy nhất."
            text = f"🐺 Bạn là SÓI.\n{extra}"
        else:
            text = "👤 Bạn là DÂN LÀNG.\nHãy tìm ra bầy sói trước khi quá muộn!"
        if not player.alive:
            text += "\n\n💀 Bạn đã chết, chỉ được xem."
        await query.answer(text[:200], show_alert=True)
        return

    if action == "kill":
        if game.phase != "night":
            await query.answer("Chưa tới lượt sói.", show_alert=True)
            return
        player = game.players.get(user.id)
        if player is None or not player.alive or player.role != WOLF:
            await query.answer("Bạn không phải sói còn sống.", show_alert=True)
            return
        target = game.players.get(int(value)) if value.isdigit() else None
        if target is None or not target.alive:
            await query.answer("Mục tiêu không hợp lệ.", show_alert=True)
            return
        if target.role == WOLF:
            await query.answer("Sói không cắn đồng bọn.", show_alert=True)
            return
        game.votes[user.id] = target.id
        wolves = len(game.alive_wolves())
        await query.answer(f"Bạn chọn cắn {target.name}. ({len(game.votes)}/{wolves} sói)")
        if len(game.votes) >= wolves:
            game.event.set()
        return

    if action == "vote":
        if game.phase != "day":
            await query.answer("Chưa tới lượt bỏ phiếu.", show_alert=True)
            return
        player = game.players.get(user.id)
        if player is None or not player.alive:
            await query.answer("Chỉ người còn sống mới được bỏ phiếu.", show_alert=True)
            return
        if value == "0":
            game.votes[user.id] = 0
            label = "phiếu trắng"
        else:
            target = game.players.get(int(value)) if value.isdigit() else None
            if target is None or not target.alive:
                await query.answer("Mục tiêu không hợp lệ.", show_alert=True)
                return
            game.votes[user.id] = target.id
            label = target.name
        total = len(game.alive_players())
        await query.answer(f"Bạn đã bỏ phiếu cho {label}. ({len(game.votes)}/{total})")
        if len(game.votes) >= total:
            game.event.set()
        return

    await query.answer()
