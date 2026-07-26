from pyrogram.filters import command, regex
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, EditedMessageHandler

from ..modules import *
from ..helper.telegram_helper.bot_commands import BotCommands
from ..helper.telegram_helper.filters import CustomFilters
from .telegram_manager import TgClient


def add_handlers():
    TgClient.bot.add_handler(
        MessageHandler(
            authorize,
            filters=command(BotCommands.AuthorizeCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            unauthorize,
            filters=command(BotCommands.UnAuthorizeCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            add_sudo,
            filters=command(BotCommands.AddSudoCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            remove_sudo,
            filters=command(BotCommands.RmSudoCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            send_bot_settings,
            filters=command(BotCommands.BotSetCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            edit_bot_settings, filters=regex("^botset") & CustomFilters.sudo
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            cancel,
            filters=command(BotCommands.CancelTaskCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            cancel_all_buttons,
            filters=command(BotCommands.CancelAllCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(cancel_all_update, filters=regex("^canall"))
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(cancel_multi, filters=regex("^stopm"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            clone_node,
            filters=command(BotCommands.CloneCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            aioexecute,
            filters=command(BotCommands.AExecCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            execute,
            filters=command(BotCommands.ExecCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            clear,
            filters=command(BotCommands.ClearLocalsCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            select,
            filters=command(BotCommands.SelectCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(confirm_selection, filters=regex("^sel"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            remove_from_queue,
            filters=command(BotCommands.ForceStartCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            count_node,
            filters=command(BotCommands.CountCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            delete_file,
            filters=command(BotCommands.DeleteCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            gdrive_search,
            filters=command(BotCommands.ListCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(select_type, filters=regex("^list_types"))
    )
    TgClient.bot.add_handler(CallbackQueryHandler(arg_usage, filters=regex("^help")))
    TgClient.bot.add_handler(
        MessageHandler(
            mirror,
            filters=command(BotCommands.MirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            qb_mirror,
            filters=command(BotCommands.QbMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            jd_mirror,
            filters=command(BotCommands.JdMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            nzb_mirror,
            filters=command(BotCommands.NzbMirrorCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            leech,
            filters=command(BotCommands.LeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            qb_leech,
            filters=command(BotCommands.QbLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            jd_leech,
            filters=command(BotCommands.JdLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            nzb_leech,
            filters=command(BotCommands.NzbLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            get_rss_menu,
            filters=command(BotCommands.RssCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(CallbackQueryHandler(rss_listener, filters=regex("^rss")))
    TgClient.bot.add_handler(
        MessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        EditedMessageHandler(
            run_shell,
            filters=command(BotCommands.ShellCommand, case_sensitive=True)
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            start, filters=command(BotCommands.StartCommand, case_sensitive=True)
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            log,
            filters=command(BotCommands.LogCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            restart_bot,
            filters=command(BotCommands.RestartCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            confirm_restart, filters=regex("^botrestart") & CustomFilters.sudo
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ping,
            filters=command(BotCommands.PingCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            bot_help,
            filters=command(BotCommands.HelpCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            bot_stats,
            filters=command(BotCommands.StatsCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            task_status,
            filters=command(BotCommands.StatusCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(status_pages, filters=regex("^status"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            fish,
            filters=command(BotCommands.FishCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            mine,
            filters=command(BotCommands.MineCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            game_profile,
            filters=command(
                BotCommands.GameProfileCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            game_inventory,
            filters=command(
                BotCommands.InventoryCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            game_top,
            filters=command(
                BotCommands.GameTopCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            tai_xiu,
            filters=command(
                BotCommands.TaiXiuCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            no_hu,
            filters=command(
                BotCommands.NoHuCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            dice_bet,
            filters=command(
                BotCommands.DiceBetCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            shipper_job,
            filters=command(
                BotCommands.ShipperCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            rocket_launch,
            filters=command(
                BotCommands.RocketCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            buy_luck_buff,
            filters=command(
                BotCommands.LuckShopCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            redeem_code,
            filters=command(
                BotCommands.RedeemCodeCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            drop_coins,
            filters=command(
                BotCommands.DropCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            pickup_drop,
            filters=command(
                BotCommands.PickupCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            pay_coins,
            filters=command(
                BotCommands.PayCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            account_stats,
            filters=command(
                BotCommands.GameStatsCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            equipment_shop,
            filters=command(
                BotCommands.EquipmentShopCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            buy_equipment,
            filters=command(
                BotCommands.BuyEquipmentCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            equip_item,
            filters=command(
                BotCommands.EquipCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            merge_equipment,
            filters=command(
                BotCommands.MergeEquipmentCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            repair_equipment,
            filters=command(
                BotCommands.RepairEquipmentCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            summon_boss,
            filters=command(
                BotCommands.SummonBossCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            boss_status,
            filters=command(
                BotCommands.BossStatusCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            attack_boss,
            filters=command(
                BotCommands.AttackBossCommand,
                case_sensitive=True,
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            create_code,
            filters=command(
                BotCommands.CreateCodeCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            delete_code,
            filters=command(
                BotCommands.DeleteCodeCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            set_coins,
            filters=command(
                BotCommands.SetCoinsCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            allow_group,
            filters=command(
                BotCommands.AllowGroupCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            delete_group,
            filters=command(
                BotCommands.DeleteGroupCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            gift_coins,
            filters=command(
                BotCommands.GiftCoinsCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            set_luck,
            filters=command(
                BotCommands.LuckyCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            reset_luck,
            filters=command(
                BotCommands.UnluckyCommand,
                case_sensitive=True,
            )
            & CustomFilters.owner,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            torrent_search,
            filters=command(BotCommands.SearchCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(torrent_search_update, filters=regex("^torser"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            get_users_settings,
            filters=command(BotCommands.UsersCommand, case_sensitive=True)
            & CustomFilters.sudo,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            send_user_settings,
            filters=command(BotCommands.UserSetCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        CallbackQueryHandler(edit_user_settings, filters=regex("^userset"))
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ytdl,
            filters=command(BotCommands.YtdlCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            ytdl_leech,
            filters=command(BotCommands.YtdlLeechCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            gallery_dl,
            filters=command(BotCommands.GallerydlCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            gallery_dl_leech,
            filters=command(
                BotCommands.GallerydlLeechCommand, case_sensitive=True
            )
            & CustomFilters.authorized,
        )
    )
    TgClient.bot.add_handler(
        MessageHandler(
            hydra_search,
            filters=command(BotCommands.NzbSearchCommand, case_sensitive=True)
            & CustomFilters.authorized,
        )
    )
