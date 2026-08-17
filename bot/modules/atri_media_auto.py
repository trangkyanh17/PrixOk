from __future__ import annotations

from copy import copy

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from bot import LOGGER, bot_loop
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.mirror_leech_utils.download_utils.universal_media_resolver import (
    extract_bare_social_url,
    resolve_media,
)
from bot.modules.ytdlp import YtDlp


# ATRI_UNIVERSAL_MEDIA_AUTO_V163
async def _run_media_auto(client, message, original_url: str) -> None:
    try:
        result = await resolve_media(original_url)

        proxy = copy(message)
        proxy.text = f"/mediaauto {result.resolved_url}"

        LOGGER.info(
            "ATRI_MEDIA_AUTO_DISPATCH platform=%s backend=%s direct=%s",
            result.platform,
            result.backend,
            int(result.direct),
        )

        await YtDlp(
            client,
            proxy,
            is_leech=True,
        ).new_event()

    except Exception as exc:
        LOGGER.exception(
            "ATRI_MEDIA_AUTO_FAIL url=%s error=%s",
            original_url,
            exc,
        )


async def atri_media_auto_message(client, message) -> None:
    url = extract_bare_social_url(message.text)

    if not url:
        return

    # Schedule first; stop_propagation() raises internally and no code after
    # it is expected to run.
    bot_loop.create_task(
        _run_media_auto(
            client,
            message,
            url,
        )
    )

    message.stop_propagation()


def add_atri_media_auto_handlers(client) -> None:
    client.add_handler(
        MessageHandler(
            atri_media_auto_message,
            filters=(
                filters.incoming
                & filters.private
                & filters.text
                & CustomFilters.authorized
            ),
        ),
        group=17,
    )
