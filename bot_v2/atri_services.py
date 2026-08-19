from __future__ import annotations

from bot import LOGGER
from bot.modules import atri_free_tools as free_tools

from .tasks import SUPERVISOR


async def start_free_tools(client) -> None:
    """Start Atri free-tool services under the v2 task supervisor.

    The legacy starter creates anonymous ``asyncio.create_task`` workers.  v2
    performs the same initialization but gives both persistent workers explicit
    ownership so shutdown and failures are observable.
    """

    if free_tools.STARTED:
        LOGGER.info("PRIXOK_V2_FREE_TOOLS_ALREADY_STARTED")
        return

    await free_tools.db_call(free_tools.db_init)
    free_tools.TMP.mkdir(parents=True, exist_ok=True)
    free_tools.STARTED = True

    SUPERVISOR.spawn(
        free_tools.reminder_worker(client),
        name="prixok-v2-reminder-worker",
    )
    SUPERVISOR.spawn(
        free_tools.merge_menu(client),
        name="prixok-v2-free-tools-menu-sync",
    )

    LOGGER.info(
        "PRIXOK_V2_FREE_TOOLS_STARTED reminder_worker=1 menu_sync=1"
    )
