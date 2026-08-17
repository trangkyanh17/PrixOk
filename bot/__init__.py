from uvloop import install

install()

# SENTRY_RUNTIME_INIT_V1
from os import environ
import sentry_sdk

_sentry_dsn = environ.get("SENTRY_DSN", "").strip()
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        send_default_pii=False,
        enable_logs=True,
        traces_sample_rate=0.1,
        environment=environ.get("SENTRY_ENVIRONMENT", "production"),
        release=environ.get("SENTRY_RELEASE") or None,
    )

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from asyncio import Lock, new_event_loop, set_event_loop
from logging import (
    getLogger,
    FileHandler,
    StreamHandler,
    INFO,
    basicConfig,
    WARNING,
    ERROR,
)
from sabnzbdapi import SabnzbdClient
from time import time
from os import cpu_count

getLogger("requests").setLevel(WARNING)
getLogger("urllib3").setLevel(WARNING)
getLogger("pyrogram").setLevel(ERROR)
getLogger("httpx").setLevel(WARNING)
getLogger("pymongo").setLevel(WARNING)
getLogger("aiohttp").setLevel(WARNING)

bot_start_time = time()

bot_loop = new_event_loop()
set_event_loop(bot_loop)

basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[FileHandler("log.txt"), StreamHandler()],
    level=INFO,
)

LOGGER = getLogger(__name__)
cpu_no = cpu_count()
threads = max(1, cpu_no // 2)
cores = ",".join(str(i) for i in reversed(range(threads)))

DOWNLOAD_DIR = "/app/downloads/"
intervals = {"status": {}, "qb": "", "jd": "", "nzb": "", "stopAll": False}
qb_torrents = {}
jd_downloads = {}
nzb_jobs = {}
user_data = {}
aria2_options = {}
qbit_options = {}
nzb_options = {}
queued_dl = {}
queued_up = {}
status_dict = {}
task_dict = {}
rss_dict = {}
auth_chats = {}
excluded_extensions = ["aria2", "!qB"]
included_extensions = []
drives_names = []
drives_ids = []
index_urls = []
sudo_users = []
non_queued_dl = set()
non_queued_up = set()
multi_tags = set()
task_dict_lock = Lock()
queue_dict_lock = Lock()
qb_listener_lock = Lock()
nzb_listener_lock = Lock()
jd_listener_lock = Lock()
cpu_eater_lock = Lock()
same_directory_lock = Lock()

sabnzbd_client = SabnzbdClient(
    host="http://localhost",
    api_key="mltb",
    port="8070",
)

scheduler = AsyncIOScheduler(event_loop=bot_loop)

# ATRI_AI_RUNTIME_GUARD_V153_BOOT
# Install after LOGGER/event-loop initialization but before bot modules import
# the code-plugin hub. The guard is read-only and does not start any network
# request during import.
try:
    from bot.modules.atri_ai_runtime_guard import install_atri_ai_runtime_guard

    install_atri_ai_runtime_guard()
except Exception:
    LOGGER.exception("ATRI_AI_RUNTIME_GUARD_V153_INSTALL_FAILED")

# ATRI_SYSTEM_CONTRACT_GUARD_V154_BOOT
# Cross-module Atri contracts must be installed before atri_ai imports helper
# functions by value. The guard performs no network request during install.
try:
    from bot.modules.atri_system_guard import install_atri_system_guard

    install_atri_system_guard()
except Exception:
    LOGGER.exception("ATRI_SYSTEM_CONTRACT_GUARD_V154_INSTALL_FAILED")

# ATRI_STICKER_CHAT_PRIVACY_V154_BOOT
# Sticker learning is private conversation-derived state; install chat scoping
# before atri_ai imports sticker helpers by value.
try:
    from bot.modules.atri_sticker_privacy_guard import (
        install_atri_sticker_privacy_guard,
    )

    install_atri_sticker_privacy_guard()
except Exception:
    LOGGER.exception("ATRI_STICKER_CHAT_PRIVACY_V154_INSTALL_FAILED")

# ATRI_WEBAPP_NETWORK_GUARD_V154_BOOT
# The real browser skill must not reach loopback/private networks through a
# public-looking hostname or redirect.
try:
    from bot.modules.atri_webapp_safety_guard import (
        install_atri_webapp_safety_guard,
    )

    install_atri_webapp_safety_guard()
except Exception:
    LOGGER.exception("ATRI_WEBAPP_NETWORK_GUARD_V154_INSTALL_FAILED")

# ATRI_XLSX_FORMULA_SAFETY_V1541_BOOT
# Install before atri_ai imports the skill context by value. Raw spreadsheet
# strings remain data; formulas require the explicit safe formula cell schema.
try:
    from bot.modules.atri_xlsx_formula_guard import (
        install_atri_xlsx_formula_guard,
    )

    install_atri_xlsx_formula_guard()
except Exception:
    LOGGER.exception("ATRI_XLSX_FORMULA_SAFETY_V1541_INSTALL_FAILED")

# ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_BOOT
# Persistent file RAG must be opt-in by relevance: unrelated chat must never
# inherit old artifact chunks or extend their TTL.
try:
    from bot.modules.atri_artifact_relevance_guard import (
        install_atri_artifact_relevance_guard,
    )

    install_atri_artifact_relevance_guard()
except Exception:
    LOGGER.exception("ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALL_FAILED")
