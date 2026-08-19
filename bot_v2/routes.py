from __future__ import annotations

from pyrogram import filters
from pyrogram.filters import command, regex
from pyrogram.handlers import CallbackQueryHandler, EditedMessageHandler, MessageHandler

from bot import LOGGER, bot_loop
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.filters import CustomFilters
from bot.modules import (
    add_sudo,
    aioexecute,
    arg_usage,
    authorize,
    bot_stats,
    cancel,
    cancel_all_buttons,
    cancel_all_update,
    cancel_multi,
    clear,
    clone_node,
    confirm_restart,
    confirm_selection,
    count_node,
    delete_file,
    edit_bot_settings,
    edit_user_settings,
    execute,
    gallery_dl,
    gallery_dl_leech,
    gdrive_search,
    get_rss_menu,
    get_users_settings,
    hydra_search,
    jd_leech,
    jd_mirror,
    leech,
    mirror,
    nzb_leech,
    nzb_mirror,
    qb_leech,
    qb_mirror,
    remove_from_queue,
    remove_sudo,
    restart_bot,
    rss_listener,
    run_shell,
    run_speedtest,
    select,
    select_type,
    send_bot_settings,
    send_user_settings,
    status_pages,
    task_status,
    torrent_search,
    torrent_search_update,
    unauthorize,
    ytdl,
    ytdl_leech,
)
from bot.modules.atri_ai import atri_accept_message, atri_message
from bot.modules.atri_free_tools import atri_free_tools_message
from bot.modules.atri_media_direct import media_direct
from bot.modules.atri_web_tools import atri_tools_message, sync_bot_command_menu

from .commands.core import log, ping, start
from .registry import HandlerRegistry


async def _atri_public_interaction_filter(_, client, message):
    return await atri_accept_message(client, message)


async def _not_slash_command_filter(_, __, message):
    text = str(getattr(message, "text", "") or "").lstrip()
    caption = str(getattr(message, "caption", "") or "").lstrip()
    candidate = text or caption
    return not candidate.startswith("/")


ATRI_PUBLIC_INTERACTION = filters.create(
    _atri_public_interaction_filter,
    name="PrixOkV2AtriPublicInteraction",
)

NOT_SLASH_COMMAND = filters.create(
    _not_slash_command_filter,
    name="PrixOkV2NotSlashCommand",
)


def _message(
    registry: HandlerRegistry,
    route_id: str,
    callback,
    command_value,
    permission=None,
    *,
    group: int = 0,
) -> None:
    route_filter = command(command_value, case_sensitive=True)
    if permission is not None:
        route_filter &= permission
    registry.add(
        MessageHandler(callback, filters=route_filter),
        group=group,
        route_id=route_id,
    )


def _callback(
    registry: HandlerRegistry,
    route_id: str,
    callback,
    pattern: str,
    permission=None,
    *,
    group: int = 0,
) -> None:
    route_filter = regex(pattern)
    if permission is not None:
        route_filter &= permission
    registry.add(
        CallbackQueryHandler(callback, filters=route_filter),
        group=group,
        route_id=route_id,
    )


def register_core_routes(registry: HandlerRegistry) -> None:
    """Register each business callback under one explicit v2 owner.

    Most business implementations are migrated incrementally. Handler ownership
    is already native v2 and never calls ``bot.core.handlers.add_handlers``.
    """

    _message(registry, "auth.authorize", authorize, BotCommands.AuthorizeCommand, CustomFilters.sudo)
    _message(registry, "auth.unauthorize", unauthorize, BotCommands.UnAuthorizeCommand, CustomFilters.sudo)
    _message(registry, "auth.add_sudo", add_sudo, BotCommands.AddSudoCommand, CustomFilters.owner)
    _message(registry, "auth.remove_sudo", remove_sudo, BotCommands.RmSudoCommand, CustomFilters.owner)

    _message(registry, "settings.bot", send_bot_settings, BotCommands.BotSetCommand, CustomFilters.sudo)
    _callback(registry, "settings.bot.callback", edit_bot_settings, "^botset", CustomFilters.sudo)
    _message(registry, "settings.users", get_users_settings, BotCommands.UsersCommand, CustomFilters.sudo)
    _message(registry, "settings.user", send_user_settings, BotCommands.UserSetCommand, CustomFilters.authorized)
    _callback(registry, "settings.user.callback", edit_user_settings, "^userset")

    _message(registry, "task.cancel", cancel, BotCommands.CancelTaskCommand, CustomFilters.authorized)
    _message(registry, "task.cancel_all", cancel_all_buttons, BotCommands.CancelAllCommand, CustomFilters.authorized)
    _callback(registry, "task.cancel_all.callback", cancel_all_update, "^canall")
    _callback(registry, "task.cancel_multi.callback", cancel_multi, "^stopm")
    _message(registry, "task.force_start", remove_from_queue, BotCommands.ForceStartCommand, CustomFilters.authorized)
    _message(registry, "task.status", task_status, BotCommands.StatusCommand, CustomFilters.authorized)
    _callback(registry, "task.status.callback", status_pages, "^status")

    _message(registry, "cloud.clone", clone_node, BotCommands.CloneCommand, CustomFilters.authorized)
    _message(registry, "cloud.count", count_node, BotCommands.CountCommand, CustomFilters.authorized)
    _message(registry, "cloud.delete", delete_file, BotCommands.DeleteCommand, CustomFilters.authorized)
    _message(registry, "cloud.list", gdrive_search, BotCommands.ListCommand, CustomFilters.authorized)
    _callback(registry, "cloud.list.callback", select_type, "^list_types")

    _message(registry, "owner.aexec", aioexecute, BotCommands.AExecCommand, CustomFilters.owner)
    _message(registry, "owner.exec", execute, BotCommands.ExecCommand, CustomFilters.owner)
    _message(registry, "owner.clear", clear, BotCommands.ClearLocalsCommand, CustomFilters.owner)
    _message(registry, "owner.shell", run_shell, BotCommands.ShellCommand, CustomFilters.owner)
    registry.add(
        EditedMessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True)
            & CustomFilters.owner,
        ),
        route_id="owner.shell.edited",
    )

    _message(registry, "selection.open", select, BotCommands.SelectCommand, CustomFilters.authorized)
    _callback(registry, "selection.callback", confirm_selection, "^sel")
    _callback(registry, "help.argument.callback", arg_usage, "^help")

    _message(registry, "transfer.mirror", mirror, BotCommands.MirrorCommand, CustomFilters.authorized)
    _message(registry, "transfer.qb_mirror", qb_mirror, BotCommands.QbMirrorCommand, CustomFilters.authorized)
    _message(registry, "transfer.jd_mirror", jd_mirror, BotCommands.JdMirrorCommand, CustomFilters.authorized)
    _message(registry, "transfer.nzb_mirror", nzb_mirror, BotCommands.NzbMirrorCommand, CustomFilters.authorized)
    _message(registry, "transfer.leech", leech, BotCommands.LeechCommand, CustomFilters.authorized)
    _message(registry, "transfer.qb_leech", qb_leech, BotCommands.QbLeechCommand, CustomFilters.authorized)
    _message(registry, "transfer.jd_leech", jd_leech, BotCommands.JdLeechCommand, CustomFilters.authorized)
    _message(registry, "transfer.nzb_leech", nzb_leech, BotCommands.NzbLeechCommand, CustomFilters.authorized)
    _message(registry, "transfer.ytdl", ytdl, BotCommands.YtdlCommand, CustomFilters.authorized)
    _message(registry, "transfer.ytdl_leech", ytdl_leech, BotCommands.YtdlLeechCommand, CustomFilters.authorized)
    _message(registry, "transfer.gallery", gallery_dl, BotCommands.GallerydlCommand, CustomFilters.authorized)
    _message(registry, "transfer.gallery_leech", gallery_dl_leech, BotCommands.GallerydlLeechCommand, CustomFilters.authorized)
    _message(registry, "transfer.media_direct", media_direct, BotCommands.MediaDirectCommand, CustomFilters.authorized)

    _message(registry, "rss.menu", get_rss_menu, BotCommands.RssCommand, CustomFilters.authorized)
    _callback(registry, "rss.callback", rss_listener, "^rss")
    _message(registry, "search.torrent", torrent_search, BotCommands.SearchCommand, CustomFilters.authorized)
    _callback(registry, "search.torrent.callback", torrent_search_update, "^torser")
    _message(registry, "search.nzb", hydra_search, BotCommands.NzbSearchCommand, CustomFilters.authorized)

    _message(registry, "core.start", start, BotCommands.StartCommand)
    _message(registry, "core.log", log, BotCommands.LogCommand, CustomFilters.sudo)
    _message(registry, "core.restart", restart_bot, BotCommands.RestartCommand, CustomFilters.sudo)
    _callback(registry, "core.restart.callback", confirm_restart, "^botrestart", CustomFilters.sudo)
    _message(registry, "core.ping", ping, BotCommands.PingCommand, CustomFilters.authorized)
    _message(registry, "core.speedtest", run_speedtest, BotCommands.SpeedtestCommand, CustomFilters.sudo)
    _message(registry, "core.stats", bot_stats, BotCommands.StatsCommand, CustomFilters.authorized)

    # /help is intentionally NOT registered here. The unified command center
    # is the sole v2 owner of /help. This removes the legacy dual-owner route.

    registry.add(
        MessageHandler(
            atri_free_tools_message,
            filters=(
                filters.incoming
                & filters.text
                & NOT_SLASH_COMMAND
                & CustomFilters.authorized
            ),
        ),
        group=18,
        route_id="atri.free_tools.text",
    )
    registry.add(
        MessageHandler(
            atri_tools_message,
            filters=(
                filters.incoming
                & filters.text
                & NOT_SLASH_COMMAND
                & CustomFilters.authorized
            ),
        ),
        group=19,
        route_id="atri.web_tools.text",
    )
    registry.add(
        MessageHandler(
            atri_message,
            filters=(
                filters.incoming
                & NOT_SLASH_COMMAND
                & (
                    filters.text
                    | filters.photo
                    | filters.sticker
                    | filters.animation
                    | filters.video
                    | filters.video_note
                    | filters.document
                    | filters.audio
                    | filters.voice
                )
                & ATRI_PUBLIC_INTERACTION
            ),
        ),
        group=20,
        route_id="atri.conversation",
    )

    LOGGER.info(
        "PRIXOK_V2_CORE_ROUTES_READY handlers=%s slash_commands_excluded_from_atri=1",
        len(registry.records),
    )
    bot_loop.create_task(sync_bot_command_menu(registry.client))
