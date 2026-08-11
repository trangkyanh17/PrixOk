from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import LOGGER
from bot.core.config_manager import Config


STATE_PATH = Path(
    os.getenv(
        "ATRI_THINKING_CONTROL_PATH",
        "/app/atri_data/atri_thinking_control.json",
    )
)

LEVELS = ("minimal", "low", "medium", "high")

AUTO_DEFAULTS = {
    "chat": "medium",
    "web": "high",
    "tools": "high",
    "code": "high",
}

PRESETS = {
    "eco": {
        "chat": "minimal",
        "web": "low",
        "tools": "low",
        "code": "low",
    },
    "balanced": {
        "chat": "medium",
        "web": "medium",
        "tools": "medium",
        "code": "medium",
    },
    "max": {
        "chat": "high",
        "web": "high",
        "tools": "high",
        "code": "high",
    },
}

_LOCK = RLock()


def _default_state() -> dict[str, Any]:
    return {
        "auto": True,
        "levels": dict(AUTO_DEFAULTS),
    }


def _sanitize(raw: Any) -> dict[str, Any]:
    state = _default_state()

    if not isinstance(raw, dict):
        return state

    state["auto"] = bool(raw.get("auto", True))

    levels = raw.get("levels")
    if isinstance(levels, dict):
        for mode, default in AUTO_DEFAULTS.items():
            value = str(levels.get(mode, default)).casefold()
            if value in LEVELS:
                state["levels"][mode] = value

    return state


def _load_locked() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            return _sanitize(
                json.loads(STATE_PATH.read_text(encoding="utf-8"))
            )
    except Exception:
        LOGGER.exception("ATRI_THINKING_STATE_READ_FAILED")

    return _default_state()


def _save_locked(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        _sanitize(state),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=STATE_PATH.parent,
            prefix=f".{STATE_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)

        os.chmod(temp_path, 0o600)
        os.replace(temp_path, STATE_PATH)

    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def ensure_state_file() -> dict[str, Any]:
    with _LOCK:
        state = _load_locked()
        if not STATE_PATH.exists():
            _save_locked(state)
        return state


def get_thinking_control_state() -> dict[str, Any]:
    with _LOCK:
        state = _load_locked()
        return {
            "auto": bool(state["auto"]),
            "levels": dict(state["levels"]),
        }


def _effective_levels(state: dict[str, Any]) -> dict[str, str]:
    if state["auto"]:
        return dict(AUTO_DEFAULTS)

    return dict(state["levels"])


def resolve_thinking(mode: str) -> str:
    key = str(mode or "chat").casefold()

    if key not in AUTO_DEFAULTS:
        key = "chat"

    state = get_thinking_control_state()
    return _effective_levels(state)[key]


def thinking_status_text() -> str:
    state = get_thinking_control_state()
    levels = _effective_levels(state)

    control = "AUTO" if state["auto"] else "MANUAL"

    return (
        f"Điều khiển: {control}\n"
        "Thinking: "
        f"chat={levels['chat']} | "
        f"web={levels['web']} | "
        f"tools={levels['tools']} | "
        f"code={levels['code']}"
    )


def _thinking_keyboard_base(owner_id: int) -> InlineKeyboardMarkup:
    state = get_thinking_control_state()
    effective = _effective_levels(state)

    auto_label = (
        "🤖 AUTO: BẬT"
        if state["auto"]
        else "🛠 AUTO: TẮT"
    )

    def button(label: str, action: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"atc:{owner_id}:{action}",
        )

    return InlineKeyboardMarkup(
        [
            [
                button(auto_label, "auto"),
            ],
            [
                button(
                    f"💬 Chat: {effective['chat']}",
                    "cycle:chat",
                ),
                button(
                    f"🌐 Web: {effective['web']}",
                    "cycle:web",
                ),
            ],
            [
                button(
                    f"🧰 Tools: {effective['tools']}",
                    "cycle:tools",
                ),
                button(
                    f"💻 Code: {effective['code']}",
                    "cycle:code",
                ),
            ],
            [
                button("🌱 ECO", "preset:eco"),
                button("⚖️ BALANCED", "preset:balanced"),
                button("🚀 MAX", "preset:max"),
            ],
        ]
    )


def _cycle(level: str) -> str:
    try:
        index = LEVELS.index(level)
    except ValueError:
        index = 0

    return LEVELS[(index + 1) % len(LEVELS)]


def _change(action: str) -> tuple[dict[str, Any], str]:
    with _LOCK:
        state = _load_locked()

        if action == "auto":
            state["auto"] = not state["auto"]
            notice = (
                "AUTO đã bật."
                if state["auto"]
                else "AUTO đã tắt, chuyển sang MANUAL."
            )

        elif action.startswith("cycle:"):
            mode = action.split(":", 1)[1]

            if mode not in AUTO_DEFAULTS:
                raise ValueError("Mode không hợp lệ.")

            # Khi đang AUTO, lấy chính cấu hình AUTO hiện tại làm
            # điểm xuất phát trước khi chuyển sang MANUAL.
            if state["auto"]:
                state["levels"] = dict(AUTO_DEFAULTS)

            state["auto"] = False
            state["levels"][mode] = _cycle(
                state["levels"].get(
                    mode,
                    AUTO_DEFAULTS[mode],
                )
            )

            notice = (
                f"{mode} = {state['levels'][mode]} • MANUAL"
            )

        elif action.startswith("preset:"):
            preset = action.split(":", 1)[1]

            if preset not in PRESETS:
                raise ValueError("Preset không hợp lệ.")

            state["auto"] = False
            state["levels"] = dict(PRESETS[preset])
            notice = f"Preset {preset.upper()} đã áp dụng."

        else:
            raise ValueError("Action không hợp lệ.")

        state = _sanitize(state)
        _save_locked(state)
        return state, notice


def _refresh_message(text: str) -> str:
    status = thinking_status_text().splitlines()
    control_line = status[0]
    thinking_line = status[1]

    lines = str(text or "").splitlines()

    found_control = False
    found_thinking = False

    for i, line in enumerate(lines):
        if line.startswith("Điều khiển:"):
            lines[i] = control_line
            found_control = True

        elif line.startswith("Thinking:"):
            lines[i] = thinking_line
            found_thinking = True

    if not found_control:
        insert_at = 1

        for i, line in enumerate(lines):
            if line.startswith("Model:"):
                insert_at = i + 1
                break

        lines.insert(insert_at, control_line)

    if not found_thinking:
        insert_at = 1

        for i, line in enumerate(lines):
            if line.startswith("Điều khiển:"):
                insert_at = i + 1
                break

        lines.insert(insert_at, thinking_line)

    return "\n".join(lines)


async def atri_thinking_callback(_, query) -> None:
    data = str(getattr(query, "data", "") or "")
    parts = data.split(":", 2)

    if len(parts) != 3:
        await query.answer("Callback không hợp lệ.", show_alert=True)
        return

    try:
        owner_id = int(parts[1])
    except ValueError:
        await query.answer("Callback không hợp lệ.", show_alert=True)
        return

    user = getattr(query, "from_user", None)
    user_id = int(getattr(user, "id", 0) or 0)

    try:
        configured_owner = int(Config.OWNER_ID)
    except Exception:
        configured_owner = 0

    if (
        user_id <= 0
        or user_id != owner_id
        or user_id != configured_owner
    ):
        await query.answer(
            "Chỉ Prix mới được chỉnh Thinking.",
            show_alert=True,
        )
        return

    action = parts[2]

    try:
        _, notice = _change(action)
    except Exception as exc:
        LOGGER.exception("ATRI_THINKING_CALLBACK_FAILED")
        await query.answer(
            f"Không đổi được: {exc}",
            show_alert=True,
        )
        return

    await query.answer(notice)

    message = getattr(query, "message", None)
    if message is None:
        return

    text = str(
        getattr(message, "text", "")
        or getattr(message, "caption", "")
        or "Atri AI"
    )

    try:
        await message.edit_text(
            _refresh_message(text),
            reply_markup=thinking_keyboard(user_id),
            parse_mode=None,
            disable_web_page_preview=True,
        )
    except Exception:
        LOGGER.exception("ATRI_THINKING_MESSAGE_REFRESH_FAILED")


def add_atri_thinking_handlers(client) -> None:
    ensure_state_file()

    client.add_handler(
        CallbackQueryHandler(
            atri_thinking_callback,
            filters=filters.regex(r"^atc:"),
        ),
        group=-19,
    )

    LOGGER.info(
        "Atri Thinking Control registered state=%s",
        STATE_PATH,
    )


# ATRI_PROVIDER_CONTROL_KEYBOARD_BRIDGE_V1
def thinking_keyboard(owner_id: int):
    base = _thinking_keyboard_base(owner_id)

    try:
        from .atri_provider_control import provider_control_rows

        rows = list(getattr(base, "inline_keyboard", []) or [])
        rows.extend(provider_control_rows(owner_id))
        return InlineKeyboardMarkup(rows)

    except Exception:
        return base
