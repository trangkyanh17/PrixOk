from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler, EditedMessageHandler, MessageHandler

from bot import LOGGER, bot_loop

_SCHEMA_VERSION = 1
_HANDLER_GROUP = -1000
_QUEUE_MAX = 256
_HTTP_TIMEOUT_SECONDS = 0.75
_MEDIA_FIELDS = (
    "photo",
    "sticker",
    "animation",
    "video",
    "video_note",
    "document",
    "audio",
    "voice",
)

_queue: asyncio.Queue[dict[str, Any]] | None = None
_worker_task: asyncio.Task | None = None
_last_error_log = 0.0
_drop_count = 0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _shadow_enabled() -> bool:
    return _env_bool("ATRI_V150_TELEGRAM_SHADOW", False)


def _shadow_url() -> str:
    explicit = os.getenv("ATRI_TELEGRAM_SHADOW_URL", "").strip()
    if explicit:
        return explicit
    addr = os.getenv("ATRI_TELEGRAM_SHADOW_ADDR", "127.0.0.1:18750").strip()
    return f"http://{addr}/v1/telegram/shadow"


def _shadow_secret() -> str:
    return os.getenv("ATRI_TELEGRAM_SHADOW_SECRET", "").strip()


def _int_attr(obj: Any, name: str) -> int:
    value = getattr(obj, name, 0) if obj is not None else 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _str_attr(obj: Any, name: str) -> str:
    value = getattr(obj, name, "") if obj is not None else ""
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw)


def _command_from_text(text: str) -> str:
    text = text.strip()
    if not text.startswith("/"):
        return ""
    token = text.split(maxsplit=1)[0][1:]
    return token.split("@", 1)[0].lower()


def _media_payload(message: Any) -> dict[str, Any] | None:
    for media_type in _MEDIA_FIELDS:
        media = getattr(message, media_type, None)
        if media is None:
            continue
        return {
            "type": media_type,
            "file_id": _str_attr(media, "file_id"),
            "unique_id": _str_attr(media, "file_unique_id"),
            "file_name": _str_attr(media, "file_name"),
            "mime_type": _str_attr(media, "mime_type"),
            "size": _int_attr(media, "file_size"),
            "width": _int_attr(media, "width"),
            "height": _int_attr(media, "height"),
            "duration": _int_attr(media, "duration"),
            "emoji": _str_attr(media, "emoji"),
            "is_animated": bool(getattr(media, "is_animated", False)),
            "is_video": bool(getattr(media, "is_video", False)),
        }
    return None


def _message_payload(message: Any, kind: str) -> dict[str, Any]:
    chat = getattr(message, "chat", None)
    user = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    payload: dict[str, Any] = {
        "version": _SCHEMA_VERSION,
        "kind": kind,
        "chat_id": _int_attr(chat, "id"),
        "message_id": _int_attr(message, "id"),
        "thread_id": _int_attr(message, "message_thread_id"),
        "user_id": _int_attr(user, "id"),
        "chat_type": _str_attr(chat, "type"),
        "text": text,
        "command": _command_from_text(text),
    }
    media = _media_payload(message)
    if media is not None:
        payload["media"] = media
    return payload


def _callback_payload(query: Any) -> dict[str, Any]:
    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None) if message is not None else None
    user = getattr(query, "from_user", None)
    return {
        "version": _SCHEMA_VERSION,
        "kind": "callback_query",
        "chat_id": _int_attr(chat, "id"),
        "message_id": _int_attr(message, "id"),
        "thread_id": _int_attr(message, "message_thread_id"),
        "user_id": _int_attr(user, "id"),
        "chat_type": _str_attr(chat, "type"),
        "callback_data": str(getattr(query, "data", "") or ""),
    }


def _post_sync(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        _shadow_url(),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    secret = _shadow_secret()
    if secret:
        request.add_header("X-Atri-Shadow-Secret", secret)
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 202:
            raise RuntimeError(f"shadow ingress returned HTTP {response.status}")


def _log_transport_error(exc: BaseException) -> None:
    global _last_error_log
    now = time.monotonic()
    if now - _last_error_log < 60:
        return
    _last_error_log = now
    LOGGER.warning("ATRI_V150_TELEGRAM_SHADOW_TRANSPORT_ERROR error=%s", exc)


async def _shadow_worker() -> None:
    assert _queue is not None
    while True:
        payload = await _queue.get()
        try:
            await asyncio.to_thread(_post_sync, payload)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            _log_transport_error(exc)
        except Exception as exc:  # shadow path must never break production dispatch
            _log_transport_error(exc)
        finally:
            _queue.task_done()


def _enqueue(payload: dict[str, Any]) -> None:
    global _drop_count
    if _queue is None:
        return
    try:
        _queue.put_nowait(payload)
    except asyncio.QueueFull:
        _drop_count += 1
        if _drop_count == 1 or _drop_count % 100 == 0:
            LOGGER.warning(
                "ATRI_V150_TELEGRAM_SHADOW_QUEUE_FULL dropped=%s max=%s",
                _drop_count,
                _QUEUE_MAX,
            )


async def _observe_message(_, message: Any) -> None:
    _enqueue(_message_payload(message, "message"))


async def _observe_edited_message(_, message: Any) -> None:
    _enqueue(_message_payload(message, "edited_message"))


async def _observe_callback(_, query: Any) -> None:
    _enqueue(_callback_payload(query))


def add_v150_shadow_handlers(client: Any) -> bool:
    """Install observe-only handlers. No Telegram send/edit API is reachable here."""
    global _queue, _worker_task
    if not _shadow_enabled():
        LOGGER.info("ATRI_V150_TELEGRAM_SHADOW_DISABLED")
        return False
    if _worker_task is not None:
        return True

    _queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    client.add_handler(
        MessageHandler(_observe_message, filters=filters.incoming),
        group=_HANDLER_GROUP,
    )
    client.add_handler(
        EditedMessageHandler(_observe_edited_message, filters=filters.incoming),
        group=_HANDLER_GROUP,
    )
    client.add_handler(
        CallbackQueryHandler(_observe_callback),
        group=_HANDLER_GROUP,
    )
    _worker_task = bot_loop.create_task(
        _shadow_worker(),
        name="atri-v150-telegram-shadow",
    )
    LOGGER.info(
        "ATRI_V150_TELEGRAM_SHADOW_ENABLED url=%s group=%s queue=%s mode=observe-only",
        _shadow_url(),
        _HANDLER_GROUP,
        _QUEUE_MAX,
    )
    return True
