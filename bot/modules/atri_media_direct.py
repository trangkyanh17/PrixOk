from __future__ import annotations

from bot import LOGGER, bot_loop

from ..helper.mirror_leech_utils.download_utils.universal_media_resolver import (
    detect_platform,
)
from .ytdlp import YtDlp


# ATRI_MEDIA_DIRECT_V164
# ATRI_MD_CLEAN_UX_V1642
_PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "x": "X",
    "reddit": "Reddit",
    "threads": "Threads",
    "generic": "Source",
}


def _extract_source_url(message) -> str:
    text = str(getattr(message, "text", "") or "")
    first_line = text.split("\n", 1)[0].strip()
    parts = first_line.split(maxsplit=1)

    if len(parts) > 1:
        return parts[1].strip()

    reply = getattr(message, "reply_to_message", None)
    reply_text = str(getattr(reply, "text", "") or "")

    if reply_text:
        return reply_text.split("\n", 1)[0].strip()

    return ""


class MediaDirectYtDlp(YtDlp):
    def __init__(self, client, message):
        super().__init__(
            client,
            message,
            is_leech=True,
        )

        self.is_media_direct = True

        source_url = _extract_source_url(message)
        platform = detect_platform(source_url) if source_url else "generic"

        self.media_direct_source_url = source_url
        self.media_direct_source_platform = platform
        self.media_direct_source_label = _PLATFORM_LABELS.get(
            platform,
            "Source",
        )


async def _run_media_direct(client, message) -> None:
    try:
        await MediaDirectYtDlp(
            client,
            message,
        ).new_event()

    except Exception as exc:
        LOGGER.exception(
            "ATRI_MEDIA_DIRECT_FAIL chat_id=%s message_id=%s error=%s",
            getattr(getattr(message, "chat", None), "id", 0),
            getattr(message, "id", 0),
            exc,
        )


async def media_direct(client, message) -> None:
    LOGGER.info(
        "ATRI_MEDIA_DIRECT_DISPATCH chat_id=%s message_id=%s",
        getattr(getattr(message, "chat", None), "id", 0),
        getattr(message, "id", 0),
    )

    bot_loop.create_task(
        _run_media_direct(
            client,
            message,
        )
    )
