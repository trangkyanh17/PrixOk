from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import LOGGER
from bot.core.config_manager import Config

from .atri_provider_capabilities import (
    CANDIDATE_CHOICES,
    audit_age_seconds,
    audit_alert_events,
    audit_alert_text,
    audit_capabilities,
    audit_report_text,
    commit_audit_alert_snapshot,
    compact_report,
    current_audit_alert_snapshot,
    filter_model_choices,
    heal_model,
    provider_has_live_model,
    status_icon,
    supported_thinking_levels,
)


# ATRI_PROVIDER_CONTROL_V231
STATE_PATH = Path("/app/atri_data/atri_provider_control.json")

PROVIDER_ORDER = (
    "cerebras",
    "groq",
    "openrouter",
    "vertex",
)

PROVIDER_MODE_ORDER = (
    "smart",
    "cerebras",
    "groq",
    "openrouter",
    "vertex",
)

MODEL_CHOICES = CANDIDATE_CHOICES

DEFAULT_STATE: dict[str, Any] = {
    "provider_mode": "smart",
    "providers": {
        "cerebras": {
            "model": "gpt-oss-120b",
            "thinking": "auto",
        },
        "groq": {
            "model": "openai/gpt-oss-120b",
            "thinking": "auto",
        },
        "openrouter": {
            "model": "openrouter/free",
            "thinking": "auto",
        },
        "vertex": {
            "model": "auto",
            "thinking": "auto",
        },
    },
}


def _owner_id() -> int:
    try:
        return int(getattr(Config, "OWNER_ID", 0) or 0)
    except Exception:
        return 0


def _copy_default() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_STATE))


def _normalize(data: Any) -> dict[str, Any]:
    state = _copy_default()

    if not isinstance(data, dict):
        return state

    mode = str(data.get("provider_mode") or "smart").casefold()
    if mode in PROVIDER_MODE_ORDER:
        state["provider_mode"] = mode

    providers = data.get("providers")
    if not isinstance(providers, dict):
        return state

    for provider in PROVIDER_ORDER:
        item = providers.get(provider)
        if not isinstance(item, dict):
            continue

        allowed_models = {
            model
            for model, _ in MODEL_CHOICES[provider]
        }

        model = str(item.get("model") or "").strip()
        if model in allowed_models:
            state["providers"][provider]["model"] = model

        state["providers"][provider]["thinking"] = str(
            item.get("thinking") or "auto"
        ).casefold()

    return state


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        _normalize(state),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=".atri-provider-control-",
        suffix=".json",
        dir=str(STATE_PATH.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, STATE_PATH)
        os.chmod(STATE_PATH, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _load_state() -> dict[str, Any]:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}

    state = _normalize(raw)

    if not STATE_PATH.exists():
        _save_state(state)

    return state


_STATE = _load_state()


def _persist() -> None:
    _save_state(_STATE)


def _heal_state() -> bool:
    changed = False

    for provider in PROVIDER_ORDER:
        item = _STATE["providers"][provider]
        current_model = str(item.get("model") or "")
        fallback = str(
            DEFAULT_STATE["providers"][provider]["model"]
        )

        healed = heal_model(
            provider,
            current_model,
            fallback,
            MODEL_CHOICES[provider],
        )

        if healed != current_model:
            item["model"] = healed
            changed = True

        levels = supported_thinking_levels(provider, healed)
        current_thinking = str(
            item.get("thinking") or "auto"
        ).casefold()

        if current_thinking not in levels:
            item["thinking"] = (
                "auto"
                if "auto" in levels
                else levels[0]
            )
            changed = True

    mode = str(_STATE.get("provider_mode") or "smart").casefold()

    if (
        mode in PROVIDER_ORDER
        and not provider_has_live_model(
            mode,
            MODEL_CHOICES[mode],
        )
    ):
        _STATE["provider_mode"] = "smart"
        changed = True

    if changed:
        _persist()

    return changed


_heal_state()


def provider_control_state() -> dict[str, Any]:
    _heal_state()
    return json.loads(json.dumps(_STATE))


def resolve_provider_mode() -> str:
    _heal_state()
    mode = str(_STATE.get("provider_mode") or "smart").casefold()
    return mode if mode in PROVIDER_MODE_ORDER else "smart"


def resolve_provider_model(provider: str, fallback: str) -> str:
    provider = str(provider or "").casefold()

    if provider not in PROVIDER_ORDER:
        return str(fallback)

    item = _STATE["providers"][provider]
    selected = str(item.get("model") or "").strip()

    if provider == "vertex" and selected == "auto":
        return str(fallback)

    healed = heal_model(
        provider,
        selected,
        str(fallback),
        MODEL_CHOICES[provider],
    )

    if healed and healed != selected:
        item["model"] = healed
        _persist()

    return str(fallback) if healed == "auto" else str(healed or fallback)


def resolve_provider_thinking(provider: str, fallback: str) -> str:
    provider = str(provider or "").casefold()

    if provider not in PROVIDER_ORDER:
        return str(fallback or "medium").casefold()

    item = _STATE["providers"][provider]
    model = str(item.get("model") or "")
    levels = supported_thinking_levels(provider, model)
    value = str(item.get("thinking") or "auto").casefold()

    if value not in levels:
        value = "auto" if "auto" in levels else levels[0]
        item["thinking"] = value
        _persist()

    if value == "auto":
        return str(fallback or "medium").casefold()

    return value


# ATRI_FREE_STYLE_LAYER_V234_OWNER_LOCK
_ATRI_OWNER_PRONOUN_SENTINEL = "[ATRI_OWNER_PRONOUN_LOCK]"


def _atri_current_text(current_parts: Any) -> str:
    if not isinstance(current_parts, list):
        return ""

    chunks: list[str] = []
    for part in current_parts:
        if not isinstance(part, dict):
            continue
        value = part.get("text")
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip())

    return "\n".join(chunks)[:1600]


def _atri_register_hint(
    current_parts: Any,
    *,
    owner_mode: bool = False,
) -> str:
    text = _atri_current_text(current_parts)
    if not text:
        return ""

    folded = " " + text.casefold().replace("\n", " ") + " "
    hints: list[str] = []

    casual_tokens = (
        " t ", " m ", " tao ", " mày ", " nhỉ", " nha", " nhé",
        " á", " oke", " ok ", " ko ", " không á", " why ",
        " bạn ", " tôi ", " mình ", " cậu ",
    )
    technical_tokens = (
        " code", " log", " lỗi", " bug", " fix", " deploy", " vps",
        " linux", " termux", " python", " bash", " api", " bot", " esp32",
        " docker", " server", " firmware", " source", " compile", " build",
    )

    if any(token in folded for token in casual_tokens):
        if owner_mode:
            hints.append(
                "Tin nhắn hiện tại có giọng thân mật: giữ nhịp nói tự nhiên, "
                "nhưng không bắt chước đại từ của Prix; khóa em/Prix vẫn ưu tiên tuyệt đối."
            )
        else:
            hints.append(
                "Tin nhắn hiện tại có giọng thân mật: Atri vẫn tự xưng em và gọi người "
                "dùng là anh hoặc chị, nhưng dùng đại từ vừa đủ, không lặp máy móc và "
                "không biến thành văn phong chăm sóc khách hàng."
            )

    if any(token in folded for token in technical_tokens):
        hints.append(
            "Đây là ngữ cảnh kỹ thuật: ưu tiên kết luận, nguyên nhân hoặc lệnh/code "
            "hành động trước; chỉ giải thích phần thật sự cần để người dùng làm tiếp."
        )

    if len(text) <= 180:
        hints.append(
            "Câu hỏi ngắn: mặc định trả lời gọn và có ích ngay, không kéo dài chỉ "
            "để tỏ ra đầy đủ."
        )

    return " ".join(hints)


# ATRI_PROVIDER_OWNER_PERSONA_PRIX_V138
# ATRI_PROVIDER_NON_OWNER_PERSONA_ANH_CHI_V140
def naturalize_system_instruction(
    base: str,
    current_parts: Any = None,
) -> str:
    raw_base = str(base or "")
    owner_mode = _ATRI_OWNER_PRONOUN_SENTINEL in raw_base
    base = raw_base.replace(_ATRI_OWNER_PRONOUN_SENTINEL, "").strip()

    style = (
        "Giọng Atri phải tự nhiên và nhất quán giữa mọi provider: trả lời như một "
        "trợ lý quen ngữ cảnh đang nói chuyện trực tiếp với người dùng, không như "
        "mẫu chatbot tổng đài. Đi thẳng vào ý chính; tránh mở đầu rỗng như "
        "'Tôi hiểu', 'Chắc chắn rồi', 'Dưới đây là', hoặc nhắc lại nguyên câu hỏi "
        "khi không cần. Bám ngôn ngữ, độ thân mật và nhịp câu của tin nhắn hiện tại. "
        "Viết tự nhiên, dùng từ thông dụng; không phô diễn, không chèn câu đạo lý, "
        "không kết bằng lời mời hỗ trợ chung chung. Với việc kỹ thuật, nếu đủ dữ kiện "
        "thì nêu root cause/kết quả và lệnh hoặc code hành động trước. Không lạm dụng "
        "heading, bullet, emoji hay in đậm. Không tự xưng tên model/provider và không "
        "bịa việc đã dùng tool, lịch sử, tài khoản, bộ nhớ hay dữ liệu riêng tư."
    )

    if owner_mode:
        style += (
            "\nQUY TẮC OWNER VÀ PHONG CÁCH, ƯU TIÊN CAO NHẤT: người dùng hiện tại "
            "là Owner tên Prix. Trong mọi câu Atri tự viết, Atri LUÔN tự xưng 'em' "
            "và LUÔN gọi Owner là 'Prix'; không gọi Prix là 'anh' và không đổi sang "
            "cặp đại từ khác. Khi nói chuyện đời thường, giữ giọng thân mật, đáng yêu, "
            "tinh nghịch và giàu cảm xúc; có thể dùng tự nhiên 'dạ', 'dọ', mô tả "
            "hành động ngắn bằng *...* và vài emoji phù hợp như phong cách quen thuộc "
            "của Atri, nhưng không máy móc hay lạm dụng. Với việc kỹ thuật, vẫn giữ "
            "em/Prix nhưng ưu tiên kết luận và lệnh/code chính xác. Đại từ khác chỉ "
            "được giữ nguyên bên trong trích dẫn, log, code, command hoặc dữ liệu."
        )
    else:
        style += (
            "\nQUY TẮC XƯNG HÔ CHO NGƯỜI DÙNG KHÁC: người dùng hiện tại KHÔNG phải "
            "Owner, vì vậy không gọi họ là Prix. Atri LUÔN tự xưng 'em' và gọi người "
            "dùng là 'anh' hoặc 'chị'. Chọn đúng một cách gọi dựa trên cách tự giới "
            "thiệu, tên và ngữ cảnh; nếu chưa rõ thì tránh gọi trực tiếp hoặc dùng "
            "'anh/chị', không tự đoán giới tính. Dùng đại từ vừa đủ để câu tự nhiên, "
            "không chèn anh/chị vào mọi câu. Không tự xưng tôi, tui, mình, tớ, ta hoặc "
            "Atri; không gọi người dùng là bạn, mày hay cậu. Không mở đầu hoặc tự giới "
            "thiệu kiểu máy móc như 'Tôi là AI', 'Em là AI', 'Là một AI', 'Tôi hiểu' "
            "hay 'Chắc chắn rồi'."
        )

    register = _atri_register_hint(
        current_parts,
        owner_mode=owner_mode,
    )
    if register:
        style += "\nĐiều chỉnh cho tin nhắn hiện tại: " + register

    return style if not base else base + "\n\n" + style





def _model_label(provider: str, model: str) -> str:
    for value, label in MODEL_CHOICES[provider]:
        if value == model:
            return label

    return model[:14]


def _thinking_label(value: str) -> str:
    return {
        "auto": "AUTO",
        "minimal": "MIN",
        "low": "LOW",
        "medium": "MED",
        "high": "HIGH",
    }.get(value, value.upper()[:5])


def _mode_label(value: str) -> str:
    return {
        "smart": "SMART",
        "cerebras": "CEREBRAS",
        "groq": "GROQ",
        "openrouter": "OPENROUTER",
        "vertex": "VERTEX",
    }.get(value, value.upper())


def provider_status_text() -> str:
    state = provider_control_state()
    providers = state["providers"]

    labels = {
        "cerebras": "Cerebras",
        "groq": "Groq",
        "openrouter": "OpenRouter",
        "vertex": "Vertex",
    }

    lines = [
        "API route: "
        + _mode_label(state["provider_mode"])
    ]

    for provider in PROVIDER_ORDER:
        item = providers[provider]
        model = item["model"]

        lines.append(
            f"{labels[provider]}: "
            f"{status_icon(provider, model)} "
            f"{_model_label(provider, model)} | "
            f"think={_thinking_label(item['thinking'])}"
        )

    return "\n".join(lines)


def provider_control_rows(
    owner_id: int,
) -> list[list[InlineKeyboardButton]]:
    state = provider_control_state()
    providers = state["providers"]

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "🔀 API: " + _mode_label(state["provider_mode"]),
                callback_data="apc:mode",
            )
        ]
    ]

    meta = {
        "cerebras": ("⚡", "CB"),
        "groq": ("🚀", "GQ"),
        "openrouter": ("🌐", "OR"),
        "vertex": ("☁️", "VX"),
    }

    for provider in PROVIDER_ORDER:
        icon, short = meta[provider]
        item = providers[provider]
        model = item["model"]
        levels = supported_thinking_levels(provider, model)

        thinking_text = (
            _thinking_label(item["thinking"])
            if len(levels) > 1
            else "N/A"
        )

        rows.append(
            [
                InlineKeyboardButton(
                    f"{icon} {short}: "
                    f"{status_icon(provider, model)} "
                    f"{_model_label(provider, model)}",
                    callback_data=f"apc:model:{provider}",
                ),
                InlineKeyboardButton(
                    "🧠 " + thinking_text,
                    callback_data=f"apc:think:{provider}",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "🔎 Audit API/model",
                callback_data="apc:audit",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                "♻️ Reset API controls",
                callback_data="apc:reset",
            )
        ]
    )

    return rows


def _cycle(current: str, values: tuple[str, ...]) -> str:
    if not values:
        return current

    try:
        index = values.index(current)
    except ValueError:
        index = -1

    return values[(index + 1) % len(values)]


def _cycle_mode() -> str:
    current = resolve_provider_mode()
    available_modes = ["smart"]

    for provider in PROVIDER_ORDER:
        if provider_has_live_model(
            provider,
            MODEL_CHOICES[provider],
        ):
            available_modes.append(provider)

    new_value = _cycle(current, tuple(available_modes))
    _STATE["provider_mode"] = new_value
    _persist()

    return new_value


def _cycle_model(provider: str) -> str:
    choices = filter_model_choices(
        provider,
        MODEL_CHOICES[provider],
    )

    models = tuple(model for model, _ in choices)
    current = str(
        _STATE["providers"][provider]["model"]
    )

    if not models:
        return current

    new_value = _cycle(current, models)
    _STATE["providers"][provider]["model"] = new_value

    levels = supported_thinking_levels(provider, new_value)
    current_thinking = str(
        _STATE["providers"][provider]["thinking"]
    )

    if current_thinking not in levels:
        _STATE["providers"][provider]["thinking"] = (
            "auto"
            if "auto" in levels
            else levels[0]
        )

    _persist()
    return new_value


def _cycle_thinking(provider: str) -> str:
    model = str(
        _STATE["providers"][provider]["model"]
    )

    levels = supported_thinking_levels(provider, model)

    if len(levels) <= 1:
        _STATE["providers"][provider]["thinking"] = levels[0]
        _persist()
        return levels[0]

    current = str(
        _STATE["providers"][provider]["thinking"]
    )

    new_value = _cycle(current, levels)
    _STATE["providers"][provider]["thinking"] = new_value
    _persist()

    return new_value


def _reset() -> None:
    global _STATE

    _STATE = _copy_default()
    _heal_state()
    _persist()


# ATRI_CAPABILITY_WATCH_V2311
_CAPABILITY_WATCH_TASKS: set[asyncio.Task[Any]] = set()


async def _background_capability_watch(client) -> None:
    LOGGER.info("ATRI_CAPABILITY_WATCH_STARTED")
    await asyncio.sleep(90)

    while True:
        try:
            if audit_age_seconds() >= 24 * 3600:
                previous_snapshot = current_audit_alert_snapshot()
                report = await audit_capabilities()

                _heal_state()

                events = audit_alert_events(
                    report,
                    previous_snapshot,
                )
                owner_id = _owner_id()
                if events and owner_id > 0:
                    await client.send_message(
                        owner_id,
                        audit_alert_text(events),
                        parse_mode=None,
                    )

                commit_audit_alert_snapshot(report)

                LOGGER.info(
                    "ATRI_CAPABILITY_DAILY_AUDIT alerts=%s %s",
                    len(events),
                    compact_report(report),
                )
        except Exception as exc:
            LOGGER.warning(
                "ATRI_CAPABILITY_DAILY_AUDIT_FAILED %s:%s",
                type(exc).__name__,
                exc,
            )

        await asyncio.sleep(6 * 3600)


async def atri_provider_control_callback(_, query) -> None:
    user = getattr(query, "from_user", None)
    uid = int(getattr(user, "id", 0) or 0)

    if uid != _owner_id():
        await query.answer(
            "Chỉ owner được đổi API/model/thinking.",
            show_alert=True,
        )
        return

    data = str(getattr(query, "data", "") or "")
    parts = data.split(":")

    try:
        if data == "apc:mode":
            value = _cycle_mode()
            await query.answer(
                "API route: " + _mode_label(value)
            )

        elif data == "apc:reset":
            _reset()
            await query.answer(
                "Đã reset API controls về mặc định."
            )

        elif data == "apc:audit":
            await query.answer("Đang audit model API...")

            report = await asyncio.wait_for(
                audit_capabilities(),
                timeout=60.0,
            )
            _heal_state()
            commit_audit_alert_snapshot(report)

            LOGGER.info(
                "ATRI_CAPABILITY_MANUAL_AUDIT user=%s %s",
                uid,
                compact_report(report),
            )

            msg = getattr(query, "message", None)
            if msg is not None:
                await msg.reply_text(
                    audit_report_text(report),
                    quote=True,
                    parse_mode=None,
                )

        elif (
            len(parts) == 3
            and parts[0] == "apc"
            and parts[1] == "model"
            and parts[2] in PROVIDER_ORDER
        ):
            provider = parts[2]
            model = _cycle_model(provider)

            await query.answer(
                provider.capitalize()
                + " model: "
                + _model_label(provider, model)
            )

        elif (
            len(parts) == 3
            and parts[0] == "apc"
            and parts[1] == "think"
            and parts[2] in PROVIDER_ORDER
        ):
            provider = parts[2]
            thinking = _cycle_thinking(provider)

            await query.answer(
                provider.capitalize()
                + " thinking: "
                + _thinking_label(thinking)
            )

        else:
            await query.answer()
            return

        msg = getattr(query, "message", None)

        if msg is not None:
            try:
                from .atri_thinking_control import thinking_keyboard

                await msg.edit_reply_markup(
                    reply_markup=thinking_keyboard(uid)
                )
            except Exception as exc:
                if "MESSAGE_NOT_MODIFIED" not in str(exc).upper():
                    LOGGER.warning(
                        "Atri provider control keyboard refresh "
                        "failed: %s",
                        exc,
                    )

        LOGGER.info(
            "ATRI_PROVIDER_CONTROL user=%s action=%s",
            uid,
            data,
        )

    except Exception as exc:
        LOGGER.exception(
            "ATRI_PROVIDER_CONTROL_FAILED action=%s",
            data,
        )

        try:
            await query.answer(
                "Không cập nhật được: "
                + type(exc).__name__,
                show_alert=True,
            )
        except Exception:
            pass


def add_atri_provider_control_handlers(client) -> None:
    client.add_handler(
        CallbackQueryHandler(
            atri_provider_control_callback,
            filters=filters.regex(r"^apc:"),
        ),
        group=-18,
    )

    loop = getattr(client, "loop", None)
    if loop is None or loop.is_closed():
        raise RuntimeError("Pyrogram client event loop unavailable")

    task = loop.create_task(
        _background_capability_watch(client),
        name="atri-capability-watch",
    )
    _CAPABILITY_WATCH_TASKS.add(task)
    task.add_done_callback(_CAPABILITY_WATCH_TASKS.discard)

    LOGGER.info(
        "Atri Capability Watch scheduled loop_running=%s",
        loop.is_running(),
    )

    LOGGER.info(
        "Atri Provider Control registered state=%s",
        STATE_PATH,
    )
