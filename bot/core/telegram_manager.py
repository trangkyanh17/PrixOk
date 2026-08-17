from pyrogram import Client, enums
from pyrogram.errors import FloodWait
from pyrogram.types import LinkPreviewOptions
from asyncio import Lock, sleep

from .. import LOGGER
from .config_manager import Config


class TgClient:
    _lock = Lock()
    bot = None
    user = None
    NAME = ""
    ID = 0
    IS_PREMIUM_USER = False
    MAX_SPLIT_SIZE = 2097152000

    @classmethod
    async def start_bot(cls):
        LOGGER.info("Creating client from BOT_TOKEN")
        cls.ID = Config.BOT_TOKEN.split(":", 1)[0]
        cls.bot = Client(
            cls.ID,
            Config.TELEGRAM_API,
            Config.TELEGRAM_HASH,
            proxy=Config.TG_PROXY,
            bot_token=Config.BOT_TOKEN,
            workdir="/app",
            # V167.4: keep the authorized bot session on disk. V167.1 forced
            # in-memory storage, which made every process restart call
            # auth.ImportBotAuthorization again and amplified Telegram FloodWait.
            in_memory=False,
            parse_mode=enums.ParseMode.HTML,
            max_concurrent_transmissions=10,
            max_message_cache_size=15000,
            max_topic_cache_size=15000,
            sleep_threshold=0,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

        # Kurigram disconnects the client before re-raising a start failure, so
        # retrying the same Client after Telegram's exact FloodWait is safe. Keep
        # the worker, singleton flock and tmux session alive while waiting; do
        # not turn a rate limit into a supervisor-driven authorization storm.
        while True:
            try:
                await cls.bot.start()
                break
            except FloodWait as exc:
                wait_seconds = max(1, int(exc.value))
                LOGGER.warning(
                    "TELEGRAM_BOT_START_FLOOD_WAIT seconds=%s action=wait-in-process",
                    wait_seconds,
                )
                await sleep(wait_seconds + 1)

        cls.NAME = cls.bot.me.username

    @classmethod
    async def start_user(cls):
        if Config.USER_SESSION_STRING:
            LOGGER.info("Creating client from USER_SESSION_STRING")
            try:
                cls.user = Client(
                    "user",
                    Config.TELEGRAM_API,
                    Config.TELEGRAM_HASH,
                    proxy=Config.TG_PROXY,
                    session_string=Config.USER_SESSION_STRING,
                    workdir="/app",
                    parse_mode=enums.ParseMode.HTML,
                    sleep_threshold=60,
                    max_concurrent_transmissions=10,
                    max_message_cache_size=15000,
                    max_topic_cache_size=15000,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                await cls.user.start()
                cls.IS_PREMIUM_USER = cls.user.me.is_premium
                if cls.IS_PREMIUM_USER:
                    cls.MAX_SPLIT_SIZE = 4194304000
            except Exception as e:
                LOGGER.error(f"Failed to start client from USER_SESSION_STRING. {e}")
                cls.IS_PREMIUM_USER = False
                cls.user = None

    @classmethod
    async def stop(cls):
        async with cls._lock:
            if cls.bot:
                await cls.bot.stop()
            if cls.user:
                await cls.user.stop()
            LOGGER.info("Client(s) stopped")

    @classmethod
    async def reload(cls):
        async with cls._lock:
            await cls.bot.restart()
            if cls.user:
                await cls.user.restart()
            LOGGER.info("Client(s) restarted")
