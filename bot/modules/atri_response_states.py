from __future__ import annotations

# ATRI_VISUAL_RESPONSE_STATES_V165

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from bot import LOGGER


_ASSET_DIR = Path("/app/atri_data/atri_response_states")
_CACHE_PATH = _ASSET_DIR / "telegram_file_ids.json"

_ASSETS = {
    "thinking": _ASSET_DIR / "thinking.mp4",
    "solved": _ASSET_DIR / "solved.mp4",
    "error": _ASSET_DIR / "error.mp4",
    "confused": _ASSET_DIR / "confused.jpg",
}

_CACHE_LOCK = asyncio.Lock()


def _load_file_ids() -> dict[str, str]:
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    return {
        str(key): str(value)
        for key, value in raw.items()
        if value
    }


async def _save_file_id(kind: str, file_id: str) -> None:
    if not file_id:
        return

    async with _CACHE_LOCK:
        data = _load_file_ids()
        data[str(kind)] = str(file_id)

        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(_CACHE_PATH)


async def _drop_file_id(kind: str) -> None:
    async with _CACHE_LOCK:
        data = _load_file_ids()
        if str(kind) not in data:
            return

        data.pop(str(kind), None)

        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(_CACHE_PATH)


def _media_file_id(message: Any, kind: str) -> str:
    if kind == "confused":
        media = getattr(message, "photo", None)
    else:
        media = getattr(message, "video", None)

    return str(getattr(media, "file_id", "") or "")


def _normalize_error_code(value: Any) -> int:
    try:
        code = int(value)
    except Exception:
        match = re.search(r"\b([45]\d{2})\b", str(value or ""))
        code = int(match.group(1)) if match else 500

    if code < 100 or code > 599:
        return 500

    return code


def _looks_confused(text: str) -> bool:
    normalized = " ".join(
        str(text or "").casefold().split()
    )

    if not normalized:
        return False

    hard_cues = (
        "em chưa rõ",
        "em chưa hiểu",
        "chưa đủ thông tin",
        "chưa có đủ thông tin",
        "cần thêm thông tin",
        "thiếu thông tin",
        "em cần thêm",
    )

    if any(cue in normalized for cue in hard_cues):
        return True

    if len(normalized) > 800 or "?" not in normalized:
        return False

    soft_cues = (
        "ý prix là",
        "ý anh/chị là",
        "ý anh là",
        "ý chị là",
        "cho em biết thêm",
        "gửi thêm",
        "cụ thể hơn",
        "làm rõ",
        "anh/chị muốn",
        "prix muốn",
    )

    return any(cue in normalized for cue in soft_cues)


def _split_caption(text: str, limit: int = 1000) -> tuple[str, str]:
    text = str(text or "").strip()

    if len(text) <= limit:
        return text, ""

    cut = max(
        text.rfind("\n", 0, limit),
        text.rfind(" ", 0, limit),
    )

    if cut < 400:
        cut = limit

    return (
        text[:cut].rstrip(),
        text[cut:].lstrip(),
    )


def _split_text(text: str, limit: int = 3900) -> list[str]:
    text = str(text or "").strip()
    chunks: list[str] = []

    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        cut = max(
            text.rfind("\n\n", 0, limit),
            text.rfind("\n", 0, limit),
            text.rfind(" ", 0, limit),
        )

        if cut < 1000:
            cut = limit

        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    return chunks


class AtriResponseState:
    def __init__(
        self,
        source_message,
        *,
        enabled: bool = True,
    ) -> None:
        self.source_message = source_message
        self.enabled = bool(enabled)
        self.thinking_message = None
        self._lock = asyncio.Lock()

    async def _send_media(
        self,
        kind: str,
        *,
        caption: str = "",
    ):
        path = _ASSETS[kind]

        if not path.is_file():
            raise FileNotFoundError(path)

        cached = _load_file_ids().get(kind, "")
        payload = cached or str(path)

        async def _send(value: str):
            if kind == "confused":
                return await self.source_message.reply_photo(
                    photo=value,
                    caption=caption or None,
                    quote=True,
                    parse_mode=None,
                )

            return await self.source_message.reply_video(
                video=value,
                caption=caption or None,
                quote=True,
                parse_mode=None,
                supports_streaming=True,
            )

        try:
            sent = await _send(payload)
        except Exception:
            if not cached:
                raise

            LOGGER.warning(
                "ATRI_RESPONSE_STATE_FILE_ID_STALE kind=%s",
                kind,
                exc_info=True,
            )
            await _drop_file_id(kind)
            sent = await _send(str(path))

        file_id = _media_file_id(sent, kind)

        if file_id and file_id != cached:
            try:
                await _save_file_id(kind, file_id)
            except Exception:
                LOGGER.warning(
                    "ATRI_RESPONSE_STATE_FILE_ID_CACHE_FAIL kind=%s",
                    kind,
                    exc_info=True,
                )

        return sent

    async def show_thinking(self):
        if not self.enabled:
            return None

        async with self._lock:
            if self.thinking_message is not None:
                return self.thinking_message

            try:
                self.thinking_message = await self._send_media(
                    "thinking",
                    caption="Hmm...",
                )
                LOGGER.info(
                    "ATRI_RESPONSE_STATE state=thinking"
                )
            except Exception:
                LOGGER.exception(
                    "ATRI_RESPONSE_STATE_THINKING_SEND_FAIL"
                )

            return self.thinking_message

    async def _delete_thinking(self) -> None:
        async with self._lock:
            message = self.thinking_message
            self.thinking_message = None

        if message is None:
            return

        try:
            await message.delete()
        except Exception:
            LOGGER.warning(
                "ATRI_RESPONSE_STATE_THINKING_DELETE_FAIL",
                exc_info=True,
            )

    async def _send_remaining_text(
        self,
        anchor_message,
        text: str,
    ) -> None:
        for chunk in _split_text(text):
            try:
                await anchor_message.reply_text(
                    chunk,
                    quote=True,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
            except Exception:
                LOGGER.exception(
                    "ATRI_RESPONSE_STATE_REMAINDER_SEND_FAIL"
                )
                break

    async def finalize(self, text: str) -> None:
        clean_text = str(text or "").strip()

        await self._delete_thinking()

        state = (
            "confused"
            if _looks_confused(clean_text)
            else "solved"
        )

        caption, remaining = _split_caption(clean_text)

        if not caption and state == "solved":
            caption = "Xong rồi."

        try:
            sent = await self._send_media(
                state,
                caption=caption,
            )
            LOGGER.info(
                "ATRI_RESPONSE_STATE state=%s chars=%s",
                state,
                len(clean_text),
            )

            if remaining:
                await self._send_remaining_text(
                    sent,
                    remaining,
                )

        except Exception:
            LOGGER.exception(
                "ATRI_RESPONSE_STATE_FINAL_SEND_FAIL state=%s",
                state,
            )

            if clean_text:
                await self._send_remaining_text(
                    self.source_message,
                    clean_text,
                )

    async def finalize_error(
        self,
        status_code: Any = None,
    ) -> None:
        await self._delete_thinking()

        code = _normalize_error_code(status_code)
        text = f"Em không ổn rồi ({code})"

        try:
            await self._send_media(
                "error",
                caption=text,
            )
            LOGGER.info(
                "ATRI_RESPONSE_STATE state=error code=%s",
                code,
            )
        except Exception:
            LOGGER.exception(
                "ATRI_RESPONSE_STATE_ERROR_SEND_FAIL code=%s",
                code,
            )
            try:
                await self.source_message.reply_text(
                    text,
                    quote=True,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
            except Exception:
                LOGGER.exception(
                    "ATRI_RESPONSE_STATE_ERROR_TEXT_FALLBACK_FAIL"
                )
