from __future__ import annotations

from asyncio import CancelledError, Lock, Task, create_task, current_task, sleep
from collections import Counter
from html import escape
from secrets import SystemRandom
from time import time
from typing import Any

from pyrogram.enums import ChatType

from .. import LOGGER
from ..core.telegram_manager import TgClient
from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import send_message
from .game_common import (
    capped_xp_gain,
    ensure_message_user,
    entertainment_guard,
    format_number,
    game_collection,
    minigame_coin_reward,
    require_game_collection,
    require_user,
)


RNG = SystemRandom()
WEREWOLF_MIN_PLAYERS = 5
WEREWOLF_MAX_PLAYERS = 10
WEREWOLF_LOBBY_SECONDS = 90
WEREWOLF_WOLF_PHASE_SECONDS = 30
WEREWOLF_SPECIAL_PHASE_SECONDS = 30
WEREWOLF_DISCUSSION_SECONDS = 45
WEREWOLF_VOTE_SECONDS = 45

ROLE_LABELS = {
    "wolf": "Ma Sói",
    "villager": "Dân Làng",
    "seer": "Tiên Tri",
    "witch": "Phù Thuỷ",
    "guard": "Bảo Vệ",
}
VILLAGE_ROLES = {"villager", "seer", "witch", "guard"}

ROOMS: dict[int, dict[str, Any]] = {}
PLAYER_ROOMS: dict[int, int] = {}
ROOM_LOCKS: dict[int, Lock] = {}


def _room_lock(chat_id: int) -> Lock:
    return ROOM_LOCKS.setdefault(int(chat_id), Lock())


def _display_user(user) -> str:
    full_name = " ".join(
        part
        for part in (
            getattr(user, "first_name", None),
            getattr(user, "last_name", None),
        )
        if part
    ).strip()
    return full_name or getattr(user, "username", None) or str(user.id)


def _player_record(user) -> dict[str, Any]:
    return {
        "id": int(user.id),
        "name": _display_user(user),
        "username": str(getattr(user, "username", None) or ""),
    }


def _player_label(player: dict[str, Any]) -> str:
    username = str(player.get("username") or "")
    suffix = f" (@{escape(username)})" if username else ""
    return f"<b>{escape(str(player.get('name') or player['id']))}</b>{suffix} · <code>{player['id']}</code>"


def _target_token(player: dict[str, Any]) -> str:
    username = str(player.get("username") or "")
    return f"@{username}" if username else str(player["id"])


def _resolve_target(
    room: dict[str, Any],
    raw: str,
    *,
    alive_only: bool = True,
) -> int | None:
    token = raw.strip()
    if not token:
        return None
    players: dict[int, dict[str, Any]] = room["players"]
    candidates = set(room.get("alive", set())) if alive_only else set(players)
    if token.lstrip("-").isdigit():
        user_id = int(token)
        return user_id if user_id in candidates else None
    username = token.lstrip("@").lower()
    for user_id in candidates:
        if str(players[user_id].get("username") or "").lower() == username:
            return int(user_id)
    return None


async def _dm(user_id: int, text: str) -> bool:
    try:
        await TgClient.bot.send_message(int(user_id), text)
        return True
    except Exception:
        return False


async def _group(room: dict[str, Any], text: str) -> None:
    await TgClient.bot.send_message(int(room["chat_id"]), text)


def _phase_label(phase: str) -> str:
    return {
        "lobby": "Đang chờ người chơi",
        "night_wolves": "Ban đêm — Sói chọn nạn nhân",
        "night_special": "Ban đêm — vai trò đặc biệt hành động",
        "day_discussion": "Ban ngày — thảo luận",
        "day_vote": "Ban ngày — bỏ phiếu",
        "ended": "Đã kết thúc",
    }.get(phase, phase)


async def _cancel_task(task: Task | None) -> None:
    if task is None or task.done() or task is current_task():
        return
    task.cancel()
    try:
        await task
    except CancelledError:
        pass


async def _cleanup_room(room: dict[str, Any]) -> None:
    chat_id = int(room["chat_id"])
    for user_id in list(room.get("players", {})):
        if PLAYER_ROOMS.get(int(user_id)) == chat_id:
            PLAYER_ROOMS.pop(int(user_id), None)
    if ROOMS.get(chat_id) is room:
        ROOMS.pop(chat_id, None)
    ROOM_LOCKS.pop(chat_id, None)


async def _cancel_room(room: dict[str, Any], reason: str) -> None:
    room["phase"] = "ended"
    await _cancel_task(room.get("lobby_task"))
    await _cancel_task(room.get("game_task"))
    await _group(room, f"🚫 <b>Phòng Ma Sói đã bị huỷ.</b>\n{reason}")
    await _cleanup_room(room)


def _assign_roles(player_ids: list[int]) -> dict[int, str]:
    count = len(player_ids)
    roles = ["wolf"]
    if count == 10:
        roles = ["wolf", "wolf", "seer", "witch", "guard"]
    roles.extend(["villager"] * (count - len(roles)))
    RNG.shuffle(roles)
    shuffled_players = list(player_ids)
    RNG.shuffle(shuffled_players)
    return dict(zip(shuffled_players, roles, strict=True))


async def _send_roles(room: dict[str, Any]) -> bool:
    roles: dict[int, str] = room["roles"]
    players: dict[int, dict[str, Any]] = room["players"]
    wolves = [user_id for user_id, role in roles.items() if role == "wolf"]

    for user_id, role in roles.items():
        common = (
            f"🐺 <b>Ma Sói — phòng {escape(str(room['title']))}</b>\n"
            f"Vai trò của bạn: <b>{ROLE_LABELS[role]}</b>\n"
            "Không tiết lộ tin nhắn này trong nhóm.\n\n"
        )
        if role == "wolf":
            allies = [
                _player_label(players[ally])
                for ally in wolves
                if ally != user_id
            ]
            detail = (
                "Mục tiêu: loại đủ người để số Sói sống bằng số người Phe Dân Làng.\n"
                "Ban đêm dùng <code>/masoi bite ID|@username</code>."
            )
            if allies:
                detail += "\nĐồng minh: " + ", ".join(allies)
        elif role == "seer":
            detail = (
                "Mỗi đêm soi một người bằng "
                "<code>/masoi see ID|@username</code>."
            )
        elif role == "witch":
            detail = (
                "Bạn có 1 bình cứu và 1 bình độc cho cả ván. "
                "Khi bot báo nạn nhân, dùng <code>/masoi save</code> hoặc "
                "<code>/masoi poison ID|@username</code>. Chỉ dùng một bình mỗi đêm."
            )
        elif role == "guard":
            detail = (
                "Mỗi đêm bảo vệ một người bằng "
                "<code>/masoi protect ID|@username</code>. "
                "Không được bảo vệ cùng một người hai đêm liên tiếp."
            )
        else:
            detail = (
                "Mục tiêu: thảo luận và treo cổ toàn bộ Ma Sói. "
                "Ban ngày dùng <code>/masoi vote ID|@username</code>."
            )
        if not await _dm(user_id, common + detail):
            return False
    return True


async def _start_room_locked(room: dict[str, Any]) -> bool:
    if room.get("phase") != "lobby":
        return False
    player_count = len(room["players"])
    if player_count < WEREWOLF_MIN_PLAYERS:
        return False

    await _cancel_task(room.get("lobby_task"))
    room["roles"] = _assign_roles(list(room["players"]))
    room["alive"] = set(room["players"])
    room["phase"] = "starting"
    room["round"] = 0
    room["witch_heal"] = True
    room["witch_poison"] = True
    room["guard_last"] = None

    if not await _send_roles(room):
        await _cancel_room(
            room,
            "Không thể gửi vai trò riêng tư cho một người chơi. "
            "Mọi người phải nhắn <code>/start</code> cho bot trước khi tham gia.",
        )
        return False

    room["game_task"] = create_task(_game_loop(room))
    await _group(
        room,
        f"🐺 <b>Ván Ma Sói bắt đầu với {player_count} người.</b>\n"
        + (
            "Phòng 10 người: 2 Sói, Tiên Tri, Phù Thuỷ, Bảo Vệ và 5 Dân Làng."
            if player_count == 10
            else "Phòng dưới 10 người: 1 Sói, những người còn lại là Dân Làng."
        )
        + "\nVai trò đã được gửi riêng. Bot là quản trò và không tính vào số người chơi.",
    )
    return True


async def _lobby_timer(room: dict[str, Any]) -> None:
    try:
        await sleep(WEREWOLF_LOBBY_SECONDS)
        async with _room_lock(room["chat_id"]):
            if ROOMS.get(room["chat_id"]) is not room or room.get("phase") != "lobby":
                return
            if len(room["players"]) < WEREWOLF_MIN_PLAYERS:
                await _cancel_room(
                    room,
                    f"Hết {WEREWOLF_LOBBY_SECONDS} giây nhưng chỉ có "
                    f"{len(room['players'])}/{WEREWOLF_MIN_PLAYERS} người.",
                )
                return
            await _start_room_locked(room)
    except CancelledError:
        raise
    except Exception:
        LOGGER.exception("Werewolf lobby failed")
        if ROOMS.get(room["chat_id"]) is room:
            await _cancel_room(room, "Phòng gặp lỗi khi chờ người chơi.")


def _choose_wolf_target(room: dict[str, Any]) -> int | None:
    votes = [
        target
        for voter, target in room.get("wolf_votes", {}).items()
        if voter in room["alive"]
        and room["roles"].get(voter) == "wolf"
        and target in room["alive"]
        and room["roles"].get(target) != "wolf"
    ]
    if not votes:
        return None
    counts = Counter(votes)
    highest = max(counts.values())
    leaders = [target for target, value in counts.items() if value == highest]
    return RNG.choice(leaders)


def _winner(room: dict[str, Any]) -> str | None:
    alive = set(room.get("alive", set()))
    wolves = sum(1 for user_id in alive if room["roles"].get(user_id) == "wolf")
    villagers = len(alive) - wolves
    if wolves <= 0:
        return "village"
    if wolves >= villagers:
        return "wolf"
    return None


async def _award_game(room: dict[str, Any], winner: str) -> None:
    collection = game_collection()
    if collection is None:
        await _group(room, "⚠️ MongoDB không sẵn sàng nên không thể phát thưởng Ma Sói.")
        return

    player_count = len(room["players"])
    winners: set[int] = {
        user_id
        for user_id, role in room["roles"].items()
        if (winner == "wolf" and role == "wolf")
        or (winner == "village" and role in VILLAGE_ROLES)
    }
    for user_id in room["players"]:
        user_doc = await collection.find_one({"_id": int(user_id)}) or {"_id": int(user_id)}
        won = user_id in winners
        base_coins = (
            100_000 + player_count * 10_000
            if won
            else 25_000 + player_count * 2_500
        )
        base_xp = 500 + player_count * 50 if won else 150 + player_count * 15
        coin_gain = minigame_coin_reward(user_doc, base_coins)
        xp_gain = capped_xp_gain(user_doc, base_xp)
        inc = {
            "coins": coin_gain,
            "xp": xp_gain,
            "stats.werewolf_games": 1,
            "stats.werewolf_coins": coin_gain,
            "stats.werewolf_xp": xp_gain,
        }
        if won:
            inc["stats.werewolf_wins"] = 1
            inc[
                "stats.werewolf_wolf_wins"
                if winner == "wolf"
                else "stats.werewolf_village_wins"
            ] = 1
        await collection.update_one(
            {"_id": int(user_id)},
            {"$inc": inc, "$set": {"updated_at": time()}},
            upsert=True,
        )
        await _dm(
            user_id,
            f"🏆 <b>Kết quả Ma Sói: {'THẮNG' if won else 'THUA'}</b>\n"
            f"💰 Nhận <b>{format_number(coin_gain)} xu</b>\n"
            f"⭐ Nhận <b>+{format_number(xp_gain)} EXP</b>\n"
            "Bùa x2 tiền và x2 EXP đã được áp dụng nếu còn hiệu lực.",
        )


async def _finish_game(room: dict[str, Any], winner: str) -> None:
    room["phase"] = "ended"
    winner_label = "Phe Ma Sói" if winner == "wolf" else "Phe Dân Làng"
    role_lines = [
        f"{_player_label(room['players'][user_id])}: "
        f"<b>{ROLE_LABELS[room['roles'][user_id]]}</b>"
        for user_id in room["players"]
    ]
    await _group(
        room,
        f"🏁 <b>{winner_label} chiến thắng!</b>\n\n"
        "<b>Vai trò toàn bộ người chơi</b>\n"
        + "\n".join(role_lines),
    )
    await _award_game(room, winner)
    await _cleanup_room(room)


async def _announce_night_prompts(room: dict[str, Any]) -> None:
    for user_id in room["alive"]:
        role = room["roles"].get(user_id)
        if role == "wolf":
            targets = [
                _player_label(room["players"][target])
                for target in room["alive"]
                if room["roles"].get(target) != "wolf"
            ]
            await _dm(
                user_id,
                "🌙 Sói chọn nạn nhân trong 30 giây:\n"
                + "\n".join(targets)
                + "\nDùng <code>/masoi bite ID|@username</code>.",
            )
        elif role == "seer":
            await _dm(
                user_id,
                "🔮 Đêm nay dùng <code>/masoi see ID|@username</code> để soi một người.",
            )
        elif role == "guard":
            await _dm(
                user_id,
                "🛡 Đêm nay dùng <code>/masoi protect ID|@username</code> để bảo vệ một người.",
            )


async def _special_phase_prompt(room: dict[str, Any]) -> None:
    wolf_target = room.get("wolf_target")
    witch_id = next(
        (
            user_id
            for user_id in room["alive"]
            if room["roles"].get(user_id) == "witch"
        ),
        None,
    )
    if witch_id is None:
        return
    if wolf_target is None:
        victim_text = "Đêm nay Sói chưa chọn được nạn nhân."
    else:
        victim_text = "Nạn nhân Sói chọn: " + _player_label(room["players"][wolf_target])
    options = []
    if room.get("witch_heal", False) and wolf_target is not None:
        options.append("<code>/masoi save</code>")
    if room.get("witch_poison", False):
        options.append("<code>/masoi poison ID|@username</code>")
    option_text = " hoặc ".join(options) if options else "Bạn đã hết cả hai bình."
    await _dm(
        witch_id,
        f"🧪 <b>Phù Thuỷ</b>\n{victim_text}\nHành động trong 30 giây: {option_text}\n"
        "Chỉ được sử dụng một bình trong mỗi đêm.",
    )


async def _resolve_night(room: dict[str, Any]) -> list[int]:
    deaths: set[int] = set()
    wolf_target = room.get("wolf_target")
    guard_target = room.get("guard_target")
    witch_action = room.get("witch_action")
    poison_target = room.get("witch_poison_target")

    saved = witch_action == "save" and wolf_target is not None
    if saved:
        room["witch_heal"] = False
    if witch_action == "poison" and poison_target is not None:
        room["witch_poison"] = False

    if wolf_target is not None and not saved and wolf_target != guard_target:
        deaths.add(int(wolf_target))
    if poison_target is not None and poison_target != guard_target:
        deaths.add(int(poison_target))

    deaths &= set(room["alive"])
    room["alive"].difference_update(deaths)
    if guard_target is not None:
        room["guard_last"] = guard_target
    return sorted(deaths)


async def _resolve_vote(room: dict[str, Any]) -> int | None:
    votes = [
        target
        for voter, target in room.get("day_votes", {}).items()
        if voter in room["alive"] and target in room["alive"]
    ]
    if not votes:
        return None
    counts = Counter(votes)
    highest = max(counts.values())
    leaders = [target for target, value in counts.items() if value == highest]
    if len(leaders) != 1:
        return None
    eliminated = leaders[0]
    room["alive"].discard(eliminated)
    return eliminated


async def _game_loop(room: dict[str, Any]) -> None:
    try:
        while ROOMS.get(room["chat_id"]) is room:
            async with _room_lock(room["chat_id"]):
                room["round"] += 1
                room["phase"] = "night_wolves"
                room["wolf_votes"] = {}
                room["wolf_target"] = None
                room["guard_target"] = None
                room["witch_action"] = None
                room["witch_poison_target"] = None
                room["seer_round"] = set()
                room["day_votes"] = {}
                round_number = room["round"]
            await _group(
                room,
                f"🌙 <b>Đêm {round_number}</b> bắt đầu. "
                f"Ma Sói có {WEREWOLF_WOLF_PHASE_SECONDS} giây để chọn nạn nhân.",
            )
            await _announce_night_prompts(room)
            await sleep(WEREWOLF_WOLF_PHASE_SECONDS)

            async with _room_lock(room["chat_id"]):
                if ROOMS.get(room["chat_id"]) is not room:
                    return
                room["wolf_target"] = _choose_wolf_target(room)
                room["phase"] = "night_special"
            await _special_phase_prompt(room)
            await sleep(WEREWOLF_SPECIAL_PHASE_SECONDS)

            async with _room_lock(room["chat_id"]):
                deaths = await _resolve_night(room)
                winner = _winner(room)
            if deaths:
                death_lines = [
                    _player_label(room["players"][user_id])
                    for user_id in deaths
                ]
                await _group(
                    room,
                    "🌅 <b>Trời sáng.</b> Người bị loại trong đêm:\n"
                    + "\n".join(death_lines),
                )
            else:
                await _group(room, "🌅 <b>Trời sáng.</b> Đêm qua không ai bị loại.")
            if winner:
                await _finish_game(room, winner)
                return

            async with _room_lock(room["chat_id"]):
                room["phase"] = "day_discussion"
            await _group(
                room,
                f"☀️ Thảo luận trong <b>{WEREWOLF_DISCUSSION_SECONDS} giây</b> để tìm Ma Sói.",
            )
            await sleep(WEREWOLF_DISCUSSION_SECONDS)

            async with _room_lock(room["chat_id"]):
                room["phase"] = "day_vote"
                room["day_votes"] = {}
            await _group(
                room,
                f"🗳 Bỏ phiếu trong <b>{WEREWOLF_VOTE_SECONDS} giây</b>. "
                "Dùng <code>/masoi vote ID|@username</code> trong nhóm.",
            )
            await sleep(WEREWOLF_VOTE_SECONDS)

            async with _room_lock(room["chat_id"]):
                eliminated = await _resolve_vote(room)
                winner = _winner(room)
            if eliminated is None:
                await _group(room, "⚖️ Phiếu hoà hoặc không đủ phiếu; không ai bị treo cổ.")
            else:
                await _group(
                    room,
                    "🪢 Người bị treo cổ: "
                    + _player_label(room["players"][eliminated])
                    + f"\nVai trò: <b>{ROLE_LABELS[room['roles'][eliminated]]}</b>.",
                )
            if winner:
                await _finish_game(room, winner)
                return
    except CancelledError:
        raise
    except Exception:
        LOGGER.exception("Werewolf game loop failed")
        if ROOMS.get(room["chat_id"]) is room:
            await _cancel_room(room, "Ván đấu gặp lỗi nội bộ.")


async def _private_action(message, action: str, args: list[str]) -> None:
    user_id = int(message.from_user.id)
    chat_id = PLAYER_ROOMS.get(user_id)
    room = ROOMS.get(chat_id) if chat_id is not None else None
    if room is None:
        await send_message(message, "❌ Bạn không ở trong phòng Ma Sói nào.")
        return

    async with _room_lock(room["chat_id"]):
        if user_id not in room.get("alive", set()):
            await send_message(message, "💀 Bạn đã bị loại và không thể hành động.")
            return
        role = room.get("roles", {}).get(user_id)
        phase = str(room.get("phase"))

        if action in {"status", "trangthai", "trạngthái"}:
            await send_message(
                message,
                f"🐺 Vai trò: <b>{ROLE_LABELS.get(role, 'Chưa phân vai')}</b>\n"
                f"Giai đoạn: <b>{_phase_label(phase)}</b>\n"
                f"Người còn sống: <b>{len(room.get('alive', set()))}</b>.",
            )
            return

        if action in {"bite", "can", "cắn"}:
            if role != "wolf" or phase != "night_wolves":
                await send_message(message, "❌ Chỉ Ma Sói được cắn trong giai đoạn Sói hành động.")
                return
            target = _resolve_target(room, " ".join(args))
            if target is None or room["roles"].get(target) == "wolf":
                await send_message(message, "❌ Mục tiêu không hợp lệ hoặc là đồng minh Sói.")
                return
            room.setdefault("wolf_votes", {})[user_id] = target
            await send_message(message, "✅ Đã chọn " + _player_label(room["players"][target]) + ".")
            return

        if action in {"see", "soi"}:
            if role != "seer" or phase not in {"night_wolves", "night_special"}:
                await send_message(message, "❌ Chỉ Tiên Tri được soi vào ban đêm.")
                return
            if user_id in room.setdefault("seer_round", set()):
                await send_message(message, "❌ Bạn đã soi trong đêm này.")
                return
            target = _resolve_target(room, " ".join(args))
            if target is None or target == user_id:
                await send_message(message, "❌ Mục tiêu soi không hợp lệ.")
                return
            room["seer_round"].add(user_id)
            result = "LÀ MA SÓI" if room["roles"].get(target) == "wolf" else "không phải Ma Sói"
            await send_message(
                message,
                "🔮 " + _player_label(room["players"][target]) + f" <b>{result}</b>.",
            )
            return

        if action in {"protect", "baove", "bảo_vệ", "bao_ve"}:
            if role != "guard" or phase not in {"night_wolves", "night_special"}:
                await send_message(message, "❌ Chỉ Bảo Vệ được hành động vào ban đêm.")
                return
            target = _resolve_target(room, " ".join(args))
            if target is None:
                await send_message(message, "❌ Mục tiêu bảo vệ không hợp lệ.")
                return
            if target == room.get("guard_last"):
                await send_message(message, "❌ Không thể bảo vệ cùng một người hai đêm liên tiếp.")
                return
            room["guard_target"] = target
            await send_message(message, "🛡 Đã bảo vệ " + _player_label(room["players"][target]) + ".")
            return

        if action in {"save", "cuu", "cứu"}:
            if role != "witch" or phase != "night_special":
                await send_message(message, "❌ Chỉ Phù Thuỷ được cứu trong giai đoạn đặc biệt.")
                return
            if not room.get("witch_heal", False):
                await send_message(message, "❌ Bình cứu đã được sử dụng.")
                return
            if room.get("wolf_target") is None:
                await send_message(message, "❌ Đêm nay không có nạn nhân để cứu.")
                return
            if room.get("witch_action") is not None:
                await send_message(message, "❌ Mỗi đêm chỉ được dùng một bình.")
                return
            room["witch_action"] = "save"
            await send_message(message, "✅ Đã dùng bình cứu.")
            return

        if action in {"poison", "doc", "độc", "dau_doc"}:
            if role != "witch" or phase != "night_special":
                await send_message(message, "❌ Chỉ Phù Thuỷ được dùng bình độc trong giai đoạn đặc biệt.")
                return
            if not room.get("witch_poison", False):
                await send_message(message, "❌ Bình độc đã được sử dụng.")
                return
            if room.get("witch_action") is not None:
                await send_message(message, "❌ Mỗi đêm chỉ được dùng một bình.")
                return
            target = _resolve_target(room, " ".join(args))
            if target is None:
                await send_message(message, "❌ Mục tiêu đầu độc không hợp lệ.")
                return
            room["witch_action"] = "poison"
            room["witch_poison_target"] = target
            await send_message(message, "☠️ Đã đầu độc " + _player_label(room["players"][target]) + ".")
            return

    await send_message(
        message,
        "Cách dùng riêng tư: <code>/masoi bite</code>, <code>see</code>, "
        "<code>protect</code>, <code>save</code> hoặc <code>poison</code> theo vai trò.",
    )


async def _room_status(message, room: dict[str, Any]) -> None:
    players = list(room["players"].values())
    lines = [
        f"🐺 <b>Phòng Ma Sói — {_phase_label(str(room['phase']))}</b>",
        f"Người chơi: <b>{len(players)}/{WEREWOLF_MAX_PLAYERS}</b>",
    ]
    if room["phase"] == "lobby":
        remaining = max(0, int(room["lobby_deadline"] - time()))
        lines.append(f"Tự bắt đầu/hủy sau: <b>{remaining} giây</b>")
    else:
        lines.append(f"Còn sống: <b>{len(room.get('alive', set()))}</b>")
    lines.append("")
    lines.extend(
        f"{index}. {_player_label(player)}"
        for index, player in enumerate(players, start=1)
    )
    await send_message(message, "\n".join(lines))


@new_task
@entertainment_guard
async def werewolf_command(_, message):
    if await require_user(message) is None:
        return
    parts = (message.text or "").split()
    action = parts[1].lower() if len(parts) > 1 else "help"
    args = parts[2:]

    if message.chat.type == ChatType.PRIVATE:
        await _private_action(message, action, args)
        return

    collection = await require_game_collection(message)
    if collection is None:
        return
    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id)

    if action in {"help", "luat", "luật"}:
        await send_message(
            message,
            "🐺 <b>MA SÓI</b>\n\n"
            "<code>/masoi create</code> — mở phòng 90 giây\n"
            "<code>/masoi join</code> — tham gia\n"
            "<code>/masoi start</code> — chủ phòng bắt đầu khi đủ 5 người\n"
            "<code>/masoi status</code> — xem phòng\n"
            "<code>/masoi vote ID|@username</code> — bỏ phiếu ban ngày\n"
            "<code>/masoi leave</code> — rời khi còn ở sảnh\n"
            "<code>/masoi cancel</code> — chủ phòng huỷ sảnh\n\n"
            "Tối thiểu 5, tối đa 10 người; bot làm quản trò và không tính vào phòng. "
            "Phòng 5–9 người có 1 Sói. Phòng đúng 10 người có 2 Sói, "
            "Tiên Tri, Phù Thuỷ và Bảo Vệ. Không đủ 5 người khi hết thời gian sẽ tự huỷ. "
            "Trò chơi miễn phí; tiền và EXP được nhân đôi khi bùa tương ứng còn hiệu lực.",
        )
        return

    async with _room_lock(chat_id):
        room = ROOMS.get(chat_id)

        if action in {"create", "tao", "tạo"}:
            if room is not None:
                await send_message(message, "❌ Nhóm đã có phòng Ma Sói.")
                return
            other_chat = PLAYER_ROOMS.get(user_id)
            if other_chat is not None:
                await send_message(message, "❌ Bạn đang ở một phòng Ma Sói khác.")
                return
            if not await _dm(
                user_id,
                "✅ Bot đã kiểm tra tin nhắn riêng. Bạn có thể nhận vai trò Ma Sói.",
            ):
                await send_message(
                    message,
                    "❌ Hãy nhắn <code>/start</code> riêng cho bot rồi tạo phòng lại.",
                )
                return
            await ensure_message_user(collection, message)
            now = time()
            room = {
                "chat_id": chat_id,
                "title": message.chat.title or str(chat_id),
                "host_id": user_id,
                "phase": "lobby",
                "players": {user_id: _player_record(message.from_user)},
                "created_at": now,
                "lobby_deadline": now + WEREWOLF_LOBBY_SECONDS,
            }
            ROOMS[chat_id] = room
            PLAYER_ROOMS[user_id] = chat_id
            room["lobby_task"] = create_task(_lobby_timer(room))
            await send_message(
                message,
                f"🐺 <b>Đã mở phòng Ma Sói.</b>\n"
                f"Tối thiểu {WEREWOLF_MIN_PLAYERS}, tối đa {WEREWOLF_MAX_PLAYERS} người. "
                f"Có <b>{WEREWOLF_LOBBY_SECONDS} giây</b> để tham gia bằng "
                "<code>/masoi join</code>. Không đủ người phòng sẽ tự huỷ.",
            )
            return

        if room is None:
            await send_message(message, "❌ Nhóm chưa có phòng Ma Sói. Dùng <code>/masoi create</code>.")
            return

        if action in {"join", "thamgia", "tham_gia"}:
            if room["phase"] != "lobby":
                await send_message(message, "❌ Ván đấu đã bắt đầu.")
                return
            if user_id in room["players"]:
                await send_message(message, "ℹ️ Bạn đã ở trong phòng.")
                return
            if len(room["players"]) >= WEREWOLF_MAX_PLAYERS:
                await send_message(message, "❌ Phòng đã đủ 10 người.")
                return
            other_chat = PLAYER_ROOMS.get(user_id)
            if other_chat is not None and other_chat != chat_id:
                await send_message(message, "❌ Bạn đang ở một phòng Ma Sói khác.")
                return
            if not await _dm(
                user_id,
                "✅ Đã kiểm tra tin nhắn riêng. Vai trò sẽ được gửi khi ván bắt đầu.",
            ):
                await send_message(
                    message,
                    "❌ Hãy nhắn <code>/start</code> riêng cho bot rồi tham gia lại.",
                )
                return
            await ensure_message_user(collection, message)
            room["players"][user_id] = _player_record(message.from_user)
            PLAYER_ROOMS[user_id] = chat_id
            await send_message(
                message,
                f"✅ {_player_label(room['players'][user_id])} đã tham gia. "
                f"Phòng hiện có <b>{len(room['players'])}/{WEREWOLF_MAX_PLAYERS}</b> người.",
            )
            if len(room["players"]) == WEREWOLF_MAX_PLAYERS:
                await _start_room_locked(room)
            return

        if action in {"leave", "roi", "rời"}:
            if room["phase"] != "lobby":
                await send_message(message, "❌ Không thể rời sau khi ván đã bắt đầu.")
                return
            if user_id not in room["players"]:
                await send_message(message, "❌ Bạn không ở trong phòng.")
                return
            room["players"].pop(user_id, None)
            PLAYER_ROOMS.pop(user_id, None)
            if not room["players"]:
                await _cancel_room(room, "Không còn người chơi trong sảnh.")
                return
            if room["host_id"] == user_id:
                room["host_id"] = next(iter(room["players"]))
            await send_message(message, "✅ Đã rời phòng Ma Sói.")
            return

        if action in {"start", "batdau", "bắtđầu"}:
            if room["phase"] != "lobby":
                await send_message(message, "❌ Ván đấu đã bắt đầu.")
                return
            if room["host_id"] != user_id:
                await send_message(message, "❌ Chỉ chủ phòng được bắt đầu.")
                return
            if len(room["players"]) < WEREWOLF_MIN_PLAYERS:
                await send_message(
                    message,
                    f"❌ Cần ít nhất {WEREWOLF_MIN_PLAYERS} người; hiện có {len(room['players'])}.",
                )
                return
            await _start_room_locked(room)
            return

        if action in {"cancel", "huy", "huỷ", "hủy"}:
            if room["phase"] != "lobby" or room["host_id"] != user_id:
                await send_message(message, "❌ Chỉ chủ phòng được huỷ khi còn ở sảnh.")
                return
            await _cancel_room(room, "Chủ phòng đã huỷ.")
            return

        if action in {"status", "trangthai", "trạngthái"}:
            await _room_status(message, room)
            return

        if action in {"vote", "bo_phieu", "bophieu"}:
            if room["phase"] != "day_vote":
                await send_message(message, "❌ Chưa đến giai đoạn bỏ phiếu.")
                return
            if user_id not in room.get("alive", set()):
                await send_message(message, "💀 Người đã bị loại không được bỏ phiếu.")
                return
            target = _resolve_target(room, " ".join(args))
            if target is None:
                await send_message(message, "❌ Mục tiêu bỏ phiếu không hợp lệ.")
                return
            room.setdefault("day_votes", {})[user_id] = target
            await send_message(
                message,
                f"🗳 {_player_label(room['players'][user_id])} đã bỏ phiếu cho "
                f"{_player_label(room['players'][target])}.",
            )
            return

    await send_message(message, "Dùng <code>/masoi help</code> để xem lệnh.")
