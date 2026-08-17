from . import LOGGER, bot_loop
from .core.telegram_manager import TgClient
from .core.config_manager import Config
from .modules.atri_network_egress_guard import install_atri_early_network_guard

# ATRI_PRODUCTION_WORKER_SINGLETON_V133
import fcntl as _atri_v133_fcntl
import hashlib as _atri_v133_hashlib
import os as _atri_v133_os
from pathlib import Path as _AtriV133Path

_ATRI_V133_LOCK_PATH = _AtriV133Path("/app/.atri-prixok-bot-v133.lock")
_ATRI_V133_LOCK_HANDLE = _ATRI_V133_LOCK_PATH.open("a+", encoding="utf-8")
try:
    _atri_v133_fcntl.flock(
        _ATRI_V133_LOCK_HANDLE.fileno(),
        _atri_v133_fcntl.LOCK_EX | _atri_v133_fcntl.LOCK_NB,
    )
except BlockingIOError:
    LOGGER.error(
        "ATRI_PRODUCTION_WORKER_V133_DUPLICATE_BLOCKED pid=%s lock=%s",
        _atri_v133_os.getpid(),
        _ATRI_V133_LOCK_PATH,
    )
    raise SystemExit(73)

_ATRI_V133_LOCK_HANDLE.seek(0)
_ATRI_V133_LOCK_HANDLE.truncate()
_ATRI_V133_LOCK_HANDLE.write(str(_atri_v133_os.getpid()) + "\n")
_ATRI_V133_LOCK_HANDLE.flush()
_atri_v133_os.fchmod(_ATRI_V133_LOCK_HANDLE.fileno(), 0o600)

_ATRI_V133_AI_PATH = _AtriV133Path(__file__).resolve().parent / "modules" / "atri_ai.py"
_ATRI_V133_REQUIRED_AI_MARKERS = (
    "ATRI_DOCUMENT_EXECUTION_BRIDGE_V128",
    "ATRI_DOCUMENT_TELEGRAM_SENDER_V128",
    "ATRI_DOCUMENT_PROGRESSIVE_FINALIZER_V132",
)
_atri_v133_ai_bytes = _ATRI_V133_AI_PATH.read_bytes()
_atri_v133_ai_text = _atri_v133_ai_bytes.decode("utf-8")
_atri_v133_missing = [
    marker
    for marker in _ATRI_V133_REQUIRED_AI_MARKERS
    if _atri_v133_ai_text.count(marker) != 1
]
if _atri_v133_missing:
    LOGGER.critical(
        "ATRI_PRODUCTION_WORKER_V133_SOURCE_REJECTED markers=%s",
        ",".join(_atri_v133_missing),
    )
    raise RuntimeError("ATRI_V133_REQUIRED_DOCUMENT_MARKERS_INVALID")

_ATRI_V133_AI_SHA256 = _atri_v133_hashlib.sha256(_atri_v133_ai_bytes).hexdigest()
_ATRI_V133_MAIN_SHA256 = _atri_v133_hashlib.sha256(
    _AtriV133Path(__file__).resolve().read_bytes()
).hexdigest()
LOGGER.info(
    "ATRI_PRODUCTION_WORKER_V133_READY pid=%s main_sha256=%s ai_sha256=%s",
    _atri_v133_os.getpid(),
    _ATRI_V133_MAIN_SHA256,
    _ATRI_V133_AI_SHA256,
)

Config.load()
# V155 MyJD guard must exist before main() can boot JDownloader.
install_atri_early_network_guard()


async def main():
    from asyncio import gather
    from .core.startup import (
        load_settings,
        load_configurations,
        save_settings,
        update_aria2_options,
        update_nzb_options,
        update_qb_options,
        update_variables,
    )

    await load_settings()

    await gather(TgClient.start_bot(), TgClient.start_user())
    await gather(load_configurations(), update_variables())

    from .core.torrent_manager import TorrentManager

    await TorrentManager.initiate()
    await gather(
        update_qb_options(),
        update_aria2_options(),
        update_nzb_options(),
    )
    from .helper.ext_utils.files_utils import clean_all
    from .core.jdownloader_booter import jdownloader
    from .helper.ext_utils.telegraph_helper import telegraph
    from .helper.mirror_leech_utils.rclone_utils.serve import rclone_serve_booter
    from .modules import (
        initiate_search_tools,
        get_packages_version,
        restart_notification,
    )

    await gather(
        save_settings(),
        jdownloader.boot(),
        clean_all(),
        initiate_search_tools(),
        get_packages_version(),
        restart_notification(),
        telegraph.create_account(),
        rclone_serve_booter(),
    )


bot_loop.run_until_complete(main())

from .helper.ext_utils.bot_utils import create_help_buttons
from .helper.listeners.aria2_listener import add_aria2_callbacks
from .core.handlers import add_handlers
from .modules.atri_v150_shadow import add_v150_shadow_handlers
from .modules.atri_system_guard import install_atri_system_post_import_guard
from .modules.atri_network_egress_guard import install_atri_network_egress_guard
from .modules.atri_capability_bootstrap import (
    add_capability_runtime_handlers,
    install_capability_runtime,
)
from .modules.atri_response_engine import install_atri_natural_response_engine

add_aria2_callbacks()
create_help_buttons()
# V155 public-egress guard must be active before request handlers.
install_atri_network_egress_guard()
# V157 patches the already-imported Atri handler/skill aliases before Telegram
# registration so routing, permissions, project context and job tracking are
# effective on the first user message without changing atri_ai.py's guarded core.
install_capability_runtime()
# V167 layers response planning/persona/naturalness over the proven Atri core
# after V157 has finished patching routing and before Telegram registers handlers.
install_atri_natural_response_engine()
add_handlers()
add_capability_runtime_handlers(TgClient.bot)
install_atri_system_post_import_guard()
add_v150_shadow_handlers(TgClient.bot)

# Warm Semgrep MCP in the background during bot startup so users do not
# pay uvx/MCP initialization latency on their first Semgrep request.
from .modules.atri_tools.code_plugins import (
    prewarm_remaining_code_plugins,
    prewarm_semgrep_mcp,
)

bot_loop.create_task(
    prewarm_semgrep_mcp(),
    name="atri-semgrep-boot-prewarm",
)
LOGGER.info("SEMGREP_MCP_BOOT_PREWARM_SCHEDULED")

bot_loop.create_task(
    prewarm_remaining_code_plugins(),
    name="atri-mcp-remaining-boot-prewarm",
)
LOGGER.info("MCP_REMAINING_BOOT_PREWARM_SCHEDULED")

LOGGER.info("Bot Started!")
# ATRI_PRODUCTION_WORKER_ONLINE_V133
LOGGER.info(
    "ATRI_PRODUCTION_WORKER_V133_ONLINE pid=%s main_sha256=%s ai_sha256=%s",
    _atri_v133_os.getpid(),
    _ATRI_V133_MAIN_SHA256,
    _ATRI_V133_AI_SHA256,
)
bot_loop.run_forever()
