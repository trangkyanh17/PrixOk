from __future__ import annotations

from pathlib import Path
from time import monotonic_ns

from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.filters import CustomFilters


async def start(client, message) -> None:
    """Native v2 start command with one direct response pipeline."""

    buttons = ButtonMaker()
    buttons.url_button(
        "Repo",
        "https://www.github.com/anasty17/mirror-leech-telegram-bot",
    )
    buttons.url_button("Code Owner", "https://t.me/anas_tayyar")

    user = getattr(message, "from_user", None)
    if user is not None:
        buttons.data_button(
            "📋 Command Center",
            f"acui:{int(user.id)}:main",
        )

    reply_markup = buttons.build_menu(2)
    authorized = await CustomFilters.authorized(client, message)

    if authorized:
        text = (
            "This bot can mirror from links|tgfiles|torrents|nzb|rclone-cloud "
            "to any rclone cloud, Google Drive or to telegram.\n"
            f"Type /{BotCommands.HelpCommand} to get a list of available commands"
        )
    else:
        text = (
            "This bot can mirror from links|tgfiles|torrents|nzb|rclone-cloud "
            "to any rclone cloud, Google Drive or to telegram.\n\n"
            "⚠️ You Are not authorized user! Deploy your own mirror-leech bot"
        )

    await message.reply_text(
        text,
        reply_markup=reply_markup,
        disable_notification=True,
    )


async def ping(_, message) -> None:
    """Native v2 ping command.

    The legacy implementation is decorated with ``new_task`` and therefore
    schedules a second coroutine after the Telegram dispatcher calls the
    handler. v2 keeps the callback itself awaitable so one dispatcher
    invocation maps to one reply/edit pipeline and is directly observable by
    tests and handler inventory.
    """

    started = monotonic_ns()
    reply = await message.reply_text(
        "Starting Ping",
        quote=True,
        parse_mode=None,
    )
    elapsed_ms = max(0, (monotonic_ns() - started) // 1_000_000)
    await reply.edit_text(
        f"{elapsed_ms} ms",
        parse_mode=None,
    )


async def log(_, message) -> None:
    """Send the current log without the legacy fire-and-forget wrapper."""

    log_path = Path("log.txt")
    if not log_path.is_file():
        await message.reply_text(
            "log.txt chưa tồn tại.",
            quote=True,
            parse_mode=None,
        )
        return

    await message.reply_document(
        document=str(log_path),
        caption="PrixOk log",
        disable_notification=True,
    )
