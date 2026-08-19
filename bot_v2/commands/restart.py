from __future__ import annotations

from asyncio import create_subprocess_exec, gather
from os import execl as osexecl
from sys import executable

from aiofiles import open as aiopen

from bot import intervals, jdownloader, sabnzbd_client, scheduler
from bot.core.telegram_manager import TgClient
from bot.core.torrent_manager import TorrentManager
from bot.helper.ext_utils.files_utils import clean_all
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import delete_message, send_message

from bot_v2.tasks import SUPERVISOR


async def restart_bot(_, message) -> None:
    """Prompt for a v2 restart without the legacy ``new_task`` wrapper."""

    buttons = ButtonMaker()
    buttons.data_button("Yes!", "botrestart confirm")
    buttons.data_button("Cancel", "botrestart cancel")
    await send_message(
        message,
        "Are you sure you want to restart the bot ?!",
        buttons.build_menu(2),
    )


async def confirm_restart(_, query) -> None:
    """Perform controlled cleanup and exec the v2 entrypoint again."""

    await query.answer()
    data = str(getattr(query, "data", "") or "").split()
    message = query.message

    if len(data) < 2 or data[1] != "confirm":
        await delete_message(message)
        return

    reply_to = message.reply_to_message
    intervals["stopAll"] = True
    restart_message = await send_message(reply_to, "Restarting...")
    await delete_message(message)

    # Stop v2-owned long-running tasks before transport and downloader teardown.
    await SUPERVISOR.shutdown(timeout=5.0)
    await TgClient.stop()

    if scheduler.running:
        scheduler.shutdown(wait=False)
    if qb := intervals["qb"]:
        qb.cancel()
    if jd := intervals["jd"]:
        jd.cancel()
    if nzb := intervals["nzb"]:
        nzb.cancel()
    if st := intervals["status"]:
        for interval in list(st.values()):
            interval.cancel()

    await clean_all()
    await TorrentManager.close_all()

    if sabnzbd_client.LOGGED_IN:
        await gather(
            sabnzbd_client.pause_all(),
            sabnzbd_client.delete_job("all", True),
            sabnzbd_client.purge_all(True),
            sabnzbd_client.delete_history("all", delete_files=True),
        )
        await sabnzbd_client.close()

    if jdownloader.is_connected:
        await gather(
            jdownloader.device.downloadcontroller.stop_downloads(),
            jdownloader.device.linkgrabber.clear_list(),
            jdownloader.device.downloads.cleanup(
                "DELETE_ALL",
                "REMOVE_LINKS_AND_DELETE_FILES",
                "ALL",
            ),
        )
        await jdownloader.close()

    proc1 = await create_subprocess_exec(
        "pkill",
        "-9",
        "-f",
        "gunicorn|aria2c|qbittorrent-nox|ffmpeg|rclone|java|sabnzbdplus|7z|split",
    )
    proc2 = await create_subprocess_exec("python3", "update.py")
    await gather(proc1.wait(), proc2.wait())

    async with aiopen(".restartmsg", "w") as handle:
        await handle.write(
            f"{restart_message.chat.id}\n{restart_message.id}\n"
        )

    # Critical v2 invariant: a v2 restart must never fall back to legacy v1.
    osexecl(executable, executable, "-m", "bot_v2")
