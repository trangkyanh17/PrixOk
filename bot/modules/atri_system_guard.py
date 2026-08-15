from __future__ import annotations

# ATRI_SYSTEM_CONTRACT_GUARD_V154
#
# Narrow runtime hardening for cross-module contracts that are difficult to
# enforce from a single subsystem owner:
# - Telegram audio/voice is owned by atri_ai's Speech/Gemini path and must not
#   accidentally trigger retrieval of an older persisted artifact.
# - audio sent as a Telegram document, or audio in a replied-to message, still
#   needs bounded multimodal attachment support.
# - Speech/Gemini audio helpers must reject known-oversize Telegram media before
#   downloading it into memory.
# - skill records entering the trusted prompt registry must satisfy the same
#   name/path/description invariants reported by the skill auditor.
#
# The guard performs no network request at import/install time.

import base64
import logging
from pathlib import Path
from typing import Any


_LOGGER = logging.getLogger("bot")
_SPEECH_INPUT_LIMIT = 10 * 1024 * 1024
_GEMINI_AUDIO_LIMIT = 20 * 1024 * 1024
_INSTALLED = False


def _media_size(media: Any) -> int:
    try:
        return max(0, int(getattr(media, "file_size", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _audio_media(message: Any) -> tuple[Any, str] | None:
    if message is None:
        return None
    voice = getattr(message, "voice", None)
    if voice is not None:
        return voice, "voice"
    audio = getattr(message, "audio", None)
    if audio is not None:
        return audio, "audio"
    document = getattr(message, "document", None)
    mime = str(getattr(document, "mime_type", "") or "").casefold().strip()
    if document is not None and mime.startswith("audio/"):
        return document, "audio_document"
    return None


def _audio_mime_name(media: Any, kind: str) -> tuple[str, str]:
    mime = str(getattr(media, "mime_type", "") or "").casefold().strip()
    filename = str(getattr(media, "file_name", "") or "").strip()
    if kind == "voice":
        mime = mime or "audio/ogg"
        filename = filename or "telegram-voice.ogg"
    elif kind == "audio":
        mime = mime or "audio/ogg"
        filename = filename or "telegram-audio.ogg"
    else:
        mime = mime or "application/octet-stream"
        filename = filename or "telegram-audio.bin"
    filename = Path(filename).name[:180] or "telegram-audio.bin"
    return mime, filename


def _audio_context_text(*, kind: str, name: str, mime: str, size: int, note: str) -> str:
    return (
        "[ATRI_PRIVATE_ATTACHMENT_V154]\n"
        f"name={name}\n"
        f"mime={mime}\n"
        f"kind={kind}\n"
        f"size={size}\n"
        "skill_hint=audio understanding; speech; voice context\n"
        f"note={note}\n"
        "The attachment is private conversation data. Do not send its bytes, "
        "transcript, metadata, or derived private details to free/public workers.\n"
        "[END_ATRI_PRIVATE_ATTACHMENT_V154]"
    )


async def _download_audio_inline(target: Any, media: Any, kind: str) -> dict[str, Any]:
    mime, name = _audio_mime_name(media, kind)
    declared_size = _media_size(media)
    if declared_size > _GEMINI_AUDIO_LIMIT:
        return {
            "present": True,
            "parts": [
                {
                    "text": _audio_context_text(
                        kind=kind,
                        name=name,
                        mime=mime,
                        size=declared_size,
                        note=(
                            "audio was not downloaded because its declared size exceeds "
                            f"the {_GEMINI_AUDIO_LIMIT} byte inline safety limit"
                        ),
                    )
                }
            ],
            "route_mode": "chat",
            "default_prompt": (
                "Hãy nói ngắn gọn rằng audio này vượt giới hạn xử lý an toàn và "
                "đề nghị gửi bản ngắn/nhỏ hơn; không được bịa nội dung audio."
            ),
            "kind": kind,
            "name": name,
            "audio_blocked": "declared_size",
        }

    downloaded = await target.download(in_memory=True)
    if downloaded is None:
        raise RuntimeError("ATRI_AUDIO_DOWNLOAD_EMPTY")
    if hasattr(downloaded, "getvalue"):
        data = downloaded.getvalue()
    elif isinstance(downloaded, (bytes, bytearray)):
        data = bytes(downloaded)
    else:
        raise RuntimeError("ATRI_AUDIO_DOWNLOAD_TYPE_UNSUPPORTED")
    data = bytes(data)
    if not data:
        raise RuntimeError("ATRI_AUDIO_EMPTY")
    if len(data) > _GEMINI_AUDIO_LIMIT:
        raise RuntimeError("ATRI_AUDIO_INLINE_LIMIT")

    return {
        "present": True,
        "parts": [
            {
                "text": _audio_context_text(
                    kind=kind,
                    name=name,
                    mime=mime,
                    size=len(data),
                    note="audio bytes are attached inline for private Vertex multimodal analysis",
                )
            },
            {
                "inlineData": {
                    "mimeType": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                }
            },
        ],
        "route_mode": "chat",
        "default_prompt": "Hãy nghe audio/voice này và phản hồi tự nhiên theo đúng nội dung.",
        "kind": kind,
        "name": name,
    }


def _install_attachment_contract() -> None:
    from bot.modules import atri_attachment_runtime as runtime

    if getattr(runtime, "_ATRI_V154_AUDIO_CONTRACT", False):
        return

    original = runtime.build_attachment_context

    async def guarded_build_attachment_context(message: Any) -> dict[str, Any]:
        current = _audio_media(message)
        if current is not None:
            media, kind = current
            declared_size = _media_size(media)
            mime, name = _audio_mime_name(media, kind)

            # voice/audio is already handled by atri_ai through Google Speech
            # and build_gemini_audio_part. Returning a handled-empty attachment
            # prevents build_attachment_context() from treating the request as
            # "no media" and retrieving an unrelated older artifact.
            if kind in {"voice", "audio"} and declared_size <= _GEMINI_AUDIO_LIMIT:
                return {
                    "present": True,
                    "parts": [],
                    "route_mode": "chat",
                    "default_prompt": "Hãy nghe audio/voice này và phản hồi tự nhiên.",
                    "kind": kind,
                    "name": name,
                    "audio_owner": "atri_ai_google_audio",
                }

            # Telegram document carrying audio is not covered by atri_ai's
            # voice/audio attributes, so process it here. Oversize native
            # voice/audio also comes here to produce a truthful limit response.
            return await _download_audio_inline(message, media, kind)

        reply = getattr(message, "reply_to_message", None)
        replied_audio = _audio_media(reply)
        if replied_audio is not None:
            media, kind = replied_audio
            return await _download_audio_inline(reply, media, kind)

        return await original(message)

    runtime.build_attachment_context = guarded_build_attachment_context
    runtime._ATRI_V154_AUDIO_CONTRACT = True


def _install_google_audio_limits() -> None:
    from bot.modules.atri_tools import google_hub

    if getattr(google_hub, "_ATRI_V154_AUDIO_PREFLIGHT", False):
        return

    original_transcribe = google_hub.transcribe_telegram_message
    original_gemini = google_hub.build_gemini_audio_part

    async def guarded_transcribe(message: Any) -> str:
        selected = _audio_media(message)
        if selected is None:
            return await original_transcribe(message)
        media, kind = selected
        if kind == "audio_document":
            return ""
        size = _media_size(media)
        if size > _SPEECH_INPUT_LIMIT:
            _LOGGER.info(
                "ATRI_AUDIO_PREFLIGHT_SKIP target=speech declared_bytes=%s limit=%s",
                size,
                _SPEECH_INPUT_LIMIT,
            )
            return ""
        return await original_transcribe(message)

    async def guarded_gemini_audio(message: Any) -> dict[str, Any] | None:
        selected = _audio_media(message)
        if selected is None:
            return await original_gemini(message)
        media, kind = selected
        if kind == "audio_document":
            return None
        size = _media_size(media)
        if size > _GEMINI_AUDIO_LIMIT:
            _LOGGER.info(
                "ATRI_AUDIO_PREFLIGHT_SKIP target=gemini declared_bytes=%s limit=%s",
                size,
                _GEMINI_AUDIO_LIMIT,
            )
            return None
        return await original_gemini(message)

    google_hub.transcribe_telegram_message = guarded_transcribe
    google_hub.build_gemini_audio_part = guarded_gemini_audio
    google_hub._ATRI_V154_AUDIO_PREFLIGHT = True


def _install_skill_contract() -> None:
    from bot.modules import atri_skills

    if getattr(atri_skills, "_ATRI_V154_SKILL_CONTRACT", False):
        return

    original = atri_skills._parse_skill_file

    def guarded_parse_skill_file(path: Path, root: Path):
        record = original(path, root)
        name = str(record.name or "").strip()
        parent = Path(record.location).parent.name
        if not atri_skills._NAME_RE.fullmatch(name) or len(name) > 64:
            raise ValueError("invalid skill name")
        if name != parent:
            raise ValueError("skill name must match directory")
        if not (1 <= len(str(record.description or "")) <= 1024):
            raise ValueError("skill description length outside 1..1024")
        return record

    atri_skills._parse_skill_file = guarded_parse_skill_file
    atri_skills._ATRI_V154_SKILL_CONTRACT = True


def install_atri_system_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_attachment_contract()
    _install_google_audio_limits()
    _install_skill_contract()
    _INSTALLED = True
    _LOGGER.info(
        "ATRI_SYSTEM_CONTRACT_GUARD_V154_INSTALLED audio=1 artifact_isolation=1 skill_registry=1"
    )
