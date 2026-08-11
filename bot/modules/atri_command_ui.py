from __future__ import annotations

import ast
import asyncio
import os
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from pyrogram import enums, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import LOGGER
from bot.core.config_manager import Config
from bot.helper.telegram_helper.bot_commands import BotCommands


# ============================================================
# Atri Command Center V2
# - Discover commands from the LIVE local source tree.
# - Do not execute arbitrary commands from inline buttons.
# - Existing handlers remain the authority for permissions/actions.
# - Notes are private and stored in a dedicated SQLite DB.
# ============================================================

ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = ROOT / "bot" / "modules"
DB_PATH = Path(
    os.getenv(
        "ATRI_PERSONAL_NOTES_DB",
        "/app/atri_data/atri_personal_notes.sqlite3",
    )
)

PAGE_SIZE = 12
SEARCH_LIMIT = 24
MAX_NOTES_PER_USER = 200
MAX_NOTE_CHARS = 1000
NOTES_PAGE_SIZE = 10


def _cmd(base: str) -> str:
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
    return f"{base}{suffix}"


MENU_COMMANDS = [_cmd("menu"), _cmd("amenu")]
SEARCH_COMMAND = _cmd("commands")
DETAIL_COMMAND = _cmd("cmd")
ADD_NOTE_COMMAND = _cmd("anote")
LIST_NOTES_COMMAND = _cmd("anotes")
CLEAR_NOTES_COMMAND = _cmd("aclearnotes")


CATEGORY_META: dict[str, tuple[str, str]] = {
    "core": ("🏠 Cơ bản", "Lệnh khởi động, trạng thái và cài đặt người dùng"),
    "mirror": ("☁️ Mirror", "Tải rồi upload lên cloud"),
    "leech": ("📤 Leech", "Tải rồi gửi lên Telegram"),
    "cloud": ("🗂 Cloud & Search", "Drive, tìm file, torrent và NZB"),
    "tasks": ("⏱ Tác vụ", "Theo dõi, hủy và điều khiển tác vụ"),
    "atri": ("🤖 Atri AI", "Chat, web, tools, code và tiện ích Atri"),
    "rose_mod": ("🛡 Rose • Quản trị", "Moderation và quản trị thành viên"),
    "rose_content": ("📝 Rose • Nội dung", "Rules, notes, filters, welcome và nội dung nhóm"),
    "rose_guard": ("🔒 Rose • Bảo vệ", "Locks, blocklist, flood, captcha và approvals"),
    "rose_fed": ("🌐 Rose • Federation", "Federation và federation ban"),
    "rose_backup": ("💾 Rose • Dữ liệu", "Export, import, reset và housekeeping"),
    "admin": ("⚙️ Bot Admin", "Lệnh sudo/owner và quản trị bot"),
    "other": ("🧩 Khác", "Các lệnh còn lại được phát hiện từ source"),
}


CATEGORY_ORDER = [
    "core",
    "mirror",
    "leech",
    "cloud",
    "tasks",
    "atri",
    "rose_mod",
    "rose_content",
    "rose_guard",
    "rose_fed",
    "rose_backup",
    "admin",
    "other",
]


CORE_COMMANDS = {
    "start", "help", "ping", "speedtest", "speed",
    "stats", "status", "usetting", "us", "rss",
}

MIRROR_COMMANDS = {
    "mirror", "m", "qbmirror", "qm", "jdmirror", "jm",
    "ytdl", "y", "gallerydl", "gdl", "nzbmirror", "nm",
}

LEECH_COMMANDS = {
    "leech", "l", "qbleech", "ql", "jdleech", "jl",
    "ytdlleech", "yl", "gallerydlleech", "gdlleech",
    "nzbleech", "nl",
}

CLOUD_COMMANDS = {
    "clone", "count", "del", "list", "search", "nzbsearch",
}

TASK_COMMANDS = {
    "cancel", "c", "cancelall", "forcestart", "fs", "sel",
}

ADMIN_COMMANDS = {
    "auth", "unauth", "addsudo", "rmsudo", "bsetting", "bs",
    "users", "restart", "log", "shell", "exec", "aexec",
    "clearlocals",
    
    
}

ROSE_MOD_COMMANDS = {
    "id", "info", "adminlist", "report",
    "ban", "unban", "kick", "mute", "unmute", "tban", "tmute",
    "warn", "warns", "resetwarns", "promote", "demote",
    "del", "purge", "purgefrom", "pin", "unpin", "unpinall",
}

ROSE_CONTENT_COMMANDS = {
    "rosehelp", "modhelp",
    "setrules", "rules", "clearrules",
    "save", "get", "clear", "notes",
    "filter", "filters", "stop",
    "setwelcome", "welcome", "setgoodbye", "goodbye",
}

ROSE_GUARD_COMMANDS = {
    "captcha", "captchamode", "captchatime",
    "lock", "unlock", "locks", "locktypes", "lockwarns",
    "allowlist", "rmallowlist", "rmallowlistall",
    "addblocklist", "blocklist", "rmblocklist", "blocklistmode",
    "setflood", "flood", "setfloodmode",
    "approve", "unapprove", "approved",
    "setlog", "unsetlog", "logchannel",
}

ROSE_FED_COMMANDS = {
    "newfed", "delfed", "joinfed", "leavefed",
    "fedinfo", "fedadmins", "fedpromote", "feddemote",
    "fban", "unfban", "fbanlist",
}

ROSE_BACKUP_COMMANDS = {
    "export", "import", "reset", "cleancommand",
}


OWNER_COMMANDS = {
    "shell", "exec", "aexec", "clearlocals",
    
    
}

SUDO_COMMANDS = {
    "auth", "unauth", "bsetting", "bs",
    "users", "restart", "log",
    
}

ADMIN_GROUP_COMMANDS = (
    ROSE_MOD_COMMANDS
    | ROSE_CONTENT_COMMANDS
    | ROSE_GUARD_COMMANDS
    | ROSE_FED_COMMANDS
    | ROSE_BACKUP_COMMANDS
)


DESCRIPTIONS: dict[str, str] = {
    "start": "Khởi động bot và mở giao diện ban đầu.",
    "help": "Xem hướng dẫn sử dụng bot.",
    "ping": "Kiểm tra độ trễ phản hồi.",
    "speedtest": "Kiểm tra tốc độ mạng của VPS.",
    "stats": "Xem thống kê bot và tài nguyên hệ thống.",
    "status": "Xem trạng thái tác vụ đang chạy/chờ.",
    "usetting": "Mở cài đặt cá nhân.",
    "rss": "Quản lý nguồn RSS.",

    "mirror": "Tải bằng aria2 rồi upload lên cloud.",
    "qbmirror": "Tải torrent/magnet bằng qBittorrent rồi upload.",
    "jdmirror": "Tải bằng JDownloader rồi upload.",
    "ytdl": "Tải video/âm thanh bằng yt-dlp rồi upload.",
    "gallerydl": "Tải album bằng gallery-dl rồi upload.",
    "nzbmirror": "Tải NZB/Usenet rồi upload.",

    "leech": "Tải rồi gửi file lên Telegram.",
    "qbleech": "Tải torrent/magnet rồi gửi lên Telegram.",
    "jdleech": "Tải bằng JDownloader rồi gửi lên Telegram.",
    "ytdlleech": "Tải video/âm thanh rồi gửi lên Telegram.",
    "gallerydlleech": "Tải album rồi gửi lên Telegram.",
    "nzbleech": "Tải NZB/Usenet rồi gửi lên Telegram.",

    "clone": "Sao chép file/thư mục Google Drive.",
    "count": "Đếm file, thư mục và dung lượng Drive.",
    "del": "Xóa file/thư mục cloud; Rose cũng có /del theo ngữ cảnh quản trị.",
    "list": "Tìm file trong Google Drive.",
    "search": "Tìm torrent.",
    "nzbsearch": "Tìm nội dung NZB/Usenet.",

    "cancel": "Hủy một tác vụ.",
    "cancelall": "Mở menu hủy nhiều tác vụ.",
    "forcestart": "Ép tác vụ trong hàng đợi bắt đầu.",
    "sel": "Chọn file trong torrent.",


    "ai": "Hỏi Atri trực tiếp.",
    "atri": "Xem/bật/tắt cấu hình Atri theo chat.",
    "resetai": "Xóa/reset hội thoại Atri theo handler hiện tại.",


    "auth": "Cấp quyền Authorized.",
    "unauth": "Gỡ quyền Authorized.",
    "addsudo": "Thêm tài khoản sudo.",
    "rmsudo": "Gỡ tài khoản sudo.",
    "bsetting": "Mở cài đặt toàn bot.",
    "users": "Quản lý dữ liệu/cài đặt người dùng.",
    "restart": "Khởi động lại bot.",
    "log": "Lấy log bot.",
    "shell": "Thực thi shell; quyền hệ thống cao.",
    "exec": "Thực thi Python đồng bộ; quyền hệ thống cao.",
    "aexec": "Thực thi Python async; quyền hệ thống cao.",
    "clearlocals": "Xóa local state của trình thực thi.",

    "ban": "Cấm thành viên nhóm.",
    "unban": "Gỡ cấm thành viên.",
    "kick": "Đá thành viên khỏi nhóm.",
    "mute": "Tắt quyền nhắn của thành viên.",
    "unmute": "Bỏ mute.",
    "tban": "Ban có thời hạn.",
    "tmute": "Mute có thời hạn.",
    "warn": "Cảnh cáo thành viên.",
    "warns": "Xem cảnh cáo.",
    "resetwarns": "Xóa cảnh cáo.",
    "promote": "Cấp quyền admin.",
    "demote": "Gỡ quyền admin.",
    "purge": "Xóa một dải tin nhắn.",
    "pin": "Ghim tin nhắn.",
    "unpin": "Bỏ ghim.",
    "rules": "Xem nội quy nhóm.",
    "setrules": "Đặt nội quy nhóm.",
    "notes": "Xem notes Rose của nhóm.",
    "filter": "Tạo filter tự động.",
    "filters": "Xem danh sách filter.",
    "setwelcome": "Đặt lời chào.",
    "welcome": "Bật/tắt/xem lời chào.",
    "captcha": "Cấu hình CAPTCHA.",
    "lock": "Khóa một loại nội dung.",
    "unlock": "Mở khóa một loại nội dung.",
    "blocklist": "Xem blocklist.",
    "setflood": "Đặt ngưỡng chống flood.",
    "approve": "Approve thành viên.",
    "newfed": "Tạo federation.",
    "fban": "Federation-ban người dùng.",
    "export": "Xuất dữ liệu Rose.",
    "import": "Nhập dữ liệu Rose.",
}


USAGE: dict[str, str] = {
    "mirror": "/mirror <URL>",
    "qbmirror": "/qbmirror <magnet|torrent|URL>",
    "jdmirror": "/jdmirror <URL>",
    "ytdl": "/ytdl <URL>",
    "gallerydl": "/gallerydl <URL>",
    "nzbmirror": "/nzbmirror <NZB|URL>",
    "leech": "/leech <URL>",
    "qbleech": "/qbleech <magnet|torrent|URL>",
    "jdleech": "/jdleech <URL>",
    "ytdlleech": "/ytdlleech <URL>",
    "gallerydlleech": "/gallerydlleech <URL>",
    "nzbleech": "/nzbleech <NZB|URL>",
    "clone": "/clone <link>",
    "count": "/count <link>",
    "del": "/del <link> hoặc reply theo handler",
    "list": "/list <từ_khóa>",
    "search": "/search <từ_khóa>",
    "nzbsearch": "/nzbsearch <từ_khóa>",

    "ai": "/ai <câu_hỏi>",
    "atri": "/atri [on|off]",
    "auth": "/auth <user_id|chat_id>",
    "unauth": "/unauth <user_id|chat_id>",
    "addsudo": "/addsudo <user_id>",
    "rmsudo": "/rmsudo <user_id>",
    "shell": "/shell <lệnh>",
    "exec": "/exec <Python>",
    "aexec": "/aexec <Python async>",
    "ban": "/ban [reply|@user|ID] [lý_do]",
    "unban": "/unban [reply|@user|ID]",
    "kick": "/kick [reply|@user|ID]",
    "mute": "/mute [reply|@user|ID]",
    "unmute": "/unmute [reply|@user|ID]",
    "tban": "/tban [reply|@user|ID] <10m|2h|3d>",
    "tmute": "/tmute [reply|@user|ID] <10m|2h|3d>",
    "warn": "/warn [reply|@user|ID] [lý_do]",
    "promote": "/promote [reply|@user|ID]",
    "demote": "/demote [reply|@user|ID]",
    "setrules": "/setrules <nội_dung>",
    "save": "/save <tên> <nội_dung>",
    "get": "/get <tên>",
    "filter": "/filter <trigger> <nội_dung>",
    "stop": "/stop <trigger>",
    "setwelcome": "/setwelcome <nội_dung>",
    "setgoodbye": "/setgoodbye <nội_dung>",
    "lock": "/lock <locktype> [mode]",
    "unlock": "/unlock <locktype>",
    "addblocklist": "/addblocklist <từ>",
    "rmblocklist": "/rmblocklist <từ>",
    "setflood": "/setflood <số_lượng>",
    "fban": "/fban [reply|@user|ID] [lý_do]",
    "unfban": "/unfban [reply|@user|ID]",
}


def _strip_suffix(name: str) -> str:
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
    if suffix and name.endswith(suffix):
        return name[: -len(suffix)]
    return name


def _normalize_command(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = [x for x in value if isinstance(x, str)]
    else:
        return []

    result: list[str] = []
    for raw in values:
        command = raw.strip().lstrip("/")
        if not command:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]{1,48}", command):
            continue
        if command not in result:
            result.append(command)
    return result


def _ast_literal_commands(node: ast.AST) -> list[str]:
    try:
        value = ast.literal_eval(node)
    except Exception:
        return []
    return _normalize_command(value)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _scan_source_commands() -> dict[str, set[str]]:
    # ATRI_COMMAND_SCANNER_V3
    found: dict[str, set[str]] = defaultdict(set)

    candidates = list(MODULE_DIR.glob("*.py"))
    candidates.append(
        ROOT / "bot" / "core" / "handlers.py"
    )

    for path in candidates:
        if not path.is_file():
            continue

        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(
                source,
                filename=str(path),
            )
        except Exception:
            continue

        rel = str(path.relative_to(ROOT))
        constants: dict[str, list[str]] = {}

        for item in tree.body:
            targets: list[ast.AST] = []
            value: ast.AST | None = None

            if isinstance(item, ast.Assign):
                targets = list(item.targets)
                value = item.value
            elif isinstance(item, ast.AnnAssign):
                targets = [item.target]
                value = item.value

            if value is None:
                continue

            literal = _ast_literal_commands(value)
            if not literal:
                continue

            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = literal

        def resolve(node: ast.AST) -> list[str]:
            literal = _ast_literal_commands(node)
            if literal:
                return literal

            if isinstance(node, ast.Name):
                return list(constants.get(node.id, []))

            if isinstance(node, ast.Call):
                call = _call_name(node.func)

                if call.endswith("command_name") and node.args:
                    return resolve(node.args[0])

            return []

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets: list[ast.AST]
                value: ast.AST | None

                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                    value = node.value
                else:
                    targets = [node.target]
                    value = node.value

                if value is None:
                    continue

                names = {
                    target.id
                    for target in targets
                    if isinstance(target, ast.Name)
                }

                if names & {
                    "COMMANDS",
                    "MENU_COMMANDS",
                    "COMMAND_LIST",
                    "SUPPORTED_COMMANDS",
                }:
                    for command in resolve(value):
                        found[command].add(rel)

            if not isinstance(node, ast.Call):
                continue

            name = _call_name(node.func)

            if name.endswith(".command") or name == "command":
                if node.args:
                    for command in resolve(node.args[0]):
                        found[command].add(rel)

            if name in {"_message", "message"} and len(node.args) >= 2:
                for command in resolve(node.args[1]):
                    found[command].add(rel)

            if (
                name in {"_matches_command", "matches_command"}
                and len(node.args) >= 2
            ):
                for command in resolve(node.args[1]):
                    found[command].add(rel)

    return found

def _botcommands_catalog() -> tuple[
    dict[str, set[str]],
    dict[str, list[str]],
]:
    sources: dict[str, set[str]] = defaultdict(set)
    aliases: dict[str, list[str]] = {}

    for attr, value in vars(BotCommands).items():
        if attr.startswith("_"):
            continue

        names = _normalize_command(value)
        if not names:
            continue

        primary = names[0]
        aliases[primary] = names[1:]

        for command in names:
            sources[command].add(
                "bot/helper/telegram_helper/bot_commands.py"
            )

    return sources, aliases


def _category_for(
    command_with_suffix: str,
    sources: set[str],
) -> str:
    command = _strip_suffix(command_with_suffix)

    # Explicit command families win over file-name heuristics.
    if command in MIRROR_COMMANDS:
        return "mirror"
    if command in LEECH_COMMANDS:
        return "leech"
    if command in CLOUD_COMMANDS:
        return "cloud"
    if command in TASK_COMMANDS:
        return "tasks"
    if command in ADMIN_COMMANDS:
        return "admin"
    if command in CORE_COMMANDS:
        return "core"

    if command in ROSE_MOD_COMMANDS:
        return "rose_mod"
    if command in ROSE_CONTENT_COMMANDS:
        return "rose_content"
    if command in ROSE_GUARD_COMMANDS:
        return "rose_guard"
    if command in ROSE_FED_COMMANDS:
        return "rose_fed"
    if command in ROSE_BACKUP_COMMANDS:
        return "rose_backup"

    joined = " ".join(sorted(sources)).casefold()

    if "atri_rose" in joined:
        return "rose_mod"
    if "atri_" in joined:
        return "atri"

    return "other"


def _permission_for(command_with_suffix: str) -> str:
    command = _strip_suffix(command_with_suffix)

    if command in OWNER_COMMANDS:
        return "Owner"
    if command in SUDO_COMMANDS:
        return "Sudo"
    if command in ADMIN_GROUP_COMMANDS:
        return "Admin nhóm / theo handler"
    if command == "start":
        return "Công khai"
    return "Authorized / theo handler"


def _build_catalog() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[str]],
]:
    bot_sources, aliases_by_primary = _botcommands_catalog()
    scanned = _scan_source_commands()

    sources: dict[str, set[str]] = defaultdict(set)

    for command, items in bot_sources.items():
        sources[command].update(items)

    for command, items in scanned.items():
        sources[command].update(items)

    # Command Center commands themselves.
    own_commands = [
        *MENU_COMMANDS,
        SEARCH_COMMAND,
        DETAIL_COMMAND,
        ADD_NOTE_COMMAND,
        LIST_NOTES_COMMAND,
        CLEAR_NOTES_COMMAND,
    ]

    for command in own_commands:
        sources[command].add("bot/modules/atri_command_ui.py")

    # De-duplicate aliases: show primary command as the card/button,
    # aliases are displayed inside its detail screen.
    alias_to_primary: dict[str, str] = {}

    for primary, aliases in aliases_by_primary.items():
        for alias in aliases:
            alias_to_primary[alias] = primary

    catalog: dict[str, dict[str, Any]] = {}

    for command in sorted(sources):
        if command in alias_to_primary:
            continue

        base = _strip_suffix(command)
        category = _category_for(command, sources[command])

        aliases = [
            alias
            for alias in aliases_by_primary.get(command, [])
            if alias != command
        ]

        catalog[command] = {
            "name": command,
            "base": base,
            "aliases": aliases,
            "category": category,
            "permission": _permission_for(command),
            "description": DESCRIPTIONS.get(
                base,
                "Lệnh được phát hiện trực tiếp từ source hiện tại của bot.",
            ),
            "usage": USAGE.get(base, f"/{command}"),
            "sources": sorted(sources[command]),
        }

    categories: dict[str, list[str]] = defaultdict(list)

    for command, item in catalog.items():
        categories[item["category"]].append(command)

    for key in categories:
        categories[key].sort(
            key=lambda x: _strip_suffix(x).casefold()
        )

    return catalog, dict(categories)


CATALOG, CATEGORIES = _build_catalog()


# =========================
# Private notes
# =========================

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _init_notes_sync() -> None:
    with closing(_connect()) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS atri_personal_notes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_atri_personal_notes_user_id
            ON atri_personal_notes(user_id, id DESC);
            """
        )
        db.commit()


def _add_note_sync(user_id: int, text: str) -> int:
    now = int(time.time())

    with closing(_connect()) as db:
        cursor = db.execute(
            """
            INSERT INTO atri_personal_notes(user_id, text, created_at)
            VALUES(?, ?, ?)
            """,
            (user_id, text, now),
        )
        note_id = int(cursor.lastrowid)

        db.execute(
            """
            DELETE FROM atri_personal_notes
            WHERE user_id = ?
              AND id NOT IN (
                  SELECT id
                  FROM atri_personal_notes
                  WHERE user_id = ?
                  ORDER BY id DESC
                  LIMIT ?
              )
            """,
            (user_id, user_id, MAX_NOTES_PER_USER),
        )
        db.commit()

    return note_id


def _list_notes_sync(user_id: int) -> list[dict[str, Any]]:
    with closing(_connect()) as db:
        rows = db.execute(
            """
            SELECT id, text, created_at
            FROM atri_personal_notes
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, NOTES_PAGE_SIZE),
        ).fetchall()

    return [dict(row) for row in rows]


def _count_notes_sync(user_id: int) -> int:
    with closing(_connect()) as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total
            FROM atri_personal_notes
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    return int(row["total"] if row else 0)


def _clear_notes_sync(user_id: int) -> int:
    with closing(_connect()) as db:
        cursor = db.execute(
            "DELETE FROM atri_personal_notes WHERE user_id = ?",
            (user_id,),
        )
        db.commit()
        return max(0, int(cursor.rowcount or 0))


async def _db(function, *args):
    return await asyncio.to_thread(function, *args)


def _is_private(message) -> bool:
    chat = getattr(message, "chat", None)
    return bool(
        chat is not None
        and getattr(chat, "type", None) == enums.ChatType.PRIVATE
    )


def _callback_owner_ok(query, owner_id: int) -> bool:
    user = getattr(query, "from_user", None)
    return bool(user is not None and int(user.id) == int(owner_id))


def _cb(owner_id: int, *parts: object) -> str:
    return "acui:" + ":".join(
        [str(owner_id), *(str(x) for x in parts)]
    )


def _rows(
    buttons: list[InlineKeyboardButton],
    width: int = 2,
) -> list[list[InlineKeyboardButton]]:
    return [
        buttons[i:i + width]
        for i in range(0, len(buttons), width)
    ]


def _main_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []

    for key in CATEGORY_ORDER:
        commands = CATEGORIES.get(key, [])
        if not commands:
            continue

        label, _ = CATEGORY_META[key]
        buttons.append(
            InlineKeyboardButton(
                f"{label} · {len(commands)}",
                callback_data=_cb(owner_id, "cat", key, 0),
            )
        )

    rows = _rows(buttons, 2)
    rows.append(
        [
            InlineKeyboardButton(
                "🔎 Tìm lệnh",
                callback_data=_cb(owner_id, "searchhelp"),
            ),
            InlineKeyboardButton(
                "📝 Notes",
                callback_data=_cb(owner_id, "notes"),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "🔄 Làm mới catalog",
                callback_data=_cb(owner_id, "refresh"),
            )
        ]
    )

    return InlineKeyboardMarkup(rows)


def _main_text() -> str:
    total = len(CATALOG)
    active_categories = sum(
        1
        for key in CATEGORY_ORDER
        if CATEGORIES.get(key)
    )

    return (
        "Atri • Command Center\n\n"
        f"Đã phát hiện {total} lệnh từ source hiện tại.\n"
        f"Phân loại thành {active_categories} nhóm.\n\n"
        "Menu này chỉ là giao diện tra cứu/chọn lệnh; "
        "quyền thực thi vẫn do handler gốc kiểm soát.\n\n"
        f"Tìm nhanh: /{SEARCH_COMMAND} <từ_khóa>"
    )


def _category_view(
    owner_id: int,
    key: str,
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    commands = CATEGORIES.get(key, [])
    label, description = CATEGORY_META.get(
        key,
        CATEGORY_META["other"],
    )

    pages = max(1, (len(commands) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))

    start = page * PAGE_SIZE
    subset = commands[start:start + PAGE_SIZE]

    buttons = [
        InlineKeyboardButton(
            f"/{command}",
            callback_data=_cb(
                owner_id,
                "cmd",
                command,
                key,
                page,
            ),
        )
        for command in subset
    ]

    rows = _rows(buttons, 2)

    nav: list[InlineKeyboardButton] = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=_cb(
                    owner_id,
                    "cat",
                    key,
                    page - 1,
                ),
            )
        )

    nav.append(
        InlineKeyboardButton(
            f"{page + 1}/{pages}",
            callback_data=_cb(
                owner_id,
                "noop",
            ),
        )
    )

    if page + 1 < pages:
        nav.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=_cb(
                    owner_id,
                    "cat",
                    key,
                    page + 1,
                ),
            )
        )

    rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                "🏠 Command Center",
                callback_data=_cb(owner_id, "main"),
            )
        ]
    )

    text = (
        f"{label}\n\n"
        f"{description}\n"
        f"Tổng: {len(commands)} lệnh."
    )

    return text, InlineKeyboardMarkup(rows)


def _command_view(
    owner_id: int,
    command: str,
    back_key: str = "other",
    back_page: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    item = CATALOG.get(command)

    if item is None:
        return (
            "Không tìm thấy lệnh này trong catalog hiện tại.",
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🏠 Command Center",
                            callback_data=_cb(owner_id, "main"),
                        )
                    ]
                ]
            ),
        )

    aliases = item["aliases"]
    aliases_text = (
        ", ".join(f"/{x}" for x in aliases)
        if aliases
        else "Không"
    )

    source_text = ", ".join(
        Path(x).name
        for x in item["sources"][:4]
    )

    text = (
        f"/{item['name']}\n\n"
        f"{item['description']}\n\n"
        f"Cú pháp: {item['usage']}\n"
        f"Alias: {aliases_text}\n"
        f"Quyền: {item['permission']}\n"
        f"Nguồn: {source_text or 'runtime catalog'}\n\n"
        "Inline UI không tự chạy lệnh này để tránh kích hoạt "
        "nhầm các lệnh xóa, restart, moderation hoặc shell."
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Danh mục",
                    callback_data=_cb(
                        owner_id,
                        "cat",
                        back_key,
                        back_page,
                    ),
                ),
                InlineKeyboardButton(
                    "🏠 Menu",
                    callback_data=_cb(owner_id, "main"),
                ),
            ]
        ]
    )

    return text, keyboard


def _search_results(
    owner_id: int,
    query: str,
) -> tuple[str, InlineKeyboardMarkup]:
    needle = query.casefold().strip()

    matches: list[str] = []

    for command, item in CATALOG.items():
        haystack = " ".join(
            [
                command,
                item["base"],
                " ".join(item["aliases"]),
                item["description"],
                CATEGORY_META.get(
                    item["category"],
                    CATEGORY_META["other"],
                )[0],
            ]
        ).casefold()

        if needle in haystack:
            matches.append(command)

    matches.sort(
        key=lambda x: (
            0 if _strip_suffix(x).casefold().startswith(needle) else 1,
            _strip_suffix(x).casefold(),
        )
    )

    matches = matches[:SEARCH_LIMIT]

    if not matches:
        return (
            f"Không tìm thấy lệnh khớp với: {query}",
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🏠 Command Center",
                            callback_data=_cb(owner_id, "main"),
                        )
                    ]
                ]
            ),
        )

    buttons = [
        InlineKeyboardButton(
            f"/{command}",
            callback_data=_cb(
                owner_id,
                "cmd",
                command,
                CATALOG[command]["category"],
                0,
            ),
        )
        for command in matches
    ]

    rows = _rows(buttons, 2)
    rows.append(
        [
            InlineKeyboardButton(
                "🏠 Command Center",
                callback_data=_cb(owner_id, "main"),
            )
        ]
    )

    return (
        f"🔎 Kết quả cho: {query}\n"
        f"Tìm thấy {len(matches)} lệnh phù hợp.",
        InlineKeyboardMarkup(rows),
    )


def _notes_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Cách thêm",
                    callback_data=_cb(owner_id, "noteadd"),
                ),
                InlineKeyboardButton(
                    "🔄 Làm mới",
                    callback_data=_cb(owner_id, "notes"),
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗑 Xóa tất cả",
                    callback_data=_cb(owner_id, "noteclear"),
                ),
                InlineKeyboardButton(
                    "🏠 Menu",
                    callback_data=_cb(owner_id, "main"),
                ),
            ],
        ]
    )


def _note_clear_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Xóa hết",
                    callback_data=_cb(owner_id, "noteclear_yes"),
                ),
                InlineKeyboardButton(
                    "❌ Hủy",
                    callback_data=_cb(owner_id, "notes"),
                ),
            ]
        ]
    )


def _format_note_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(
            int(value)
        ).strftime("%H:%M %d/%m")
    except Exception:
        return "--:-- --/--"


async def _notes_view(
    owner_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    rows, total = await asyncio.gather(
        _db(_list_notes_sync, owner_id),
        _db(_count_notes_sync, owner_id),
    )

    if not rows:
        return (
            "📝 Notes cá nhân\n\n"
            "Chưa có ghi chú nào.\n\n"
            f"Dùng /{ADD_NOTE_COMMAND} <nội_dung> để thêm.",
            _notes_keyboard(owner_id),
        )

    lines = [f"📝 Notes cá nhân • {total} mục"]

    for index, row in enumerate(rows, start=1):
        value = " ".join(
            str(row.get("text") or "").split()
        )

        if len(value) > 240:
            value = value[:237] + "..."

        lines.append(
            f"{index}. {value}\n"
            f"   {_format_note_time(row.get('created_at'))}"
        )

    if total > len(rows):
        lines.append(
            f"Đang hiện {len(rows)}/{total} note mới nhất."
        )

    return "\n\n".join(lines), _notes_keyboard(owner_id)


async def _edit(query, text: str, keyboard) -> None:
    message = getattr(query, "message", None)

    if message is None:
        return

    try:
        await message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=None,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        if "MESSAGE_NOT_MODIFIED" in str(exc).upper():
            return

        LOGGER.warning(
            "Atri Command Center edit failed: %s",
            exc,
        )


# =========================
# Commands
# =========================

async def command_center(_, message) -> None:
    owner_id = int(message.from_user.id)

    await message.reply_text(
        _main_text(),
        reply_markup=_main_keyboard(owner_id),
        quote=True,
        parse_mode=None,
        disable_web_page_preview=True,
    )

    message.stop_propagation()


async def command_search(_, message) -> None:
    owner_id = int(message.from_user.id)
    raw = str(getattr(message, "text", "") or "").strip()
    parts = raw.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            f"Dùng /{SEARCH_COMMAND} <từ_khóa>\n"
            f"Ví dụ: /{SEARCH_COMMAND} boss",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    text, keyboard = _search_results(
        owner_id,
        parts[1].strip(),
    )

    await message.reply_text(
        text,
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
    )

    message.stop_propagation()


async def command_detail(_, message) -> None:
    owner_id = int(message.from_user.id)
    raw = str(getattr(message, "text", "") or "").strip()
    parts = raw.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            f"Dùng /{DETAIL_COMMAND} <tên_lệnh>\n"
            f"Ví dụ: /{DETAIL_COMMAND} mirror",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    query = parts[1].strip().lstrip("/")
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")

    candidates = [query]

    if suffix and not query.endswith(suffix):
        candidates.append(query + suffix)

    command = next(
        (x for x in candidates if x in CATALOG),
        "",
    )

    if not command:
        # Resolve aliases.
        for primary, item in CATALOG.items():
            if query in item["aliases"]:
                command = primary
                break

    if not command:
        text, keyboard = _search_results(
            owner_id,
            query,
        )
    else:
        item = CATALOG[command]
        text, keyboard = _command_view(
            owner_id,
            command,
            item["category"],
            0,
        )

    await message.reply_text(
        text,
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
    )

    message.stop_propagation()


async def add_note_command(_, message) -> None:
    if not _is_private(message):
        await message.reply_text(
            "Notes cá nhân chỉ dùng trong chat riêng với bot.",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    owner_id = int(message.from_user.id)
    raw = str(getattr(message, "text", "") or "").strip()
    parts = raw.split(maxsplit=1)
    text = parts[1].strip() if len(parts) == 2 else ""

    if not text:
        await message.reply_text(
            f"Dùng /{ADD_NOTE_COMMAND} <nội_dung>\n"
            f"Ví dụ: /{ADD_NOTE_COMMAND} Mua cà phê chiều nay",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    if len(text) > MAX_NOTE_CHARS:
        await message.reply_text(
            f"Note tối đa {MAX_NOTE_CHARS} ký tự.",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    note_id = await _db(
        _add_note_sync,
        owner_id,
        text,
    )

    LOGGER.info(
        "ATRI_NOTE_ADD user=%s note_id=%s chars=%s",
        owner_id,
        note_id,
        len(text),
    )

    await message.reply_text(
        f"✅ Em đã lưu note #{note_id}.\n\n{text}",
        quote=True,
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📝 Xem Notes",
                        callback_data=_cb(owner_id, "notes"),
                    )
                ]
            ]
        ),
    )

    message.stop_propagation()


async def list_notes_command(_, message) -> None:
    if not _is_private(message):
        await message.reply_text(
            "Notes cá nhân chỉ hiển thị trong chat riêng với bot.",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    owner_id = int(message.from_user.id)
    text, keyboard = await _notes_view(owner_id)

    await message.reply_text(
        text,
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
    )

    message.stop_propagation()


async def clear_notes_command(_, message) -> None:
    if not _is_private(message):
        await message.reply_text(
            "Notes cá nhân chỉ quản lý trong chat riêng với bot.",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    owner_id = int(message.from_user.id)
    total = await _db(_count_notes_sync, owner_id)

    if total <= 0:
        await message.reply_text(
            "📭 Bạn chưa có note nào.",
            quote=True,
            parse_mode=None,
        )
        message.stop_propagation()
        return

    await message.reply_text(
        f"Xóa toàn bộ {total} note cá nhân?",
        reply_markup=_note_clear_keyboard(owner_id),
        quote=True,
        parse_mode=None,
    )

    message.stop_propagation()


# =========================
# Callback
# =========================

async def command_center_callback(_, query) -> None:
    data = str(getattr(query, "data", "") or "")

    if not data.startswith("acui:"):
        return

    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return

    try:
        owner_id = int(parts[1])
    except Exception:
        await query.answer()
        return

    if not _callback_owner_ok(query, owner_id):
        await query.answer(
            "Menu này thuộc phiên của người khác. Hãy dùng /menu để mở menu riêng.",
            show_alert=True,
        )
        return

    action = parts[2]
    await query.answer()

    if action == "noop":
        return

    if action == "main":
        await _edit(
            query,
            _main_text(),
            _main_keyboard(owner_id),
        )
        return

    if action == "refresh":
        global CATALOG, CATEGORIES
        CATALOG, CATEGORIES = _build_catalog()

        LOGGER.info(
            "ATRI_COMMAND_UI_REFRESH user=%s commands=%s categories=%s",
            owner_id,
            len(CATALOG),
            len(CATEGORIES),
        )

        await _edit(
            query,
            _main_text(),
            _main_keyboard(owner_id),
        )
        return

    if action == "searchhelp":
        await _edit(
            query,
            (
                "🔎 Tìm lệnh\n\n"
                f"Dùng /{SEARCH_COMMAND} <từ_khóa>\n"
                f"Ví dụ:\n"
                f"/{SEARCH_COMMAND} boss\n"
                f"/{SEARCH_COMMAND} mirror\n"
                f"/{SEARCH_COMMAND} ban\n\n"
                f"Xem chi tiết trực tiếp:\n"
                f"/{DETAIL_COMMAND} <tên_lệnh>"
            ),
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🏠 Command Center",
                            callback_data=_cb(owner_id, "main"),
                        )
                    ]
                ]
            ),
        )
        return

    if action == "cat" and len(parts) >= 5:
        key = parts[3]

        try:
            page = int(parts[4])
        except Exception:
            page = 0

        text, keyboard = _category_view(
            owner_id,
            key,
            page,
        )

        await _edit(query, text, keyboard)
        return

    if action == "cmd" and len(parts) >= 6:
        command = parts[3]
        key = parts[4]

        try:
            page = int(parts[5])
        except Exception:
            page = 0

        text, keyboard = _command_view(
            owner_id,
            command,
            key,
            page,
        )

        await _edit(query, text, keyboard)
        return

    if action in {
        "notes",
        "noteadd",
        "noteclear",
        "noteclear_yes",
    }:
        message = getattr(query, "message", None)

        if message is None or not _is_private(message):
            await query.answer(
                "Notes cá nhân chỉ mở trong chat riêng với bot.",
                show_alert=True,
            )
            return

    if action == "notes":
        text, keyboard = await _notes_view(owner_id)
        await _edit(query, text, keyboard)
        return

    if action == "noteadd":
        await _edit(
            query,
            (
                "➕ Thêm note\n\n"
                f"Gửi:\n/{ADD_NOTE_COMMAND} <nội_dung>\n\n"
                f"Ví dụ:\n/{ADD_NOTE_COMMAND} Mua cà phê chiều nay"
            ),
            _notes_keyboard(owner_id),
        )
        return

    if action == "noteclear":
        total = await _db(_count_notes_sync, owner_id)

        if total <= 0:
            text, keyboard = await _notes_view(owner_id)
            await _edit(query, text, keyboard)
            return

        await _edit(
            query,
            f"Xóa toàn bộ {total} note cá nhân?",
            _note_clear_keyboard(owner_id),
        )
        return

    if action == "noteclear_yes":
        deleted = await _db(
            _clear_notes_sync,
            owner_id,
        )

        LOGGER.info(
            "ATRI_NOTE_CLEAR user=%s deleted=%s",
            owner_id,
            deleted,
        )

        await _edit(
            query,
            f"🗑 Đã xóa {deleted} note.\n\n"
            "Danh sách hiện đang trống.",
            _notes_keyboard(owner_id),
        )


def add_atri_command_ui_handlers(client) -> None:
    _init_notes_sync()

    # These are navigation commands only. Existing command handlers are not replaced.
    client.add_handler(
        MessageHandler(
            command_center,
            filters=filters.command(MENU_COMMANDS),
        ),
        group=-20,
    )

    client.add_handler(
        MessageHandler(
            command_search,
            filters=filters.command(SEARCH_COMMAND),
        ),
        group=-20,
    )

    client.add_handler(
        MessageHandler(
            command_detail,
            filters=filters.command(DETAIL_COMMAND),
        ),
        group=-20,
    )

    client.add_handler(
        MessageHandler(
            add_note_command,
            filters=filters.command(ADD_NOTE_COMMAND),
        ),
        group=-20,
    )

    client.add_handler(
        MessageHandler(
            list_notes_command,
            filters=filters.command(LIST_NOTES_COMMAND),
        ),
        group=-20,
    )

    client.add_handler(
        MessageHandler(
            clear_notes_command,
            filters=filters.command(CLEAR_NOTES_COMMAND),
        ),
        group=-20,
    )

    client.add_handler(
        CallbackQueryHandler(
            command_center_callback,
            filters=filters.regex(r"^acui:"),
        ),
        group=-20,
    )

    LOGGER.info(
        "Atri Command Center registered commands=%s categories=%s "
        "menu=/%s search=/%s detail=/%s",
        len(CATALOG),
        len(CATEGORIES),
        MENU_COMMANDS[0],
        SEARCH_COMMAND,
        DETAIL_COMMAND,
    )
