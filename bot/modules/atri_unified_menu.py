from __future__ import annotations

# ATRI_UI_POLISH_V1614

from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import LOGGER
from bot.core.config_manager import Config
from . import atri_command_ui as command_ui
from .atri_update_idempotency_v1684 import (
    claim_telegram_update_once,
)


# ATRI_UNIFIED_COMMAND_CENTER_V160
# Navigation only: never calls operational handlers from callbacks.
# Legacy direct commands/aliases remain authoritative and keep their permissions.


def _cmd(base: str) -> str:
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
    return f"{base}{suffix}"


HUB_COMMANDS: dict[str, str] = {
    _cmd("menu"): "main",
    _cmd("amenu"): "main",
    _cmd("transfer"): "transfer",
    _cmd("cloud"): "cloud",
    _cmd("tasks"): "tasks",
    _cmd("tools"): "tools",
    _cmd("rose"): "rose",
    _cmd("admin"): "admin",
    _cmd("help"): "main",
}

CATEGORY_PARENT = {
    "mirror": "transfer",
    "leech": "transfer",
    "rose_mod": "rose",
    "rose_content": "rose",
    "rose_guard": "rose",
    "rose_fed": "rose",
    "rose_backup": "rose",
}

ROOT_BUTTONS = (
    ("🤖 Atri AI", "cat", "atri"),
    ("📥 Tải & gửi", "hub", "transfer"),
    ("🗂 Cloud & Search", "cat", "cloud"),
    ("⏱ Tác vụ", "cat", "tasks"),
    ("🧰 Công cụ", "cat", "tools"),
    ("🛡 Rose", "hub", "rose"),
    ("🏠 Cơ bản", "cat", "core"),
    ("⚙️ Bot Admin", "cat", "admin"),
)

SUBHUBS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "transfer": (
        "📥 Tải & gửi",
        (
            ("☁️ Mirror lên cloud", "mirror"),
            ("📤 Leech về Telegram", "leech"),
        ),
    ),
    "rose": (
        "🛡 Atri Rose",
        (
            ("👮 Quản trị", "rose_mod"),
            ("📝 Nội dung", "rose_content"),
            ("🔒 Bảo vệ", "rose_guard"),
            ("🌐 Federation", "rose_fed"),
            ("💾 Dữ liệu", "rose_backup"),
        ),
    ),
}

PAGE_SIZE = 10
_HANDLER_REGISTRATION_MARKER = (
    "_atri_unified_menu_handlers_registered_v1684"
)


def _owner_id(query_or_message) -> int:
    user = getattr(query_or_message, "from_user", None)
    return int(getattr(user, "id", 0) or 0)


def _is_owner(user_id: int) -> bool:
    try:
        return int(user_id) == int(getattr(Config, "OWNER_ID", 0) or 0)
    except Exception:
        return False


def _is_owner_or_sudo(user_id: int) -> bool:
    try:
        if int(user_id) == int(getattr(Config, "OWNER_ID", 0) or 0):
            return True
    except Exception:
        pass

    raw = str(getattr(Config, "SUDO_USERS", "") or "")
    sudo_ids = {
        int(value)
        for value in raw.replace(",", " ").split()
        if value.lstrip("-").isdigit()
    }
    return int(user_id) in sudo_ids


def _cb(owner_id: int, *parts: object) -> str:
    return "aucm:" + ":".join([str(owner_id), *(str(x) for x in parts)])


def _rows(buttons: list[InlineKeyboardButton], width: int = 2):
    return [buttons[i:i + width] for i in range(0, len(buttons), width)]


def _refresh_catalog() -> None:
    command_ui.CATALOG, command_ui.CATEGORIES = command_ui._build_catalog()


def _root_text(owner_id: int) -> str:
    _refresh_catalog()
    if not _is_owner(owner_id):
        return (
            "Atri • Trung tâm điều khiển\n\n"
            "6 nhóm công khai: Cơ bản, Mirror, Leech, Cloud & Search, "
            "Tác vụ và Công cụ."
        )
    return (
        "Atri • Trung tâm điều khiển\n\n"
        f"Đã quét {len(command_ui.CATALOG)} lệnh chính."
    )


def _root_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    if not _is_owner(owner_id):
        public = (
            ("🏠 Cơ bản", "core"),
            ("☁️ Mirror", "mirror"),
            ("📤 Leech", "leech"),
            ("🗂 Cloud & Search", "cloud"),
            ("⏱ Tác vụ", "tasks"),
            ("🧰 Công cụ", "tools"),
        )
        buttons = [
            InlineKeyboardButton(
                f"{label} · {len(command_ui.CATEGORIES.get(key, []))}",
                callback_data=_cb(owner_id, "cat", key, 0),
            )
            for label, key in public
        ]
        return InlineKeyboardMarkup(_rows(buttons, 2))

    buttons: list[InlineKeyboardButton] = []
    for label, action, key in ROOT_BUTTONS:
        buttons.append(
            InlineKeyboardButton(
                label,
                callback_data=_cb(owner_id, action, key, 0),
            )
        )

    rows = _rows(buttons, 2)
    rows.append(
        [
            InlineKeyboardButton(
                "🔎 Tìm lệnh",
                callback_data=command_ui._cb(owner_id, "searchhelp"),
            ),
            InlineKeyboardButton(
                "📝 Notes",
                callback_data=command_ui._cb(owner_id, "notes"),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "📚 Toàn bộ lệnh",
                callback_data=command_ui._cb(owner_id, "main"),
            ),
            InlineKeyboardButton(
                "🔄 Quét lại",
                callback_data=_cb(owner_id, "refresh"),
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _subhub_view(owner_id: int, key: str) -> tuple[str, InlineKeyboardMarkup]:
    title, children = SUBHUBS[key]
    buttons = []
    total = 0
    for label, category in children:
        count = len(command_ui.CATEGORIES.get(category, []))
        total += count
        buttons.append(
            InlineKeyboardButton(
                f"{label} · {count}",
                callback_data=_cb(owner_id, "cat", category, 0),
            )
        )

    rows = _rows(buttons, 2)
    rows.append(
        [InlineKeyboardButton("🏠 Menu", callback_data=_cb(owner_id, "main"))]
    )
    return f"{title}\n\n{total} lệnh trong nhóm. Chọn chức năng:", InlineKeyboardMarkup(rows)


def _category_back(owner_id: int, key: str) -> InlineKeyboardButton:
    parent = CATEGORY_PARENT.get(key)
    if parent:
        return InlineKeyboardButton(
            "⬅️ Quay lại",
            callback_data=_cb(owner_id, "hub", parent),
        )
    return InlineKeyboardButton("🏠 Menu", callback_data=_cb(owner_id, "main"))


def _category_view(owner_id: int, key: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    commands = list(command_ui.CATEGORIES.get(key, []))
    label, description = command_ui.CATEGORY_META.get(
        key, command_ui.CATEGORY_META["other"]
    )
    pages = max(1, (len(commands) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    subset = commands[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    buttons = [
        InlineKeyboardButton(
            f"/{command}",
            callback_data=_cb(owner_id, "cmd", command, key, page),
        )
        for command in subset
    ]
    rows = _rows(buttons, 2)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=_cb(owner_id, "cat", key, page - 1)))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=_cb(owner_id, "noop")))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=_cb(owner_id, "cat", key, page + 1)))
    if nav:
        rows.append(nav)

    rows.append([
        _category_back(owner_id, key),
        InlineKeyboardButton("🏠 Menu", callback_data=_cb(owner_id, "main")),
    ])
    return (
        f"{label}\n{description}\n\n"
        f"{len(commands)} lệnh • trang {page + 1}/{pages}\n"
        "Chọn lệnh để xem cú pháp và quyền.",
        InlineKeyboardMarkup(rows),
    )


def _command_view(owner_id: int, command: str, category: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    item = command_ui.CATALOG.get(command)
    if not item:
        return _root_text(owner_id), _root_keyboard(owner_id)

    aliases = item.get("aliases") or []
    alias_text = ", ".join(f"/{value}" for value in aliases) if aliases else "không có"
    text = (
        f"/{command}\n\n"
        f"{item.get('description', '')}\n\n"
        f"Cú pháp: {item.get('usage', '/' + command)}\n"
        f"Quyền: {item.get('permission', 'theo handler')}\n"
        f"Alias: {alias_text}\n\n"
        "Gửi cú pháp trên trong chat để chạy. Menu không tự gọi handler nên không thể bypass quyền."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Danh sách",
                    callback_data=_cb(owner_id, "cat", category, page),
                ),
                InlineKeyboardButton("🏠 Menu", callback_data=_cb(owner_id, "main")),
            ]
        ]
    )
    return text, keyboard


async def _edit(query, text: str, keyboard: InlineKeyboardMarkup) -> None:
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
        if "MESSAGE_NOT_MODIFIED" not in str(exc).upper():
            LOGGER.warning("Atri unified menu edit failed: %s", exc)


async def unified_menu_command(_, message) -> None:
    accepted, identity = claim_telegram_update_once(
        message,
        route="unified-menu-command",
    )
    if not accepted:
        LOGGER.warning(
            "ATRI_UNIFIED_MENU_DUPLICATE_DROPPED_V1684 "
            "route=command identity=%s",
            identity,
        )
        message.stop_propagation()
        return

    owner_id = _owner_id(message)
    raw = str(getattr(message, "text", "") or "").strip()
    command = raw.split(maxsplit=1)[0].lstrip("/").split("@", 1)[0]
    target = HUB_COMMANDS.get(command, "main")

    _refresh_catalog()
    if not _is_owner(owner_id) and target not in {
        "main", "transfer", "cloud", "tasks", "tools"
    }:
        target = "main"

    if target == "main":
        text, keyboard = _root_text(owner_id), _root_keyboard(owner_id)
    elif target in SUBHUBS:
        text, keyboard = _subhub_view(owner_id, target)
    else:
        text, keyboard = _category_view(owner_id, target, 0)

    await message.reply_text(
        text,
        reply_markup=keyboard,
        quote=True,
        parse_mode=None,
        disable_web_page_preview=True,
    )
    message.stop_propagation()


async def unified_menu_callback(_, query) -> None:
    accepted, identity = claim_telegram_update_once(
        query,
        route="unified-menu-callback",
    )
    if not accepted:
        LOGGER.warning(
            "ATRI_UNIFIED_MENU_DUPLICATE_DROPPED_V1684 "
            "route=callback identity=%s",
            identity,
        )
        return

    data = str(getattr(query, "data", "") or "")
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer()
        return
    try:
        owner_id = int(parts[1])
    except Exception:
        await query.answer()
        return
    if _owner_id(query) != owner_id:
        await query.answer(
            "Menu này thuộc phiên của người khác. Dùng /menu để mở menu riêng.",
            show_alert=True,
        )
        return

    action = parts[2]
    if action == "noop":
        await query.answer()
        return

    if not _is_owner(owner_id):
        allowed_categories = set(command_ui.PUBLIC_CATEGORIES)
        if action in {"refresh", "hub"}:
            await query.answer("Mục này chỉ hiển thị cho Owner.", show_alert=True)
            await _edit(query, _root_text(owner_id), _root_keyboard(owner_id))
            return
        if action == "cat" and (len(parts) < 4 or parts[3] not in allowed_categories):
            await query.answer("Danh mục này chỉ hiển thị cho Owner.", show_alert=True)
            await _edit(query, _root_text(owner_id), _root_keyboard(owner_id))
            return
        if action == "cmd" and len(parts) >= 4 and not command_ui._command_visible(owner_id, parts[3]):
            await query.answer("Lệnh này chỉ hiển thị cho Owner.", show_alert=True)
            await _edit(query, _root_text(owner_id), _root_keyboard(owner_id))
            return

    await query.answer()
    _refresh_catalog()

    if action == "main":
        await _edit(query, _root_text(owner_id), _root_keyboard(owner_id))
        return
    if action == "refresh":
        await _edit(query, _root_text(owner_id), _root_keyboard(owner_id))
        return
    if action == "hub" and len(parts) >= 4 and parts[3] in SUBHUBS:
        text, keyboard = _subhub_view(owner_id, parts[3])
        await _edit(query, text, keyboard)
        return
    if action == "cat" and len(parts) >= 4:
        key = parts[3]
        page = int(parts[4]) if len(parts) >= 5 and parts[4].isdigit() else 0
        text, keyboard = _category_view(owner_id, key, page)
        await _edit(query, text, keyboard)
        return
    if action == "cmd" and len(parts) >= 6:
        command = parts[3]
        category = parts[4]
        page = int(parts[5]) if parts[5].isdigit() else 0
        text, keyboard = _command_view(owner_id, command, category, page)
        await _edit(query, text, keyboard)
        return


def add_atri_unified_menu_handlers(client) -> bool:
    if getattr(client, _HANDLER_REGISTRATION_MARKER, False):
        LOGGER.info(
            "Atri Unified Command Center V168.4 registration skipped "
            "reason=already-registered"
        )
        return False

    registered_handlers = []
    setattr(client, _HANDLER_REGISTRATION_MARKER, True)
    try:
        for handler in (
            MessageHandler(
                unified_menu_command,
                filters=filters.command(list(HUB_COMMANDS)),
            ),
            CallbackQueryHandler(
                unified_menu_callback,
                filters=filters.regex(r"^aucm:"),
            ),
        ):
            client.add_handler(handler, group=-21)
            registered_handlers.append((handler, -21))
    except BaseException:
        rollback_failed = False
        for handler, group in reversed(registered_handlers):
            try:
                client.remove_handler(handler, group=group)
            except BaseException as rollback_error:
                rollback_failed = True
                LOGGER.error(
                    "Atri Unified Command Center V168.4 rollback failed "
                    "handler=%s group=%s error=%s",
                    type(handler).__name__,
                    group,
                    rollback_error,
                )

        if rollback_failed:
            LOGGER.error(
                "Atri Unified Command Center V168.4 marker retained "
                "reason=partial-registration-rollback-failed"
            )
        else:
            delattr(client, _HANDLER_REGISTRATION_MARKER)
        raise

    LOGGER.info(
        "Atri Unified Command Center V168.4 registered "
        "hubs=%s public=6 owner_full=1",
        ",".join(HUB_COMMANDS),
    )
    return True
