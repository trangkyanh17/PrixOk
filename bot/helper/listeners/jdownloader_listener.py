from asyncio import sleep

from ... import LOGGER, intervals, jd_listener_lock, jd_downloads
from ..ext_utils.bot_utils import new_task
from ...core.jdownloader_booter import jdownloader
from ..ext_utils.status_utils import get_task_by_gid


@new_task
async def remove_download(gid):
    if intervals["stopAll"]:
        return
    download = jd_downloads.get(gid)
    if download is None:
        return
    await jdownloader.device.downloads.remove_links(
        package_ids=download.get("ids", [])
    )
    if task := await get_task_by_gid(gid):
        await task.listener.on_download_error("Download removed manually!")
        async with jd_listener_lock:
            jd_downloads.pop(gid, None)


@new_task
async def _on_download_complete(gid):
    if task := await get_task_by_gid(gid):
        if task.listener.select:
            async with jd_listener_lock:
                await jdownloader.device.downloads.cleanup(
                    "DELETE_DISABLED",
                    "REMOVE_LINKS_AND_DELETE_FILES",
                    "ALL",
                    package_ids=jd_downloads[gid]["ids"],
                )
        await task.listener.on_download_complete()
        if intervals["stopAll"]:
            return
        async with jd_listener_lock:
            if gid in jd_downloads:
                await jdownloader.device.downloads.remove_links(
                    package_ids=jd_downloads[gid]["ids"],
                )
                del jd_downloads[gid]


@new_task
async def _jd_listener():
    while True:
        await sleep(3)
        async with jd_listener_lock:
            if len(jd_downloads) == 0:
                intervals["jd"] = ""
                break
            try:
                packages = await jdownloader.device.downloads.query_packages(
                    [{"finished": True, "saveTo": True}]
                )
            except Exception as exc:
                LOGGER.warning("JDownloader listener query failed: %s", exc)
                continue

            all_packages = {pack["uuid"]: pack for pack in packages}
            for d_gid, d_dict in list(jd_downloads.items()):
                if d_dict["status"] == "down":
                    jd_downloads[d_gid]["ids"] = [
                        pid for pid in d_dict.get("ids", []) if pid in all_packages
                    ]
                    if len(jd_downloads[d_gid]["ids"]) == 0:
                        path = jd_downloads[d_gid]["path"]
                        jd_downloads[d_gid]["ids"] = [
                            uid
                            for uid, pk in all_packages.items()
                            if pk["saveTo"].startswith(path)
                        ]
                    if len(jd_downloads[d_gid]["ids"]) == 0:
                        await remove_download(d_gid)

            if completed_packages := [
                pack["uuid"] for pack in packages if pack.get("finished", False)
            ]:
                for d_gid, d_dict in list(jd_downloads.items()):
                    if d_dict["status"] == "down":
                        is_finished = all(
                            did in completed_packages for did in d_dict["ids"]
                        )
                        if is_finished:
                            jd_downloads[d_gid]["status"] = "done"
                            await _on_download_complete(d_gid)


async def on_download_start():
    async with jd_listener_lock:
        if not intervals["jd"]:
            intervals["jd"] = await _jd_listener()
