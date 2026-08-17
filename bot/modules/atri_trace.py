from __future__ import annotations

# ATRI_TRACE_OBSERVABILITY_V16261
import contextvars
import logging
from typing import Any

_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "atri_request_trace_id",
    default="",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def trace_id_for_message(message: Any) -> str:
    chat = getattr(message, "chat", None)
    user = getattr(message, "from_user", None)
    chat_id = _safe_int(
        getattr(chat, "id", None) if chat is not None else getattr(message, "chat_id", None),
        0,
    )
    message_id = _safe_int(
        getattr(message, "id", None) or getattr(message, "message_id", None),
        0,
    )
    user_id = _safe_int(getattr(user, "id", None) if user is not None else None, 0)
    return f"tg:{chat_id}:{message_id}:{user_id}"


def begin_trace(message: Any):
    return _TRACE_ID.set(trace_id_for_message(message))


def end_trace(token) -> None:
    try:
        _TRACE_ID.reset(token)
    except Exception:
        _TRACE_ID.set("")


def current_trace_id() -> str:
    return str(_TRACE_ID.get() or "")


class _AtriTraceFilter(logging.Filter):
    _atri_trace_filter_v16261 = True

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = current_trace_id()
        if not trace_id:
            return True
        msg = record.msg
        if not isinstance(msg, str) or not msg.startswith("ATRI_"):
            return True
        if " trace=" in msg:
            return True
        head, sep, tail = msg.partition(" ")
        record.msg = (
            f"{head} trace={trace_id} {tail}"
            if sep
            else f"{head} trace={trace_id}"
        )
        return True


def install_trace_logging(logger: logging.Logger) -> None:
    for item in logger.filters:
        if getattr(item, "_atri_trace_filter_v16261", False):
            return
    logger.addFilter(_AtriTraceFilter())
