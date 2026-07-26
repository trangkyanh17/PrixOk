from __future__ import annotations

from pathlib import Path


ROOT = Path.cwd()


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def update_bot_commands() -> None:
    path = "bot/helper/telegram_helper/bot_commands.py"
    text = read(path)
    text = text.replace(
        '    GameTopCommand = f"top{i}"\n',
        '    GameTopCommand = [f"top{i}", f"bxh{i}"]\n',
    )

    marker = '    GameTopCommand = [f"top{i}", f"bxh{i}"]\n'
    commands = [
        ('TaiXiuCommand', '    TaiXiuCommand = f"tx{i}"\n'),
        ('NoHuCommand', '    NoHuCommand = f"nohu{i}"\n'),
        ('DiceBetCommand', '    DiceBetCommand = f"xucxac{i}"\n'),
        ('ShipperCommand', '    ShipperCommand = f"shipper{i}"\n'),
        ('RocketCommand', '    RocketCommand = f"rocket{i}"\n'),
        ('LuckShopCommand', '    LuckShopCommand = f"shopmayman{i}"\n'),
        ('RedeemCodeCommand', '    RedeemCodeCommand = f"code{i}"\n'),
        ('DropCommand', '    DropCommand = f"drop{i}"\n'),
        ('PickupCommand', '    PickupCommand = f"pickup{i}"\n'),
        ('PayCommand', '    PayCommand = f"pay{i}"\n'),
        ('GameStatsCommand', '    GameStatsCommand = f"thongke{i}"\n'),
        ('CreateCodeCommand', '    CreateCodeCommand = f"crecode{i}"\n'),
        ('DeleteCodeCommand', '    DeleteCodeCommand = f"delcode{i}"\n'),
        ('SetCoinsCommand', '    SetCoinsCommand = f"setcoins{i}"\n'),
        ('AllowGroupCommand', '    AllowGroupCommand = f"allow{i}"\n'),
        ('DeleteGroupCommand', '    DeleteGroupCommand = f"delete{i}"\n'),
        ('GiftCoinsCommand', '    GiftCoinsCommand = f"giftcoins{i}"\n'),
        ('LuckyCommand', '    LuckyCommand = f"lucky{i}"\n'),
        ('UnluckyCommand', '    UnluckyCommand = f"unlucky{i}"\n'),
        ('EquipmentShopCommand', '    EquipmentShopCommand = f"shoptrangbi{i}"\n'),
        ('BuyEquipmentCommand', '    BuyEquipmentCommand = f"muatrangbi{i}"\n'),
        ('EquipCommand', '    EquipCommand = f"trangbi{i}"\n'),
        ('MergeEquipmentCommand', '    MergeEquipmentCommand = f"hopnhat{i}"\n'),
        ('RepairEquipmentCommand', '    RepairEquipmentCommand = f"suachua{i}"\n'),
        ('SummonBossCommand', '    SummonBossCommand = f"goiboss{i}"\n'),
        ('BossStatusCommand', '    BossStatusCommand = f"boss{i}"\n'),
        ('AttackBossCommand', '    AttackBossCommand = f"danhboss{i}"\n'),
    ]
    if marker not in text:
        raise SystemExit(f"Không tìm thấy marker trong {path}")
    addition = ''.join(line for name, line in commands if name not in text)
    if addition:
        text = text.replace(marker, marker + addition, 1)
    write(path, text)


def update_modules_init() -> None:
    path = "bot/modules/__init__.py"
    text = read(path)
    entertainment_marker = '''from .entertainment import (
    fish,
    mine,
    game_profile,
    game_inventory,
    game_top,
)
'''
    economy_import = '''from .game_economy import (
    tai_xiu,
    no_hu,
    dice_bet,
    shipper_job,
    rocket_launch,
    buy_luck_buff,
    redeem_code,
    drop_coins,
    pickup_drop,
    pay_coins,
    account_stats,
    create_code,
    delete_code,
    set_coins,
    gift_coins,
    set_luck,
    reset_luck,
    allow_group,
    delete_group,
)
'''
    boss_import = '''from .game_boss import (
    equipment_shop,
    buy_equipment,
    equip_item,
    merge_equipment,
    repair_equipment,
    summon_boss,
    boss_status,
    attack_boss,
)
'''
    if "from .game_economy import (" not in text:
        if entertainment_marker not in text:
            raise SystemExit(f"Không tìm thấy import marker trong {path}")
        text = text.replace(entertainment_marker, entertainment_marker + economy_import + boss_import, 1)
    else:
        if "merge_equipment," not in text:
            text = text.replace("    equip_item,\n", "    equip_item,\n    merge_equipment,\n", 1)
        if "repair_equipment," not in text:
            text = text.replace("    merge_equipment,\n", "    merge_equipment,\n    repair_equipment,\n", 1)

    exports = [
        "tai_xiu", "no_hu", "dice_bet", "shipper_job", "rocket_launch",
        "buy_luck_buff", "redeem_code", "drop_coins", "pickup_drop",
        "pay_coins", "account_stats", "create_code", "delete_code",
        "set_coins", "gift_coins", "set_luck", "reset_luck", "allow_group",
        "delete_group", "equipment_shop", "buy_equipment", "equip_item",
        "merge_equipment", "repair_equipment", "summon_boss", "boss_status", "attack_boss",
    ]
    missing = [name for name in exports if f'"{name}",' not in text]
    if missing:
        marker = '    "game_top",\n'
        if marker not in text:
            raise SystemExit(f"Không tìm thấy __all__ marker trong {path}")
        text = text.replace(marker, marker + ''.join(f'    "{name}",\n' for name in missing), 1)
    write(path, text)


def handler(function_name: str, command_name: str, filter_name: str) -> str:
    return f'''    TgClient.bot.add_handler(
        MessageHandler(
            {function_name},
            filters=command(
                BotCommands.{command_name},
                case_sensitive=True,
            )
            & CustomFilters.{filter_name},
        )
    )
'''


def update_handlers() -> None:
    path = "bot/core/handlers.py"
    text = read(path)
    regular = [
        ("tai_xiu", "TaiXiuCommand"), ("no_hu", "NoHuCommand"),
        ("dice_bet", "DiceBetCommand"), ("shipper_job", "ShipperCommand"),
        ("rocket_launch", "RocketCommand"), ("buy_luck_buff", "LuckShopCommand"),
        ("redeem_code", "RedeemCodeCommand"), ("drop_coins", "DropCommand"),
        ("pickup_drop", "PickupCommand"), ("pay_coins", "PayCommand"),
        ("account_stats", "GameStatsCommand"), ("equipment_shop", "EquipmentShopCommand"),
        ("buy_equipment", "BuyEquipmentCommand"), ("equip_item", "EquipCommand"),
        ("merge_equipment", "MergeEquipmentCommand"), ("repair_equipment", "RepairEquipmentCommand"),
        ("summon_boss", "SummonBossCommand"),
        ("boss_status", "BossStatusCommand"), ("attack_boss", "AttackBossCommand"),
    ]
    admins = [
        ("create_code", "CreateCodeCommand"), ("delete_code", "DeleteCodeCommand"),
        ("set_coins", "SetCoinsCommand"), ("allow_group", "AllowGroupCommand"),
        ("delete_group", "DeleteGroupCommand"), ("gift_coins", "GiftCoinsCommand"),
        ("set_luck", "LuckyCommand"), ("reset_luck", "UnluckyCommand"),
    ]
    missing_regular = [(fn,cmd) for fn,cmd in regular if f"BotCommands.{cmd}" not in text]
    missing_admins = [(fn,cmd) for fn,cmd in admins if f"BotCommands.{cmd}" not in text]
    if not missing_regular and not missing_admins:
        return
    marker = '''    TgClient.bot.add_handler(
        MessageHandler(
            torrent_search,
'''
    if marker not in text:
        raise SystemExit(f"Không tìm thấy handler marker trong {path}")
    block = ''.join(handler(fn,cmd,'authorized') for fn,cmd in missing_regular)
    block += ''.join(handler(fn,cmd,'owner') for fn,cmd in missing_admins)
    text = text.replace(marker, block + marker, 1)
    write(path, text)


def main() -> None:
    required = [
        "bot/modules/entertainment.py",
        "bot/modules/game_common.py",
        "bot/modules/game_economy.py",
        "bot/modules/game_boss.py",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit("Thiếu file: " + ", ".join(missing))
    update_bot_commands()
    update_modules_init()
    update_handlers()
    print("OK: Đã tích hợp cooldown 60 giây, gọi boss ngẫu nhiên/chỉ định, 7 set sửa chữa và 14 boss.")


if __name__ == "__main__":
    main()
