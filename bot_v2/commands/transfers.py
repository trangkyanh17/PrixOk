from __future__ import annotations

from bot import LOGGER
from bot.modules.atri_media_direct import MediaDirectYtDlp
from bot.modules.gallery_dl import GalleryDL
from bot.modules.mirror_leech import Mirror
from bot.modules.ytdlp import YtDlp

from bot_v2.tasks import SUPERVISOR


def _message_id(message) -> int:
    try:
        return int(getattr(message, "id", 0) or 0)
    except Exception:
        return 0


def _chat_id(message) -> int:
    try:
        return int(getattr(getattr(message, "chat", None), "id", 0) or 0)
    except Exception:
        return 0


def _spawn(operation, *, route: str, message) -> None:
    chat_id = _chat_id(message)
    message_id = _message_id(message)
    task_name = f"prixok-v2:{route}:{chat_id}:{message_id}"
    SUPERVISOR.spawn(operation.new_event(), name=task_name)
    LOGGER.info(
        "PRIXOK_V2_TRANSFER_DISPATCH route=%s chat=%s message=%s",
        route,
        chat_id,
        message_id,
    )


async def mirror(client, message) -> None:
    _spawn(Mirror(client, message), route="mirror", message=message)


async def qb_mirror(client, message) -> None:
    _spawn(
        Mirror(client, message, is_qbit=True),
        route="qb-mirror",
        message=message,
    )


async def jd_mirror(client, message) -> None:
    _spawn(
        Mirror(client, message, is_jd=True),
        route="jd-mirror",
        message=message,
    )


async def nzb_mirror(client, message) -> None:
    _spawn(
        Mirror(client, message, is_nzb=True),
        route="nzb-mirror",
        message=message,
    )


async def leech(client, message) -> None:
    _spawn(
        Mirror(client, message, is_leech=True),
        route="leech",
        message=message,
    )


async def qb_leech(client, message) -> None:
    _spawn(
        Mirror(client, message, is_qbit=True, is_leech=True),
        route="qb-leech",
        message=message,
    )


async def jd_leech(client, message) -> None:
    _spawn(
        Mirror(client, message, is_jd=True, is_leech=True),
        route="jd-leech",
        message=message,
    )


async def nzb_leech(client, message) -> None:
    _spawn(
        Mirror(client, message, is_nzb=True, is_leech=True),
        route="nzb-leech",
        message=message,
    )


async def ytdl(client, message) -> None:
    _spawn(YtDlp(client, message), route="ytdl", message=message)


async def ytdl_leech(client, message) -> None:
    _spawn(
        YtDlp(client, message, is_leech=True),
        route="ytdl-leech",
        message=message,
    )


async def gallery_dl(client, message) -> None:
    _spawn(GalleryDL(client, message), route="gallery-dl", message=message)


async def gallery_dl_leech(client, message) -> None:
    _spawn(
        GalleryDL(client, message, is_leech=True),
        route="gallery-dl-leech",
        message=message,
    )


async def media_direct(client, message) -> None:
    _spawn(
        MediaDirectYtDlp(client, message),
        route="media-direct",
        message=message,
    )
