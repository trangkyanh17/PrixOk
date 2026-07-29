from __future__ import annotations

from pyrogram.filters import command, regex
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from bot import LOGGER
from bot.core.telegram_manager import TgClient
from bot.helper.telegram_helper.filters import CustomFilters

from .game_casino import tai_xiu, xuc_xac
from .game_common import command_name
from .game_duck import duck_race
from .game_economy import (
    admin_set_coins,
    game_help,
    game_top,
    owner_set_coins,
    profile,
    toggle_games,
    wallet,
    werewolf_top,
)
from .game_werewolf import werewolf_callback, werewolf_command

# Đổi tên lệnh tại đây nếu muốn.
CMD_WALLET = "vi"
CMD_TOP = "bangxu"
CMD_WEREWOLF_TOP = "topmasoi"
CMD_PROFILE = "hoso"
CMD_HELP = "luatchoi"
CMD_DUCK = "duavit"
CMD_TAI_XIU = "tx"
CMD_TAI_XIU_LONG = "taixiu"
CMD_DICE = "xucxac"
CMD_DICE_SHORT = "xx"
CMD_WEREWOLF = "masoi"
CMD_SET_COINS = "setxu"
CMD_OWNER_SET_COINS = "setcoins"
CMD_TOGGLE = "game"


def _message(handler, name, custom_filter):
    return MessageHandler(
        handler,
        filters=command(command_name(name), case_sensitive=True) & custom_filter,
    )


def add_game_handlers() -> None:
    bot = TgClient.bot
    authorized = CustomFilters.authorized
    sudo = CustomFilters.sudo
    owner = CustomFilters.owner

    bot.add_handler(_message(wallet, CMD_WALLET, authorized))
    bot.add_handler(_message(game_top, CMD_TOP, authorized))
    bot.add_handler(_message(werewolf_top, CMD_WEREWOLF_TOP, authorized))
    bot.add_handler(_message(profile, CMD_PROFILE, authorized))
    bot.add_handler(_message(game_help, CMD_HELP, authorized))
    bot.add_handler(_message(duck_race, CMD_DUCK, authorized))
    bot.add_handler(_message(tai_xiu, CMD_TAI_XIU, authorized))
    bot.add_handler(_message(tai_xiu, CMD_TAI_XIU_LONG, authorized))
    bot.add_handler(_message(xuc_xac, CMD_DICE, authorized))
    bot.add_handler(_message(xuc_xac, CMD_DICE_SHORT, authorized))
    bot.add_handler(_message(werewolf_command, CMD_WEREWOLF, authorized))
    bot.add_handler(
        _message(owner_set_coins, CMD_OWNER_SET_COINS, owner)
    )
    bot.add_handler(_message(admin_set_coins, CMD_SET_COINS, sudo))
    bot.add_handler(_message(toggle_games, CMD_TOGGLE, sudo))
    bot.add_handler(CallbackQueryHandler(werewolf_callback, filters=regex("^ws ")))

    LOGGER.info(
        "Đã nạp khu giải trí: đua vịt, tài xỉu, "
        "xúc xắc, ma sói và hệ thống tiền tệ."
    )
