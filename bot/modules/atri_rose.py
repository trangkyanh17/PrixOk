from __future__ import annotations

import asyncio
import io
import inspect
import json
import os
import re
import secrets
import shlex
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pyrogram import StopPropagation, enums, filters
from pyrogram.handlers import CallbackQueryHandler, EditedMessageHandler, MessageHandler
from pyrogram.types import ChatPermissions, ChatPrivileges, InlineKeyboardButton, InlineKeyboardMarkup

from bot import LOGGER
from bot.core.config_manager import Config
from bot.modules.atri_rose_timers import (
    cancel_timed_release,
    schedule_timed_release,
)

DB_PATH = Path(os.getenv("ATRI_ROSE_DB", "/app/atri_data/atri_rose.sqlite3"))

COMMANDS = {
    "rosehelp", "modhelp", "id", "info", "adminlist", "report",
    "ban", "unban", "kick", "mute", "unmute", "tban", "tmute",
    "warn", "warns", "resetwarns", "promote", "demote",
    "del", "purge", "purgefrom", "pin", "unpin", "unpinall",
    "setrules", "rules", "clearrules", "save", "get", "clear", "notes",
    "filter", "filters", "stop", "setwelcome", "welcome", "setgoodbye",
    "goodbye", "captcha", "captchamode", "captchatime", "lock", "unlock",
    "locks", "locktypes", "lockwarns", "allowlist", "rmallowlist",
    "rmallowlistall", "addblocklist", "blocklist", "rmblocklist",
    "blocklistmode", "setflood", "flood", "setfloodmode", "approve",
    "unapprove", "approved", "setlog", "unsetlog", "logchannel",
    "newfed", "delfed", "joinfed", "leavefed", "fedinfo", "fedadmins",
    "fedpromote", "feddemote", "fban", "unfban", "fbanlist",
    "export", "import", "reset", "cleancommand",
}

LOCK_TYPES = {
    "all", "album", "anonchannel", "audio", "bot", "button", "cashtag",
    "cjk", "command", "contact", "cyrillic", "document", "email", "emoji",
    "emojionly", "forward", "gif", "invitelink", "location", "phone",
    "photo", "poll", "rtl", "sticker", "text", "url", "video",
    "videonote", "voice",
}

DEFAULTS = {
    "welcome_enabled": "0",
    "welcome_payload": json.dumps({"text": "Chào mừng {mention} đến với {chatname}!"}, ensure_ascii=False),
    "goodbye_enabled": "0",
    "goodbye_payload": json.dumps({"text": "Tạm biệt {fullname}."}, ensure_ascii=False),
    "captcha_enabled": "0",
    "captcha_timeout": "600",
    "rules": "",
    "warn_limit": "3",
    "warn_mode": "mute",
    "lockwarns": "0",
    "blocklist_mode": "delete",
    "flood_limit": "0",
    "flood_window": "10",
    "flood_mode": "mute 60m",
    "log_chat_id": "0",
    "clean_commands": "0",
}

_DB_LOCK = asyncio.Lock()
_INITIALIZED = False
_FLOOD: dict[tuple[int, int], deque[float]] = defaultdict(deque)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _init_db_sync() -> None:
    with _connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS rose_settings(chat_id INTEGER,key TEXT,value TEXT,PRIMARY KEY(chat_id,key));
        CREATE TABLE IF NOT EXISTS rose_warnings(chat_id INTEGER,user_id INTEGER,count INTEGER,reasons_json TEXT,updated_at INTEGER,PRIMARY KEY(chat_id,user_id));
        CREATE TABLE IF NOT EXISTS rose_notes(chat_id INTEGER,name TEXT,payload_json TEXT,created_by INTEGER,updated_at INTEGER,PRIMARY KEY(chat_id,name));
        CREATE TABLE IF NOT EXISTS rose_filters(chat_id INTEGER,trigger TEXT,payload_json TEXT,created_by INTEGER,updated_at INTEGER,PRIMARY KEY(chat_id,trigger));
        CREATE TABLE IF NOT EXISTS rose_locks(chat_id INTEGER,lock_type TEXT,mode TEXT,reason TEXT,seconds INTEGER,PRIMARY KEY(chat_id,lock_type));
        CREATE TABLE IF NOT EXISTS rose_allowlist(chat_id INTEGER,item TEXT,PRIMARY KEY(chat_id,item));
        CREATE TABLE IF NOT EXISTS rose_blocklist(chat_id INTEGER,item TEXT,PRIMARY KEY(chat_id,item));
        CREATE TABLE IF NOT EXISTS rose_approved(chat_id INTEGER,user_id INTEGER,approved_by INTEGER,created_at INTEGER,PRIMARY KEY(chat_id,user_id));
        CREATE TABLE IF NOT EXISTS rose_captchas(chat_id INTEGER,user_id INTEGER,token TEXT,expires_at INTEGER,message_id INTEGER,PRIMARY KEY(chat_id,user_id));
        CREATE TABLE IF NOT EXISTS rose_federations(fed_id TEXT PRIMARY KEY,name TEXT,owner_id INTEGER,created_at INTEGER);
        CREATE TABLE IF NOT EXISTS rose_fed_admins(fed_id TEXT,user_id INTEGER,PRIMARY KEY(fed_id,user_id));
        CREATE TABLE IF NOT EXISTS rose_fed_chats(fed_id TEXT,chat_id INTEGER UNIQUE,joined_at INTEGER,PRIMARY KEY(fed_id,chat_id));
        CREATE TABLE IF NOT EXISTS rose_fed_bans(fed_id TEXT,user_id INTEGER,reason TEXT,banned_by INTEGER,created_at INTEGER,PRIMARY KEY(fed_id,user_id));
        CREATE INDEX IF NOT EXISTS idx_rose_fed_bans_user ON rose_fed_bans(user_id);
        """)
        db.commit()


async def _ensure_db() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    async with _DB_LOCK:
        if not _INITIALIZED:
            await asyncio.to_thread(_init_db_sync)
            _INITIALIZED = True


async def _db(function, *args):
    await _ensure_db()
    async with _DB_LOCK:
        return await asyncio.to_thread(function, *args)


def _get_setting_sync(chat_id: int, key: str) -> str:
    with _connect() as db:
        row = db.execute("SELECT value FROM rose_settings WHERE chat_id=? AND key=?", (chat_id, key)).fetchone()
    return str(row["value"]) if row else DEFAULTS.get(key, "")


async def _setting(chat_id: int, key: str) -> str:
    return await _db(_get_setting_sync, chat_id, key)


def _set_setting_sync(chat_id: int, key: str, value: str) -> None:
    with _connect() as db:
        db.execute("INSERT INTO rose_settings(chat_id,key,value) VALUES(?,?,?) ON CONFLICT(chat_id,key) DO UPDATE SET value=excluded.value", (chat_id, key, value))
        db.commit()


async def _set_setting(chat_id: int, key: str, value: Any) -> None:
    await _db(_set_setting_sync, chat_id, key, str(value))


def _rows_sync(table: str, chat_id: int, order: str) -> list[dict[str, Any]]:
    allowed = {"rose_notes", "rose_filters", "rose_locks", "rose_allowlist", "rose_blocklist", "rose_approved"}
    if table not in allowed:
        raise ValueError("bad table")
    with _connect() as db:
        rows = db.execute(f"SELECT * FROM {table} WHERE chat_id=? ORDER BY {order}", (chat_id,)).fetchall()
    return [dict(row) for row in rows]


def _insert_sync(table: str, columns: tuple[str, ...], values: tuple[Any, ...]) -> None:
    if table not in {"rose_allowlist", "rose_blocklist", "rose_approved", "rose_locks"}:
        raise ValueError("bad table")
    with _connect() as db:
        db.execute(f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in values)})", values)
        db.commit()


def _delete_sync(table: str, chat_id: int, key: str, value: Any) -> int:
    allowed = {( "rose_notes", "name"), ("rose_filters", "trigger"), ("rose_locks", "lock_type"), ("rose_allowlist", "item"), ("rose_blocklist", "item"), ("rose_approved", "user_id")}
    if (table, key) not in allowed:
        raise ValueError("bad delete")
    with _connect() as db:
        cur = db.execute(f"DELETE FROM {table} WHERE chat_id=? AND {key}=?", (chat_id, value))
        db.commit()
    return max(0, cur.rowcount)


def _get_payload_sync(table: str, chat_id: int, key: str, value: str) -> dict[str, Any] | None:
    if (table, key) not in {("rose_notes", "name"), ("rose_filters", "trigger")}:
        raise ValueError("bad payload")
    with _connect() as db:
        row = db.execute(f"SELECT * FROM {table} WHERE chat_id=? AND {key}=?", (chat_id, value)).fetchone()
    return dict(row) if row else None


def _save_payload_sync(table: str, chat_id: int, key: str, value: str, payload: str, user_id: int) -> None:
    if (table, key) not in {("rose_notes", "name"), ("rose_filters", "trigger")}:
        raise ValueError("bad payload")
    with _connect() as db:
        db.execute(f"INSERT INTO {table}(chat_id,{key},payload_json,created_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(chat_id,{key}) DO UPDATE SET payload_json=excluded.payload_json,created_by=excluded.created_by,updated_at=excluded.updated_at", (chat_id, value, payload, user_id, int(time.time())))
        db.commit()


def _command(text: str) -> tuple[str, str]:
    text = str(text or "").strip()
    if not text.startswith("/"):
        return "", ""
    head, _, rest = text.partition(" ")
    return head[1:].split("@", 1)[0].casefold(), rest.strip()


def _truth(value: str) -> bool | None:
    value = str(value or "").strip().casefold()
    if value in {"on", "yes", "true", "1", "enable", "enabled"}:
        return True
    if value in {"off", "no", "false", "0", "disable", "disabled"}:
        return False
    return None


def _duration(value: str) -> int | None:
    match = re.fullmatch(r"(\d+)([mhdw])", value.strip().casefold())
    if not match:
        return None
    seconds = int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
    return seconds if 60 <= seconds <= 366 * 86400 else None


def _until(seconds: int) -> datetime | None:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds) if seconds else None


def _name(user) -> str:
    if user is None:
        return "người dùng"
    full = " ".join(filter(None, [str(getattr(user, "first_name", "") or "").strip(), str(getattr(user, "last_name", "") or "").strip()]))
    username = str(getattr(user, "username", "") or "").strip()
    return full or (f"@{username}" if username else str(getattr(user, "id", "unknown")))


def _mention(user) -> str:
    username = str(getattr(user, "username", "") or "").strip()
    return f"@{username}" if username else _name(user)


async def _is_admin(client, message, user_id: int | None = None) -> bool:
    sender_chat = getattr(message, "sender_chat", None)
    if sender_chat and int(getattr(sender_chat, "id", 0) or 0) == int(message.chat.id):
        return True
    if user_id is None:
        user_id = int(getattr(getattr(message, "from_user", None), "id", 0) or 0)
    if not user_id:
        return False
    if int(user_id) == int(Config.OWNER_ID):
        return True
    try:
        member = await client.get_chat_member(message.chat.id, user_id)
    except Exception:
        return False
    status = str(getattr(member, "status", "")).casefold()
    return "owner" in status or "administrator" in status


async def _is_owner(client, message) -> bool:
    user_id = int(getattr(getattr(message, "from_user", None), "id", 0) or 0)
    if user_id == int(Config.OWNER_ID):
        return True
    try:
        member = await client.get_chat_member(message.chat.id, user_id)
    except Exception:
        return False
    return "owner" in str(getattr(member, "status", "")).casefold()


async def _require_admin(client, message) -> bool:
    if await _is_admin(client, message):
        return True
    await message.reply_text("Lệnh này chỉ dành cho quản trị viên nhóm.", quote=True, parse_mode=None)
    return False


async def _require_owner(client, message) -> bool:
    if await _is_owner(client, message):
        return True
    await message.reply_text("Lệnh này chỉ dành cho chủ nhóm hoặc chủ bot.", quote=True, parse_mode=None)
    return False


async def _resolve_user(client, message, argument: str):
    reply = getattr(message, "reply_to_message", None)
    if reply and getattr(reply, "from_user", None):
        return reply.from_user, argument.strip()
    tokens = shlex.split(argument) if argument.strip() else []
    if not tokens:
        return None, ""
    token = tokens.pop(0)
    try:
        return await client.get_users(int(token) if token.lstrip("-").isdigit() else token), " ".join(tokens)
    except Exception:
        return None, " ".join(tokens)


async def _resolve_timed(client, message, argument: str):
    reply = getattr(message, "reply_to_message", None)
    tokens = shlex.split(argument) if argument.strip() else []
    if reply and getattr(reply, "from_user", None):
        user = reply.from_user
    else:
        if not tokens:
            return None, None, ""
        target = tokens.pop(0)
        try:
            user = await client.get_users(int(target) if target.lstrip("-").isdigit() else target)
        except Exception:
            return None, None, ""
    if not tokens:
        return user, None, ""
    return user, _duration(tokens.pop(0)), " ".join(tokens)


async def _target_ok(client, message, user) -> bool:
    if user is None:
        await message.reply_text("Reply, dùng @username hoặc user ID.", quote=True)
        return False
    if await _is_admin(client, message, int(user.id)):
        await message.reply_text("Không thể áp dụng hình phạt cho quản trị viên.", quote=True)
        return False
    return True


def _full_permissions() -> ChatPermissions:
    return ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True)


def _mute_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False
    )


async def _bot_privileges(
    client,
    chat_id: int,
):
    member = await client.get_chat_member(
        chat_id,
        int(
            client.me.id
        ),
    )

    return getattr(
        member,
        "privileges",
        None,
    )


async def _promote_member(
    client,
    chat_id: int,
    user_id: int,
) -> None:
    own = await _bot_privileges(
        client,
        chat_id,
    )

    if (
        own is None
        or not bool(
            getattr(
                own,
                "can_promote_members",
                False,
            )
        )
    ):
        raise RuntimeError(
            "Atri chưa có quyền 'Thêm quản trị viên / Add new admins'."
        )

    privileges = ChatPrivileges(
        can_manage_chat=True,
        can_change_info=bool(
            getattr(
                own,
                "can_change_info",
                False,
            )
        ),
        can_delete_messages=bool(
            getattr(
                own,
                "can_delete_messages",
                False,
            )
        ),
        can_manage_video_chats=bool(
            getattr(
                own,
                "can_manage_video_chats",
                False,
            )
        ),
        can_restrict_members=bool(
            getattr(
                own,
                "can_restrict_members",
                False,
            )
        ),
        can_invite_users=bool(
            getattr(
                own,
                "can_invite_users",
                False,
            )
        ),
        can_pin_messages=bool(
            getattr(
                own,
                "can_pin_messages",
                False,
            )
        ),
        can_manage_topics=bool(
            getattr(
                own,
                "can_manage_topics",
                False,
            )
        ),
        can_promote_members=False,
    )

    await client.promote_chat_member(
        chat_id,
        int(
            user_id
        ),
        privileges=privileges,
    )


def _member_status_key(member) -> str:
    status = getattr(
        member,
        "status",
        "",
    )

    value = getattr(
        status,
        "value",
        status,
    )

    return str(
        value or ""
    ).casefold()


def _demotion_privileges() -> ChatPrivileges:
    """Build ChatPrivileges with every supported boolean explicitly False.

    ChatPrivileges() leaves optional fields as None. For Telegram demotion the
    protocol requires every administrator-right boolean to be False, not merely
    omitted. Introspecting Kurigram's constructor keeps this future-compatible
    with added rights such as stories/tags/direct-message management.
    """
    signature = inspect.signature(
        ChatPrivileges.__init__
    )

    kwargs: dict[str, bool] = {}

    for name, parameter in signature.parameters.items():
        if name == "self":
            continue

        if parameter.kind not in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            continue

        if (
            name == "is_anonymous"
            or name.startswith("can_")
        ):
            kwargs[name] = False

    required = {
        "is_anonymous",
        "can_manage_chat",
        "can_delete_messages",
        "can_manage_video_chats",
        "can_restrict_members",
        "can_promote_members",
        "can_change_info",
        "can_invite_users",
    }

    missing = sorted(
        required.difference(
            kwargs
        )
    )

    if missing:
        raise RuntimeError(
            "Kurigram ChatPrivileges thiếu field demote bắt buộc: "
            + ", ".join(
                missing
            )
        )

    return ChatPrivileges(
        **kwargs
    )


async def _demote_member(
    client,
    chat_id: int,
    user_id: int,
) -> None:
    own = await _bot_privileges(
        client,
        chat_id,
    )

    if (
        own is None
        or not bool(
            getattr(
                own,
                "can_promote_members",
                False,
            )
        )
    ):
        raise RuntimeError(
            "Atri chưa có quyền 'Thêm quản trị viên / Add new admins'."
        )

    target = await client.get_chat_member(
        chat_id,
        int(
            user_id
        ),
    )

    status = _member_status_key(
        target
    )

    if (
        "owner" in status
        or "creator" in status
    ):
        raise RuntimeError(
            "Không thể hạ chức chủ sở hữu nhóm."
        )

    if "administrator" not in status:
        # Idempotent: người này đã không còn là admin.
        return

    can_be_edited = getattr(
        target,
        "can_be_edited",
        None,
    )

    if can_be_edited is False:
        raise RuntimeError(
            "Telegram không cho Atri chỉnh quyền admin này. "
            "Admin mục tiêu phải nằm trong phạm vi Atri được phép quản lý."
        )

    await client.promote_chat_member(
        chat_id,
        int(
            user_id
        ),
        privileges=_demotion_privileges(),
    )

    # Không báo thành công khi chỉ tạo ra admin 0 quyền. Kiểm tra trạng thái
    # thật từ Telegram; chỉ return khi target đã rời ADMINISTRATOR hoàn toàn.
    for attempt in range(4):
        updated = await client.get_chat_member(
            chat_id,
            int(
                user_id
            ),
        )

        updated_status = _member_status_key(
            updated
        )

        if "administrator" not in updated_status:
            return

        if attempt < 3:
            await asyncio.sleep(
                0.35
            )

    raise RuntimeError(
        "Telegram vẫn trả mục tiêu ở trạng thái administrator sau khi "
        "đã tước toàn bộ quyền; Atri không báo hạ chức thành công để tránh "
        "tạo admin bù nhìn."
    )


async def _action(
    client,
    chat_id: int,
    user_id: int,
    mode: str,
    seconds: int = 0,
) -> str:
    mode = str(
        mode or ""
    ).casefold()

    if mode == "delete":
        return "đã xóa tin"

    if mode == "kick":
        await client.ban_chat_member(
            chat_id,
            user_id,
        )
        await client.unban_chat_member(
            chat_id,
            user_id,
        )
        return "đã kick"

    if mode == "ban":
        await client.ban_chat_member(
            chat_id,
            user_id,
        )
        return "đã ban"

    if mode == "tban":
        seconds = int(
            seconds or 0
        )

        if seconds < 30:
            raise ValueError(
                "Thời gian tban phải từ 30 giây trở lên."
            )

        if seconds > 366 * 86400:
            raise ValueError(
                "Thời gian tban tối đa là 366 ngày."
            )

        await client.ban_chat_member(
            chat_id,
            user_id,
            until_date=_until(
                seconds
            ),
        )

        return "đã tban"

    if mode == "mute":
        await client.restrict_chat_member(
            chat_id,
            user_id,
            permissions=_mute_permissions(),
        )
        return "đã mute"

    if mode == "tmute":
        seconds = int(
            seconds or 0
        )

        if seconds < 30:
            raise ValueError(
                "Thời gian tmute phải từ 30 giây trở lên."
            )

        if seconds > 366 * 86400:
            raise ValueError(
                "Thời gian tmute tối đa là 366 ngày."
            )

        await client.restrict_chat_member(
            chat_id,
            user_id,
            permissions=_mute_permissions(),
            until_date=_until(
                seconds
            ),
        )

        return "đã tmute"

    raise ValueError(
        f"Action không hỗ trợ: {mode}"
    )

async def _log(client, chat_id: int, text: str) -> None:
    try:
        target = int(await _setting(chat_id, "log_chat_id"))
    except ValueError:
        target = 0
    if target:
        try:
            await client.send_message(target, text, parse_mode=None)
        except Exception:
            LOGGER.exception("Atri Rose log failed")


def _payload(message, text: str) -> dict[str, Any]:
    source = getattr(message, "reply_to_message", None) or message
    result: dict[str, Any] = {"text": text.strip()}
    for attr in ("photo", "video", "document", "animation", "audio", "voice", "sticker"):
        media = getattr(source, attr, None)
        file_id = str(getattr(media, "file_id", "") or "")
        if file_id:
            result.update(media_type=attr, file_id=file_id)
            if not result["text"]:
                result["text"] = str(getattr(source, "caption", "") or getattr(source, "text", "") or "").strip()
            break
    return result


_BUTTON_RE = re.compile(r"\[([^\]]+)\]\(buttonurl://([^)]+)\)", re.I)


def _buttons(text: str):
    rows, current = [], []
    for label, raw_url in _BUTTON_RE.findall(text):
        same = raw_url.endswith(":same")
        url = raw_url[:-5] if same else raw_url
        if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
            url = "https://" + url
        button = InlineKeyboardButton(label, url=url)
        if same and current:
            current.append(button)
        else:
            if current:
                rows.append(current)
            current = [button]
    if current:
        rows.append(current)
    return _BUTTON_RE.sub("", text).strip(), InlineKeyboardMarkup(rows) if rows else None


def _format(text: str, message, user=None) -> str:
    user = user or getattr(message, "from_user", None)
    username = str(getattr(user, "username", "") or "")
    values = {
        "first": str(getattr(user, "first_name", "") or ""),
        "fullname": _name(user),
        "username": f"@{username}" if username else "",
        "mention": _mention(user),
        "id": str(getattr(user, "id", "") or ""),
        "chatname": str(getattr(message.chat, "title", "") or ""),
    }
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


async def _send_payload(client, chat_id: int, data: dict[str, Any], message, user=None):
    text, markup = _buttons(_format(str(data.get("text") or ""), message, user))
    media_type, file_id = str(data.get("media_type") or ""), str(data.get("file_id") or "")
    if not media_type or not file_id:
        return await client.send_message(chat_id, text or " ", reply_markup=markup, parse_mode=None)
    caption = text or None
    methods = {
        "photo": client.send_photo,
        "video": client.send_video,
        "document": client.send_document,
        "animation": client.send_animation,
        "audio": client.send_audio,
        "voice": client.send_voice,
    }
    if media_type in methods:
        return await methods[media_type](chat_id, file_id, caption=caption, reply_markup=markup)
    if media_type == "sticker":
        sent = await client.send_sticker(chat_id, file_id)
        if text:
            await client.send_message(chat_id, text, reply_markup=markup, parse_mode=None)
        return sent
    return await client.send_message(chat_id, text or " ", reply_markup=markup, parse_mode=None)


def _warn_sync(chat_id: int, user_id: int, reason: str) -> tuple[int, list[str]]:
    with _connect() as db:
        row = db.execute("SELECT count,reasons_json FROM rose_warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        count = int(row["count"] or 0) if row else 0
        try:
            reasons = json.loads(row["reasons_json"]) if row else []
        except (ValueError, TypeError):
            reasons = []
        count += 1
        if reason:
            reasons.append(reason[:500])
        reasons = reasons[-20:]
        db.execute("INSERT INTO rose_warnings(chat_id,user_id,count,reasons_json,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(chat_id,user_id) DO UPDATE SET count=excluded.count,reasons_json=excluded.reasons_json,updated_at=excluded.updated_at", (chat_id, user_id, count, json.dumps(reasons, ensure_ascii=False), int(time.time())))
        db.commit()
    return count, reasons


def _get_warns_sync(chat_id: int, user_id: int) -> tuple[int, list[str]]:
    with _connect() as db:
        row = db.execute("SELECT count,reasons_json FROM rose_warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    if not row:
        return 0, []
    try:
        reasons = json.loads(row["reasons_json"])
    except (ValueError, TypeError):
        reasons = []
    return int(row["count"] or 0), reasons if isinstance(reasons, list) else []


def _reset_warns_sync(chat_id: int, user_id: int) -> int:
    with _connect() as db:
        cur = db.execute("DELETE FROM rose_warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        db.commit()
    return max(0, cur.rowcount)


async def _issue_warn(
    client,
    message,
    user,
    reason: str,
) -> tuple[int, int]:
    count, _ = await _db(
        _warn_sync,
        message.chat.id,
        int(
            user.id
        ),
        reason,
    )

    try:
        limit = max(
            1,
            int(
                await _setting(
                    message.chat.id,
                    "warn_limit",
                )
            ),
        )
    except ValueError:
        limit = 3

    if count >= limit:
        mode = await _setting(
            message.chat.id,
            "warn_mode",
        )

        try:
            action = await _action(
                client,
                message.chat.id,
                int(
                    user.id
                ),
                mode,
                86400,
            )

            if mode in {
                "tmute",
                "tban",
            }:
                await schedule_timed_release(
                    client,
                    message.chat.id,
                    int(
                        user.id
                    ),
                    mode,
                    86400,
                )

            elif mode in {
                "mute",
                "ban",
                "kick",
            }:
                await cancel_timed_release(
                    message.chat.id,
                    int(
                        user.id
                    ),
                )

        except Exception as exc:
            action = (
                "không áp dụng được hình phạt: "
                f"{exc}"
            )

        await _db(
            _reset_warns_sync,
            message.chat.id,
            int(
                user.id
            ),
        )

        await message.reply_text(
            f"{_mention(user)} đạt {limit}/{limit} cảnh cáo; {action}.",
            quote=True,
            parse_mode=None,
        )

        return (
            0,
            limit,
        )

    return (
        count,
        limit,
    )


def _approved_sync(chat_id: int, user_id: int) -> bool:
    with _connect() as db:
        row = db.execute("SELECT 1 FROM rose_approved WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    return row is not None


def _domains(text: str) -> set[str]:
    result = set()
    for raw in re.findall(r"(?:https?://|www\.)[^\s<>()]+", text, re.I):
        host = urlparse(raw if "://" in raw else "https://" + raw).hostname
        if host:
            result.add(host.casefold())
    return result


def _detect(message, text: str) -> set[str]:
    result = set()
    if text:
        result.add("text")
    if text.startswith("/"):
        result.add("command")
    mapping = {
        "photo": "photo", "video": "video", "document": "document",
        "sticker": "sticker", "animation": "gif", "voice": "voice",
        "audio": "audio", "video_note": "videonote", "poll": "poll",
        "contact": "contact", "location": "location",
    }
    for attr, lock_type in mapping.items():
        if getattr(message, attr, None):
            result.add(lock_type)
    if getattr(message, "venue", None): result.add("location")
    if getattr(message, "forward_date", None) or getattr(message, "forward_origin", None): result.add("forward")
    if getattr(message, "sender_chat", None): result.add("anonchannel")
    if getattr(message, "reply_markup", None): result.add("button")
    if getattr(message, "media_group_id", None): result.add("album")
    if re.search(r"https?://|www\.", text, re.I): result.add("url")
    if re.search(r"(?:t\.me|telegram\.me)/(?:joinchat/|\+|[A-Za-z0-9_]{4,})", text, re.I): result.add("invitelink")
    if re.search(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", text): result.add("email")
    if re.search(r"\+?\d[\d\s().-]{7,}\d", text): result.add("phone")
    if re.search(r"\$[A-Za-z][A-Za-z0-9_]{1,14}\b", text): result.add("cashtag")
    if re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text): result.add("cjk")
    if re.search(r"[\u0400-\u04ff]", text): result.add("cyrillic")
    if re.search(r"[\u0590-\u08ff]", text): result.add("rtl")
    if re.search(r"[\U0001F300-\U0001FAFF]", text): result.add("emoji")
    if text and all(not ch.isalnum() for ch in text if not ch.isspace()): result.add("emojionly")
    if getattr(message, "new_chat_members", None) and any(getattr(user, "is_bot", False) for user in message.new_chat_members): result.add("bot")
    if result:
        result.add("all")
    return result


async def _allowlisted(chat_id: int, lock_type: str, text: str) -> bool:
    rows = await _db(_rows_sync, "rose_allowlist", chat_id, "item")
    items = {str(row["item"]).casefold() for row in rows}
    if lock_type in {"url", "invitelink", "button"}:
        domains = _domains(text)
        if any(any(host == item or host.endswith("." + item) for host in domains) for item in items):
            return True
    if lock_type == "command":
        name, _ = _command(text)
        return f"/{name}" in items
    return False


async def _violation(client, message, user, mode: str, reason: str, seconds: int = 0, warn: bool = False) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    if warn:
        await _issue_warn(client, message, user, reason)
        return
    try:
        action = await _action(client, message.chat.id, int(user.id), mode, seconds)
    except Exception as exc:
        action = f"lỗi: {exc}"
    await _log(client, message.chat.id, f"[Atri Rose] {_mention(user)}: {reason}; {action}.")


def _fed_for_chat_sync(chat_id: int) -> dict[str, Any] | None:
    with _connect() as db:
        row = db.execute("SELECT f.* FROM rose_fed_chats c JOIN rose_federations f ON f.fed_id=c.fed_id WHERE c.chat_id=?", (chat_id,)).fetchone()
    return dict(row) if row else None


def _fed_ban_sync(fed_id: str, user_id: int) -> dict[str, Any] | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM rose_fed_bans WHERE fed_id=? AND user_id=?", (fed_id, user_id)).fetchone()
    return dict(row) if row else None


async def _captcha_expire(client, chat_id: int, user_id: int, token: str, timeout: int) -> None:
    await asyncio.sleep(timeout)
    def pop():
        with _connect() as db:
            row = db.execute("SELECT * FROM rose_captchas WHERE chat_id=? AND user_id=? AND token=? AND expires_at<=?", (chat_id, user_id, token, int(time.time()))).fetchone()
            if not row:
                return None
            db.execute("DELETE FROM rose_captchas WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            db.commit()
        return dict(row)
    row = await _db(pop)
    if not row:
        return
    try:
        await client.ban_chat_member(chat_id, user_id)
        await client.unban_chat_member(chat_id, user_id)
        if row.get("message_id"):
            await client.delete_messages(chat_id, int(row["message_id"]))
    except Exception:
        LOGGER.exception("Captcha expiry failed")


async def rose_callback(client, query) -> None:
    data = str(getattr(query, "data", "") or "")
    try:
        _, chat_raw, user_raw, token = data.split(":", 3)
        chat_id, user_id = int(chat_raw), int(user_raw)
    except Exception:
        return
    if int(query.from_user.id) != user_id:
        await query.answer("Nút này không dành cho bạn.", show_alert=True)
        return
    def consume():
        with _connect() as db:
            row = db.execute("SELECT 1 FROM rose_captchas WHERE chat_id=? AND user_id=? AND token=? AND expires_at>=?", (chat_id, user_id, token, int(time.time()))).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM rose_captchas WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            db.commit()
        return True
    if not await _db(consume):
        await query.answer("CAPTCHA đã hết hạn.", show_alert=True)
        return
    try:
        await client.restrict_chat_member(chat_id, user_id, permissions=_full_permissions())
        await query.answer("Đã xác minh.")
        await query.message.edit_text(f"{_mention(query.from_user)} đã xác minh thành công.", parse_mode=None)
    except Exception as exc:
        await query.answer(f"Không thể mở chat: {exc}", show_alert=True)


async def _member_event(client, message) -> bool:
    members = getattr(message, "new_chat_members", None)
    if members:
        for user in members:
            if int(getattr(user, "id", 0) or 0) == int(getattr(client.me, "id", 0) or 0):
                continue
            fed = await _db(_fed_for_chat_sync, message.chat.id)
            if fed:
                ban = await _db(_fed_ban_sync, fed["fed_id"], int(user.id))
                if ban:
                    try:
                        await client.ban_chat_member(message.chat.id, int(user.id))
                        await message.reply_text(f"{_mention(user)} bị federation ban: {ban['reason'] or 'không lý do'}.", quote=False, parse_mode=None)
                    except Exception:
                        LOGGER.exception("Fed ban apply failed")
                    continue
            captcha = _truth(await _setting(message.chat.id, "captcha_enabled"))
            welcome = _truth(await _setting(message.chat.id, "welcome_enabled"))
            data = json.loads(await _setting(message.chat.id, "welcome_payload"))
            if captcha:
                token = secrets.token_urlsafe(8)
                timeout = max(60, int(await _setting(message.chat.id, "captcha_timeout")))
                try:
                    await client.restrict_chat_member(message.chat.id, int(user.id), permissions=_mute_permissions())
                except Exception:
                    LOGGER.exception("Captcha mute failed")
                text = _format(str(data.get("text") or "Chào mừng {mention}!"), message, user)
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("Tôi không phải bot", callback_data=f"rose_captcha:{message.chat.id}:{user.id}:{token}")]])
                sent = await client.send_message(message.chat.id, text + "\n\nNhấn nút để được mở chat.", reply_markup=markup, parse_mode=None)
                def store():
                    with _connect() as db:
                        db.execute("INSERT OR REPLACE INTO rose_captchas(chat_id,user_id,token,expires_at,message_id) VALUES(?,?,?,?,?)", (message.chat.id, int(user.id), token, int(time.time()) + timeout, int(sent.id)))
                        db.commit()
                await _db(store)
                asyncio.create_task(_captcha_expire(client, message.chat.id, int(user.id), token, timeout))
            elif welcome:
                await _send_payload(client, message.chat.id, data, message, user)
        return True
    left = getattr(message, "left_chat_member", None)
    if left and _truth(await _setting(message.chat.id, "goodbye_enabled")):
        await _send_payload(client, message.chat.id, json.loads(await _setting(message.chat.id, "goodbye_payload")), message, left)
        return True
    return False


async def _passive(client, message, text: str) -> bool:
    user = getattr(message, "from_user", None)
    if user is None or getattr(user, "is_bot", False) or await _is_admin(client, message, int(user.id)) or await _db(_approved_sync, message.chat.id, int(user.id)):
        return False
    detected = _detect(message, text)
    for row in await _db(_rows_sync, "rose_locks", message.chat.id, "lock_type"):
        lock_type = str(row["lock_type"])
        if lock_type in detected and not await _allowlisted(message.chat.id, lock_type, text):
            mode = str(row["mode"])
            warn = mode == "warn" or _truth(await _setting(message.chat.id, "lockwarns")) is True
            await _violation(client, message, user, "delete" if mode == "warn" else mode, str(row["reason"] or f"Nội dung bị khóa: {lock_type}"), int(row["seconds"] or 0), warn)
            return True
    folded = text.casefold()
    for row in await _db(_rows_sync, "rose_blocklist", message.chat.id, "item"):
        item = str(row["item"])
        if item.casefold() in folded:
            parts = (await _setting(message.chat.id, "blocklist_mode")).split()
            mode = parts[0] if parts else "delete"
            seconds = _duration(parts[1]) if len(parts) > 1 else 0
            await _violation(client, message, user, "delete" if mode == "warn" else mode, f"Blocklist: {item}", seconds or 0, mode == "warn")
            return True
    try:
        limit, window = int(await _setting(message.chat.id, "flood_limit")), int(await _setting(message.chat.id, "flood_window"))
    except ValueError:
        limit, window = 0, 10
    if limit > 0:
        key, now = (message.chat.id, int(user.id)), time.monotonic()
        queue = _FLOOD[key]
        while queue and now - queue[0] > window:
            queue.popleft()
        queue.append(now)
        if len(queue) >= limit:
            queue.clear()
            parts = (await _setting(message.chat.id, "flood_mode")).split()
            mode = parts[0] if parts else "mute"
            seconds = _duration(parts[1]) if len(parts) > 1 else 3600
            await _violation(client, message, user, mode, f"Flood: {limit} tin/{window}s", seconds or 3600)
            return True
    return False


async def _triggers(client, message, text: str) -> bool:
    if not text:
        return False
    if text.startswith("#"):
        name = text.split()[0][1:].casefold()
        row = await _db(_get_payload_sync, "rose_notes", message.chat.id, "name", name)
        if row:
            await _send_payload(client, message.chat.id, json.loads(row["payload_json"]), message)
            return True
    folded = text.casefold()
    for row in await _db(_rows_sync, "rose_filters", message.chat.id, "trigger"):
        trigger = str(row["trigger"])
        if re.search(rf"(?<!\w){re.escape(trigger)}(?!\w)", folded):
            await _send_payload(client, message.chat.id, json.loads(row["payload_json"]), message)
            return True
    return False

async def _info(client, message, cmd: str, arg: str) -> bool:
    if cmd == "id":
        reply = getattr(message, "reply_to_message", None)
        user = getattr(reply, "from_user", None) or getattr(message, "from_user", None)
        await message.reply_text(f"Chat ID: {message.chat.id}\nUser ID: {getattr(user, 'id', 'unknown')}" + (f"\nMessage ID: {reply.id}" if reply else ""), quote=True, parse_mode=None)
        return True
    if cmd == "info":
        user, _ = await _resolve_user(client, message, arg)
        user = user or getattr(message, "from_user", None)
        username = f"@{user.username}" if getattr(user, "username", None) else "không có"
        await message.reply_text(f"User: {_name(user)}\nID: {user.id}\nUsername: {username}", quote=True, parse_mode=None)
        return True
    if cmd == "adminlist":
        lines = ["Quản trị viên:"]
        try:
            async for member in client.get_chat_members(message.chat.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                user = getattr(member, "user", None)
                if user:
                    lines.append(f"- {_mention(user)} ({user.id})")
        except Exception as exc:
            lines.append(f"Không đọc được danh sách: {exc}")
        await message.reply_text("\n".join(lines), quote=True, parse_mode=None)
        return True
    if cmd == "report":
        reply = getattr(message, "reply_to_message", None)
        if not reply or not getattr(reply, "from_user", None):
            await message.reply_text("Reply tin cần báo cáo.", quote=True)
            return True
        lines = [f"Báo cáo từ {_mention(message.from_user)}:", f"Người bị báo cáo: {_mention(reply.from_user)}"]
        try:
            async for member in client.get_chat_members(message.chat.id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                user = getattr(member, "user", None)
                if user and not getattr(user, "is_bot", False):
                    lines.append(_mention(user))
        except Exception:
            pass
        await message.reply_text("\n".join(lines), quote=True, parse_mode=None)
        return True
    return False


async def _moderation(
    client,
    message,
    cmd: str,
    arg: str,
) -> bool:
    commands = {
        "ban",
        "unban",
        "kick",
        "mute",
        "unmute",
        "tban",
        "tmute",
        "warn",
        "warns",
        "resetwarns",
        "promote",
        "demote",
    }

    if cmd not in commands:
        return False

    if cmd == "warns":
        user, _ = await _resolve_user(
            client,
            message,
            arg,
        )

        user = (
            user
            or getattr(
                message,
                "from_user",
                None,
            )
        )

        count, reasons = await _db(
            _get_warns_sync,
            message.chat.id,
            int(
                user.id
            ),
        )

        await message.reply_text(
            "\n".join(
                [
                    f"Cảnh cáo của {_mention(user)}: {count}",
                    *[
                        f"- {item}"
                        for item in reasons[-10:]
                    ],
                ]
            ),
            quote=True,
            parse_mode=None,
        )

        return True

    if not await _require_admin(
        client,
        message,
    ):
        return True

    if cmd in {
        "tban",
        "tmute",
    }:
        user, seconds, reason = await _resolve_timed(
            client,
            message,
            arg,
        )

        if seconds is None:
            await message.reply_text(
                f"Dùng /{cmd} bằng cách reply rồi ghi "
                "10m|2h|3d|1w, hoặc "
                f"/{cmd} @user 10m lý_do.",
                quote=True,
                parse_mode=None,
            )
            return True

    else:
        user, reason = await _resolve_user(
            client,
            message,
            arg,
        )
        seconds = 0

    if user is None:
        await message.reply_text(
            "Reply, dùng @username hoặc user ID.",
            quote=True,
            parse_mode=None,
        )
        return True

    user_id = int(
        user.id
    )

    try:
        if cmd == "unban":
            await client.unban_chat_member(
                message.chat.id,
                user_id,
            )

            await cancel_timed_release(
                message.chat.id,
                user_id,
            )

            action = "đã unban"

        elif cmd == "unmute":
            await client.restrict_chat_member(
                message.chat.id,
                user_id,
                permissions=_full_permissions(),
            )

            await cancel_timed_release(
                message.chat.id,
                user_id,
            )

            action = "đã unmute"

        elif cmd == "resetwarns":
            await _db(
                _reset_warns_sync,
                message.chat.id,
                user_id,
            )

            await message.reply_text(
                f"Đã xóa cảnh cáo của {_mention(user)}.",
                quote=True,
                parse_mode=None,
            )
            return True

        elif cmd == "promote":
            await _promote_member(
                client,
                message.chat.id,
                user_id,
            )

            await cancel_timed_release(
                message.chat.id,
                user_id,
            )

            action = "đã promote"

        elif cmd == "demote":
            await _demote_member(
                client,
                message.chat.id,
                user_id,
            )

            await cancel_timed_release(
                message.chat.id,
                user_id,
            )

            action = "đã demote"

        else:
            if not await _target_ok(
                client,
                message,
                user,
            ):
                return True

            if cmd == "warn":
                count, limit = await _issue_warn(
                    client,
                    message,
                    user,
                    reason
                    or "Không nêu lý do",
                )

                if count:
                    await message.reply_text(
                        f"Đã cảnh cáo {_mention(user)}: "
                        f"{count}/{limit}.\n"
                        f"Lý do: {reason or 'Không nêu lý do'}",
                        quote=True,
                        parse_mode=None,
                    )

                return True

            action = await _action(
                client,
                message.chat.id,
                user_id,
                cmd,
                int(
                    seconds or 0
                ),
            )

            if cmd in {
                "tban",
                "tmute",
            }:
                await schedule_timed_release(
                    client,
                    message.chat.id,
                    user_id,
                    cmd,
                    int(
                        seconds
                    ),
                )

            elif cmd in {
                "ban",
                "mute",
                "kick",
            }:
                await cancel_timed_release(
                    message.chat.id,
                    user_id,
                )

        await message.reply_text(
            f"{action.capitalize()} {_mention(user)}."
            + (
                f"\nLý do: {reason}"
                if reason
                else ""
            ),
            quote=True,
            parse_mode=None,
        )

        await _log(
            client,
            message.chat.id,
            f"[Atri Rose] {action} {_name(user)} ({user.id}); "
            f"bởi {_name(message.from_user)}; "
            f"lý do: {reason or 'không có'}.",
        )

    except Exception as exc:
        await message.reply_text(
            f"Không thể thực hiện: {exc}",
            quote=True,
            parse_mode=None,
        )

    return True


async def _message_admin(client, message, cmd: str, arg: str) -> bool:
    if cmd not in {"del", "purge", "purgefrom", "pin", "unpin", "unpinall"}:
        return False
    if not await _require_admin(client, message):
        return True
    reply = getattr(message, "reply_to_message", None)
    try:
        if cmd == "del":
            if not reply:
                await message.reply_text("Reply tin cần xóa.", quote=True)
            else:
                await reply.delete()
                await message.delete()
        elif cmd in {"purge", "purgefrom"}:
            if not reply:
                await message.reply_text("Reply tin bắt đầu vùng cần purge.", quote=True)
            else:
                ids = list(range(int(reply.id), int(message.id) + 1))
                for index in range(0, len(ids), 100):
                    await client.delete_messages(message.chat.id, ids[index:index + 100])
        elif cmd == "pin":
            if not reply:
                await message.reply_text("Reply tin cần ghim.", quote=True)
            else:
                await client.pin_chat_message(message.chat.id, reply.id)
                await message.reply_text("Đã ghim tin.", quote=True)
        elif cmd == "unpin":
            if not reply:
                await message.reply_text("Reply tin cần bỏ ghim.", quote=True)
            else:
                await client.unpin_chat_message(message.chat.id, reply.id)
                await message.reply_text("Đã bỏ ghim.", quote=True)
        else:
            await client.unpin_all_chat_messages(message.chat.id)
            await message.reply_text("Đã bỏ toàn bộ tin ghim.", quote=True)
    except Exception as exc:
        await message.reply_text(f"Thao tác thất bại: {exc}", quote=True)
    return True


async def _content_commands(client, message, cmd: str, arg: str) -> bool:
    if cmd == "rules":
        rules = await _setting(message.chat.id, "rules")
        await message.reply_text(rules or "Nhóm chưa đặt nội quy.", quote=True, parse_mode=None)
        return True
    if cmd == "get":
        name = arg.strip().lstrip("#").casefold()
        row = await _db(_get_payload_sync, "rose_notes", message.chat.id, "name", name)
        if row:
            await _send_payload(client, message.chat.id, json.loads(row["payload_json"]), message)
        else:
            await message.reply_text("Không tìm thấy note.", quote=True)
        return True
    if cmd in {"notes", "filters"}:
        table, order = ("rose_notes", "name") if cmd == "notes" else ("rose_filters", "trigger")
        rows = await _db(_rows_sync, table, message.chat.id, order)
        key = "name" if cmd == "notes" else "trigger"
        await message.reply_text((cmd.title() + ":\n" + "\n".join(f"- {row[key]}" for row in rows)) if rows else f"Chưa có {cmd}.", quote=True, parse_mode=None)
        return True
    if cmd not in {"setrules", "clearrules", "save", "clear", "filter", "stop"}:
        return False
    if not await _require_admin(client, message):
        return True
    if cmd == "setrules":
        text = arg or str(getattr(getattr(message, "reply_to_message", None), "text", "") or "")
        if text.strip():
            await _set_setting(message.chat.id, "rules", text.strip())
            await message.reply_text("Đã cập nhật nội quy.", quote=True)
        else:
            await message.reply_text("Dùng /setrules nội_quy hoặc reply nội quy.", quote=True)
        return True
    if cmd == "clearrules":
        await _set_setting(message.chat.id, "rules", "")
        await message.reply_text("Đã xóa nội quy.", quote=True)
        return True
    tokens = shlex.split(arg) if arg.strip() else []
    if cmd in {"save", "filter"}:
        if not tokens:
            await message.reply_text(f"Dùng /{cmd} tên nội_dung hoặc reply nội dung.", quote=True)
            return True
        raw_key = tokens[0]
        key = raw_key.casefold().lstrip("#")
        remaining = arg[arg.find(raw_key) + len(raw_key):].strip()
        data = _payload(message, remaining)
        if not data.get("text") and not data.get("file_id"):
            await message.reply_text("Thiếu nội dung cần lưu.", quote=True)
            return True
        table, key_name = ("rose_notes", "name") if cmd == "save" else ("rose_filters", "trigger")
        await _db(_save_payload_sync, table, message.chat.id, key_name, key, json.dumps(data, ensure_ascii=False), int(message.from_user.id))
        await message.reply_text(f"Đã lưu {cmd}: {key}.", quote=True)
        return True
    key = arg.strip().casefold().lstrip("#")
    if not key:
        await message.reply_text(f"Dùng /{cmd} tên.", quote=True)
        return True
    table, key_name = ("rose_notes", "name") if cmd == "clear" else ("rose_filters", "trigger")
    deleted = await _db(_delete_sync, table, message.chat.id, key_name, key)
    await message.reply_text("Đã xóa." if deleted else "Không tìm thấy mục cần xóa.", quote=True)
    return True


async def _greetings(client, message, cmd: str, arg: str) -> bool:
    if cmd not in {"setwelcome", "welcome", "setgoodbye", "goodbye", "captcha", "captchamode", "captchatime"}:
        return False
    if cmd in {"welcome", "goodbye"} and not arg:
        enabled = _truth(await _setting(message.chat.id, f"{cmd}_enabled"))
        data = json.loads(await _setting(message.chat.id, f"{cmd}_payload"))
        await message.reply_text(f"{cmd}: {'ON' if enabled else 'OFF'}\nNội dung: {data.get('text') or '(media)'}", quote=True, parse_mode=None)
        return True
    if not await _require_admin(client, message):
        return True
    if cmd in {"setwelcome", "setgoodbye"}:
        data = _payload(message, arg)
        if not data.get("text") and not data.get("file_id"):
            await message.reply_text("Thiếu nội dung.", quote=True)
        else:
            key = "welcome_payload" if cmd == "setwelcome" else "goodbye_payload"
            await _set_setting(message.chat.id, key, json.dumps(data, ensure_ascii=False))
            await message.reply_text("Đã lưu lời chào.", quote=True)
        return True
    if cmd in {"welcome", "goodbye", "captcha"}:
        enabled = _truth(arg)
        if enabled is None:
            await message.reply_text(f"Dùng /{cmd} on hoặc off.", quote=True)
        else:
            await _set_setting(message.chat.id, f"{cmd}_enabled", "1" if enabled else "0")
            await message.reply_text(f"{cmd}: {'ON' if enabled else 'OFF'}.", quote=True)
        return True
    if cmd == "captchamode":
        if arg.strip().casefold() != "button":
            await message.reply_text("Bản self-host hiện hỗ trợ captchamode button; text/math cần dịch vụ CAPTCHA riêng.", quote=True, parse_mode=None)
        else:
            await message.reply_text("CAPTCHA mode: button.", quote=True)
        return True
    seconds = _duration(arg)
    if seconds is None:
        await message.reply_text("Dùng /captchatime 10m|2h|3d.", quote=True)
    else:
        await _set_setting(message.chat.id, "captcha_timeout", seconds)
        await message.reply_text(f"CAPTCHA timeout: {arg}.", quote=True)
    return True

def _parse_mode(value: str, default: str = "delete") -> tuple[str, int]:
    parts = value.strip().casefold().split()
    mode = parts[0] if parts else default
    seconds = _duration(parts[1]) if len(parts) > 1 else 0
    if mode not in {"delete", "warn", "mute", "tmute", "kick", "ban", "tban"}:
        mode = default
    return mode, seconds or 0


async def _locks(client, message, cmd: str, arg: str) -> bool:
    if cmd == "locktypes":
        await message.reply_text("Locktypes:\n" + ", ".join(sorted(LOCK_TYPES)), quote=True, parse_mode=None)
        return True
    if cmd == "locks":
        rows = await _db(_rows_sync, "rose_locks", message.chat.id, "lock_type")
        await message.reply_text("\n".join(f"- {row['lock_type']}: {row['mode']}" + (f" {row['seconds']}s" if row['seconds'] else "") for row in rows) or "Không có lock đang bật.", quote=True, parse_mode=None)
        return True
    if cmd == "blocklist":
        rows = await _db(_rows_sync, "rose_blocklist", message.chat.id, "item")
        await message.reply_text("Blocklist:\n" + "\n".join(f"- {row['item']}" for row in rows) if rows else "Blocklist trống.", quote=True, parse_mode=None)
        return True
    if cmd == "allowlist" and not arg:
        rows = await _db(_rows_sync, "rose_allowlist", message.chat.id, "item")
        await message.reply_text("Allowlist:\n" + "\n".join(f"- {row['item']}" for row in rows) if rows else "Allowlist trống.", quote=True, parse_mode=None)
        return True
    if cmd not in {"lock", "unlock", "lockwarns", "allowlist", "rmallowlist", "rmallowlistall", "addblocklist", "rmblocklist", "blocklistmode"}:
        return False
    if not await _require_admin(client, message):
        return True
    if cmd in {"lock", "unlock"}:
        if not arg:
            await message.reply_text(f"Dùng /{cmd} locktype ...", quote=True)
            return True
        left, _, right = arg.partition("###")
        tokens = left.split()
        lock_types = [item.casefold() for item in tokens if item.casefold() in LOCK_TYPES]
        unknown = [item for item in tokens if item.casefold() not in LOCK_TYPES]
        if unknown:
            await message.reply_text("Locktype không hợp lệ: " + ", ".join(unknown), quote=True)
            return True
        if "all" in lock_types:
            lock_types = sorted(LOCK_TYPES - {"all"})
        if cmd == "unlock":
            for lock_type in lock_types:
                await _db(_delete_sync, "rose_locks", message.chat.id, "lock_type", lock_type)
            await message.reply_text("Đã unlock: " + ", ".join(lock_types), quote=True)
            return True
        reason, mode, seconds = right.strip(), "delete", 0
        match = re.search(r"\{([^{}]+)\}\s*$", reason)
        if match:
            mode, seconds = _parse_mode(match.group(1))
            reason = reason[:match.start()].strip()
        for lock_type in lock_types:
            await _db(_insert_sync, "rose_locks", ("chat_id", "lock_type", "mode", "reason", "seconds"), (message.chat.id, lock_type, mode, reason, seconds))
        await message.reply_text("Đã lock: " + ", ".join(lock_types) + (f"\nMode: {mode}" if mode != "delete" else ""), quote=True)
        return True
    if cmd == "lockwarns":
        enabled = _truth(arg)
        if enabled is None:
            await message.reply_text("Dùng /lockwarns on hoặc off.", quote=True)
        else:
            await _set_setting(message.chat.id, "lockwarns", "1" if enabled else "0")
            await message.reply_text(f"Lock warns: {'ON' if enabled else 'OFF'}.", quote=True)
        return True
    if cmd in {"allowlist", "rmallowlist", "addblocklist", "rmblocklist"}:
        items = [item.casefold() for item in shlex.split(arg)]
        if not items:
            await message.reply_text("Thiếu mục cần xử lý.", quote=True)
            return True
        table = "rose_allowlist" if "allowlist" in cmd else "rose_blocklist"
        if cmd in {"allowlist", "addblocklist"}:
            for item in items:
                await _db(_insert_sync, table, ("chat_id", "item"), (message.chat.id, item))
        else:
            for item in items:
                await _db(_delete_sync, table, message.chat.id, "item", item)
        await message.reply_text("Đã cập nhật: " + ", ".join(items), quote=True)
        return True
    if cmd == "rmallowlistall":
        if not await _require_owner(client, message):
            return True
        def clear():
            with _connect() as db:
                cur = db.execute("DELETE FROM rose_allowlist WHERE chat_id=?", (message.chat.id,))
                db.commit()
            return max(0, cur.rowcount)
        count = await _db(clear)
        await message.reply_text(f"Đã xóa {count} mục allowlist.", quote=True)
        return True
    mode, seconds = _parse_mode(arg)
    value = mode + (f" {max(1, seconds // 60)}m" if seconds else "")
    await _set_setting(message.chat.id, "blocklist_mode", value)
    await message.reply_text(f"Blocklist mode: {value}.", quote=True)
    return True


async def _controls(client, message, cmd: str, arg: str) -> bool:
    if cmd == "flood":
        await message.reply_text(f"Flood: {await _setting(message.chat.id, 'flood_limit')} tin/{await _setting(message.chat.id, 'flood_window')}s\nMode: {await _setting(message.chat.id, 'flood_mode')}", quote=True)
        return True
    if cmd == "approved":
        rows = await _db(_rows_sync, "rose_approved", message.chat.id, "user_id")
        await message.reply_text("Approved:\n" + "\n".join(f"- {row['user_id']}" for row in rows) if rows else "Chưa có approved user.", quote=True)
        return True
    if cmd == "logchannel":
        target = await _setting(message.chat.id, "log_chat_id")
        await message.reply_text(f"Log channel: {target if target != '0' else 'OFF'}.", quote=True)
        return True
    if cmd not in {"setflood", "setfloodmode", "approve", "unapprove", "setlog", "unsetlog", "cleancommand"}:
        return False
    if not await _require_admin(client, message):
        return True
    if cmd == "setflood":
        if arg.strip().casefold() in {"off", "0"}:
            await _set_setting(message.chat.id, "flood_limit", 0)
            await message.reply_text("Flood protection: OFF.", quote=True)
            return True
        match = re.fullmatch(r"(\d+)(?:\s*/\s*(\d+)s?)?", arg.strip().casefold())
        if not match:
            await message.reply_text("Dùng /setflood 5 hoặc /setflood 5/10s.", quote=True)
            return True
        limit, window = max(2, min(100, int(match.group(1)))), max(2, min(120, int(match.group(2) or 10)))
        await _set_setting(message.chat.id, "flood_limit", limit)
        await _set_setting(message.chat.id, "flood_window", window)
        await message.reply_text(f"Flood: {limit} tin/{window}s.", quote=True)
        return True
    if cmd == "setfloodmode":
        mode, seconds = _parse_mode(arg, "mute")
        value = mode + (f" {max(1, seconds // 60)}m" if seconds else "")
        await _set_setting(message.chat.id, "flood_mode", value)
        await message.reply_text(f"Flood mode: {value}.", quote=True)
        return True
    if cmd in {"approve", "unapprove"}:
        user, _ = await _resolve_user(client, message, arg)
        if user is None:
            await message.reply_text("Reply hoặc chỉ định user.", quote=True)
            return True
        if cmd == "approve":
            await _db(_insert_sync, "rose_approved", ("chat_id", "user_id", "approved_by", "created_at"), (message.chat.id, int(user.id), int(message.from_user.id), int(time.time())))
            await message.reply_text(f"Đã approve {_mention(user)}.", quote=True)
        else:
            await _db(_delete_sync, "rose_approved", message.chat.id, "user_id", int(user.id))
            await message.reply_text(f"Đã unapprove {_mention(user)}.", quote=True)
        return True
    if cmd == "setlog":
        target = arg.strip()
        if not target.lstrip("-").isdigit():
            await message.reply_text("Dùng /setlog -100xxxxxxxxxx.", quote=True)
        else:
            await _set_setting(message.chat.id, "log_chat_id", int(target))
            await message.reply_text(f"Đã đặt log channel: {target}.", quote=True)
        return True
    if cmd == "unsetlog":
        await _set_setting(message.chat.id, "log_chat_id", 0)
        await message.reply_text("Đã tắt log channel.", quote=True)
        return True
    enabled = _truth(arg)
    if enabled is None:
        await message.reply_text("Dùng /cleancommand on hoặc off.", quote=True)
    else:
        await _set_setting(message.chat.id, "clean_commands", "1" if enabled else "0")
        await message.reply_text(f"Clean commands: {'ON' if enabled else 'OFF'}.", quote=True)
    return True


def _fed_info_sync(fed_id: str) -> dict[str, Any] | None:
    with _connect() as db:
        fed = db.execute("SELECT * FROM rose_federations WHERE fed_id=?", (fed_id,)).fetchone()
        if not fed:
            return None
        chats = db.execute("SELECT COUNT(*) FROM rose_fed_chats WHERE fed_id=?", (fed_id,)).fetchone()[0]
        bans = db.execute("SELECT COUNT(*) FROM rose_fed_bans WHERE fed_id=?", (fed_id,)).fetchone()[0]
        admins = db.execute("SELECT COUNT(*) FROM rose_fed_admins WHERE fed_id=?", (fed_id,)).fetchone()[0]
    result = dict(fed)
    result.update(chats=int(chats), bans=int(bans), admins=int(admins) + 1)
    return result


def _fed_admin_sync(fed_id: str, user_id: int) -> bool:
    with _connect() as db:
        fed = db.execute("SELECT owner_id FROM rose_federations WHERE fed_id=?", (fed_id,)).fetchone()
        if not fed:
            return False
        if int(fed["owner_id"]) == user_id:
            return True
        return db.execute("SELECT 1 FROM rose_fed_admins WHERE fed_id=? AND user_id=?", (fed_id, user_id)).fetchone() is not None


async def _federation(client, message, cmd: str, arg: str) -> bool:
    if cmd not in {"newfed", "delfed", "joinfed", "leavefed", "fedinfo", "fedadmins", "fedpromote", "feddemote", "fban", "unfban", "fbanlist"}:
        return False
    actor = int(getattr(getattr(message, "from_user", None), "id", 0) or 0)
    if cmd == "newfed":
        if not await _require_owner(client, message):
            return True
        if not arg.strip():
            await message.reply_text("Dùng /newfed tên federation.", quote=True)
            return True
        fed_id = secrets.token_hex(4)
        def create():
            with _connect() as db:
                db.execute("INSERT INTO rose_federations(fed_id,name,owner_id,created_at) VALUES(?,?,?,?)", (fed_id, arg.strip(), actor, int(time.time())))
                db.commit()
        await _db(create)
        await message.reply_text(f"Đã tạo federation {arg.strip()}\nID: {fed_id}", quote=True)
        return True
    fed = await _db(_fed_for_chat_sync, message.chat.id)
    if cmd == "joinfed":
        if not await _require_owner(client, message):
            return True
        fed_id = arg.strip().casefold()
        info = await _db(_fed_info_sync, fed_id)
        if not info:
            await message.reply_text("Không tìm thấy federation.", quote=True)
            return True
        def join():
            with _connect() as db:
                db.execute("INSERT OR REPLACE INTO rose_fed_chats(fed_id,chat_id,joined_at) VALUES(?,?,?)", (fed_id, message.chat.id, int(time.time())))
                db.commit()
        await _db(join)
        await message.reply_text(f"Đã tham gia federation {info['name']}.", quote=True)
        return True
    if cmd == "leavefed":
        if not await _require_owner(client, message):
            return True
        def leave():
            with _connect() as db:
                cur = db.execute("DELETE FROM rose_fed_chats WHERE chat_id=?", (message.chat.id,))
                db.commit()
            return max(0, cur.rowcount)
        await message.reply_text("Đã rời federation." if await _db(leave) else "Nhóm chưa ở federation.", quote=True)
        return True
    fed_id = arg.split()[0].casefold() if cmd == "fedinfo" and arg.strip() else (str(fed["fed_id"]) if fed else "")
    if not fed_id:
        await message.reply_text("Nhóm chưa tham gia federation.", quote=True)
        return True
    info = await _db(_fed_info_sync, fed_id)
    if not info:
        await message.reply_text("Federation không tồn tại.", quote=True)
        return True
    if cmd == "fedinfo":
        await message.reply_text(f"Federation: {info['name']}\nID: {info['fed_id']}\nOwner: {info['owner_id']}\nAdmins: {info['admins']}\nChats: {info['chats']}\nBans: {info['bans']}", quote=True)
        return True
    if cmd == "fbanlist":
        def list_bans():
            with _connect() as db:
                return [dict(row) for row in db.execute("SELECT * FROM rose_fed_bans WHERE fed_id=? ORDER BY created_at DESC LIMIT 100", (fed_id,)).fetchall()]
        rows = await _db(list_bans)
        await message.reply_text("\n".join(f"- {row['user_id']}: {row['reason'] or 'không lý do'}" for row in rows) or "Federation ban list trống.", quote=True)
        return True
    if cmd == "fedadmins":
        def list_admins():
            with _connect() as db:
                return [int(info["owner_id"]), *[int(row["user_id"]) for row in db.execute("SELECT user_id FROM rose_fed_admins WHERE fed_id=?", (fed_id,)).fetchall()]]
        admins = await _db(list_admins)
        await message.reply_text("Fed admins:\n" + "\n".join(f"- {item}" for item in admins), quote=True)
        return True
    if not await _db(_fed_admin_sync, fed_id, actor) and actor != int(Config.OWNER_ID):
        await message.reply_text("Chỉ federation admin được dùng lệnh này.", quote=True)
        return True
    if cmd in {"fedpromote", "feddemote"}:
        if actor not in {int(info["owner_id"]), int(Config.OWNER_ID)}:
            await message.reply_text("Chỉ federation owner được đổi fed admin.", quote=True)
            return True
        user, _ = await _resolve_user(client, message, arg)
        if user is None:
            await message.reply_text("Reply hoặc chỉ định user.", quote=True)
            return True
        def set_admin(add: bool):
            with _connect() as db:
                if add:
                    db.execute("INSERT OR IGNORE INTO rose_fed_admins(fed_id,user_id) VALUES(?,?)", (fed_id, int(user.id)))
                else:
                    db.execute("DELETE FROM rose_fed_admins WHERE fed_id=? AND user_id=?", (fed_id, int(user.id)))
                db.commit()
        await _db(set_admin, cmd == "fedpromote")
        await message.reply_text(f"Đã {'thêm' if cmd == 'fedpromote' else 'xóa'} fed admin: {_mention(user)}.", quote=True)
        return True
    if cmd in {"fban", "unfban"}:
        user, reason = await _resolve_user(client, message, arg)
        if user is None:
            await message.reply_text("Reply hoặc chỉ định user.", quote=True)
            return True
        def update(add: bool):
            with _connect() as db:
                if add:
                    db.execute("INSERT OR REPLACE INTO rose_fed_bans(fed_id,user_id,reason,banned_by,created_at) VALUES(?,?,?,?,?)", (fed_id, int(user.id), reason, actor, int(time.time())))
                else:
                    db.execute("DELETE FROM rose_fed_bans WHERE fed_id=? AND user_id=?", (fed_id, int(user.id)))
                chats = [int(row["chat_id"]) for row in db.execute("SELECT chat_id FROM rose_fed_chats WHERE fed_id=?", (fed_id,)).fetchall()]
                db.commit()
            return chats
        chats = await _db(update, cmd == "fban")
        successes = 0
        for chat_id in chats:
            try:
                if cmd == "fban": await client.ban_chat_member(chat_id, int(user.id))
                else: await client.unban_chat_member(chat_id, int(user.id))
                successes += 1
            except Exception:
                pass
        await message.reply_text(f"Đã {cmd} {_mention(user)} trên {successes}/{len(chats)} nhóm.", quote=True)
        return True
    if cmd == "delfed":
        if actor not in {int(info["owner_id"]), int(Config.OWNER_ID)}:
            await message.reply_text("Chỉ federation owner được xóa federation.", quote=True)
            return True
        if arg.strip().casefold() != "confirm":
            await message.reply_text("Dùng /delfed confirm để xác nhận.", quote=True)
            return True
        def delete():
            with _connect() as db:
                for table in ("rose_fed_admins", "rose_fed_chats", "rose_fed_bans"):
                    db.execute(f"DELETE FROM {table} WHERE fed_id=?", (fed_id,))
                db.execute("DELETE FROM rose_federations WHERE fed_id=?", (fed_id,))
                db.commit()
        await _db(delete)
        await message.reply_text("Đã xóa federation.", quote=True)
        return True
    return True

def _export_sync(chat_id: int) -> dict[str, Any]:
    result: dict[str, Any] = {"version": 1, "chat_id": chat_id}
    with _connect() as db:
        result["settings"] = {str(row["key"]): str(row["value"]) for row in db.execute("SELECT key,value FROM rose_settings WHERE chat_id=?", (chat_id,))}
        for key, table in (("notes", "rose_notes"), ("filters", "rose_filters"), ("locks", "rose_locks"), ("allowlist", "rose_allowlist"), ("blocklist", "rose_blocklist"), ("approved", "rose_approved")):
            result[key] = [dict(row) for row in db.execute(f"SELECT * FROM {table} WHERE chat_id=?", (chat_id,)).fetchall()]
    return result


def _import_sync(chat_id: int, payload: dict[str, Any]) -> None:
    with _connect() as db:
        for key, value in (payload.get("settings") or {}).items():
            db.execute("INSERT OR REPLACE INTO rose_settings(chat_id,key,value) VALUES(?,?,?)", (chat_id, str(key), str(value)))
        specs = {
            "notes": ("rose_notes", ("name", "payload_json", "created_by", "updated_at")),
            "filters": ("rose_filters", ("trigger", "payload_json", "created_by", "updated_at")),
            "locks": ("rose_locks", ("lock_type", "mode", "reason", "seconds")),
            "allowlist": ("rose_allowlist", ("item",)),
            "blocklist": ("rose_blocklist", ("item",)),
            "approved": ("rose_approved", ("user_id", "approved_by", "created_at")),
        }
        for key, (table, columns) in specs.items():
            for row in payload.get(key) or []:
                db.execute(f"INSERT OR REPLACE INTO {table}(chat_id,{','.join(columns)}) VALUES(?,{','.join('?' for _ in columns)})", [chat_id, *[row.get(column) for column in columns]])
        db.commit()


async def _backup(client, message, cmd: str, arg: str) -> bool:
    if cmd not in {"export", "import", "reset"}:
        return False
    if not await _require_owner(client, message):
        return True
    if cmd == "export":
        stream = io.BytesIO(json.dumps(await _db(_export_sync, message.chat.id), ensure_ascii=False, indent=2).encode("utf-8"))
        stream.name = f"atri-rose-{message.chat.id}.json"
        await client.send_document(message.chat.id, stream, caption="Atri Rose group configuration export.")
        return True
    if cmd == "import":
        reply = getattr(message, "reply_to_message", None)
        if not getattr(reply, "document", None):
            await message.reply_text("Reply file JSON export rồi dùng /import.", quote=True)
            return True
        try:
            downloaded = await reply.download(in_memory=True)
            raw = downloaded.getvalue() if hasattr(downloaded, "getvalue") else bytes(downloaded)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or int(payload.get("version", 0)) != 1:
                raise ValueError("Unsupported export version")
            await _db(_import_sync, message.chat.id, payload)
            await message.reply_text("Đã import cấu hình.", quote=True)
        except Exception as exc:
            await message.reply_text(f"Import thất bại: {exc}", quote=True)
        return True
    if arg.strip().casefold() != "confirm":
        await message.reply_text("Lệnh này xóa cấu hình quản trị nhóm. Dùng /reset confirm.", quote=True)
        return True
    def reset():
        with _connect() as db:
            for table in ("rose_settings", "rose_warnings", "rose_notes", "rose_filters", "rose_locks", "rose_allowlist", "rose_blocklist", "rose_approved", "rose_captchas"):
                db.execute(f"DELETE FROM {table} WHERE chat_id=?", (message.chat.id,))
            db.commit()
    await _db(reset)
    await message.reply_text("Đã reset cấu hình Atri Rose của nhóm.", quote=True)
    return True


async def _help(message) -> None:
    await message.reply_text(
        "Atri Rose Core V1\n\n"
        "Moderation: /ban /unban /kick /mute /unmute /tban /tmute /warn /warns /resetwarns /promote /demote\n"
        "Messages: /del /purge /pin /unpin /unpinall\n"
        "Rules & notes: /setrules /rules /save /get /notes /filter /filters\n"
        "Welcome: /setwelcome /welcome /setgoodbye /goodbye /captcha\n"
        "Anti-spam: /lock /unlock /locks /locktypes /addblocklist /blocklist /setflood /setfloodmode\n"
        "Approvals & logs: /approve /approved /setlog /logchannel\n"
        "Federation: /newfed /joinfed /fedinfo /fban /unfban /fbanlist\n"
        "Backup: /export /import /reset\n\n"
        "Hỗ trợ reply, @username hoặc user ID. Thời gian: 10m, 2h, 3d, 1w.",
        quote=True,
        parse_mode=None,
        disable_web_page_preview=True,
    )


async def _dispatch(client, message, cmd: str, arg: str) -> bool:
    if cmd in {"rosehelp", "modhelp"}:
        await _help(message)
        return True
    for handler in (_info, _moderation, _message_admin, _content_commands, _greetings, _locks, _controls, _federation, _backup):
        if await handler(client, message, cmd, arg):
            return True
    return False


async def atri_rose_message(client, message) -> None:
    try:
        await _ensure_db()
        text = str(getattr(message, "text", "") or getattr(message, "caption", "") or "").strip()
        if await _member_event(client, message):
            return
        cmd, arg = _command(text)
        if cmd in COMMANDS and await _dispatch(client, message, cmd, arg):
            if _truth(await _setting(message.chat.id, "clean_commands")):
                try:
                    await message.delete()
                except Exception:
                    pass
            message.stop_propagation()
            return
        if await _passive(client, message, text):
            message.stop_propagation()
            return
        if await _triggers(client, message, text):
            message.stop_propagation()
    except StopPropagation:
        raise
    except Exception:
        LOGGER.exception("Atri Rose handler failed chat=%s message=%s", getattr(getattr(message, "chat", None), "id", None), getattr(message, "id", None))


def add_atri_rose_handlers(client) -> None:
    client.add_handler(
        MessageHandler(
            atri_rose_message,
            filters=(
                filters.incoming
                & filters.group
            ),
        ),
        group=-30,
    )

    client.add_handler(
        CallbackQueryHandler(
            rose_callback,
            filters=filters.regex(
                r"^rose_captcha:"
            ),
        ),
        group=-30,
    )
