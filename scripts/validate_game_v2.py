from __future__ import annotations

from ast import AnnAssign, Assign, literal_eval, parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SET_ORDER = [
    "nhom", "dong", "bac", "sat", "vang", "kim_cuong", "graphine"
]


def assignment_value(path: Path, name: str):
    tree = parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, Assign):
            target = node.targets[0]
            if getattr(target, "id", None) == name:
                return literal_eval(node.value)
        if isinstance(node, AnnAssign) and getattr(node.target, "id", None) == name:
            return literal_eval(node.value)
    raise ValueError(f"Không tìm thấy {name} trong {path}")


def main() -> None:
    common_path = ROOT / "bot/modules/game_common.py"
    boss_path = ROOT / "bot/modules/game_boss.py"
    entertainment = (ROOT / "bot/modules/entertainment.py").read_text(encoding="utf-8")
    commands = (ROOT / "bot/helper/telegram_helper/bot_commands.py").read_text(encoding="utf-8")
    handlers = (ROOT / "bot/core/handlers.py").read_text(encoding="utf-8")

    sets = assignment_value(common_path, "EQUIPMENT_SETS")
    bosses = assignment_value(boss_path, "BOSS_TEMPLATES")
    fish_cooldown = assignment_value(
        ROOT / "bot/modules/entertainment.py",
        "FISH_COOLDOWN",
    )
    mine_cooldown = assignment_value(
        ROOT / "bot/modules/entertainment.py",
        "MINE_COOLDOWN",
    )
    shipper_cooldown = assignment_value(
        ROOT / "bot/modules/game_economy.py",
        "SHIPPER_COOLDOWN",
    )
    rocket_cooldown = assignment_value(
        ROOT / "bot/modules/game_economy.py",
        "ROCKET_COOLDOWN",
    )
    random_summon_cost = assignment_value(
        boss_path,
        "BOSS_RANDOM_SUMMON_COST",
    )
    targeted_summon_cost = assignment_value(
        boss_path,
        "BOSS_TARGETED_SUMMON_COST",
    )

    cooldowns = {
        "fish": fish_cooldown,
        "mine": mine_cooldown,
        "shipper": shipper_cooldown,
        "rocket": rocket_cooldown,
    }
    if any(value != 60 for value in cooldowns.values()):
        raise ValueError(f"Cooldown phải đồng loạt 60 giây: {cooldowns}")
    if random_summon_cost != 20_000:
        raise ValueError(
            f"Phí gọi boss ngẫu nhiên phải là 20.000, hiện là {random_summon_cost}"
        )
    if targeted_summon_cost != 70_000:
        raise ValueError(
            f"Phí gọi boss chỉ định phải là 70.000, hiện là {targeted_summon_cost}"
        )
    if list(sets) != EXPECTED_SET_ORDER:
        raise ValueError(f"Sai thứ tự set: {list(sets)}")
    if len(bosses) != 14:
        raise ValueError(f"Cần 14 boss, hiện có {len(bosses)}")
    boss_ids = [boss["id"] for boss in bosses]
    if len(set(boss_ids)) != len(boss_ids):
        raise ValueError("Trùng ID boss")
    boss_hps = [int(boss["hp"]) for boss in bosses]
    if boss_hps != sorted(boss_hps):
        raise ValueError("Boss phải được sắp xếp theo HP tăng dần")
    for boss in bosses:
        for field in ("id", "name", "emoji", "hp", "reward", "weight", "wear_min", "wear_max"):
            if field not in boss:
                raise ValueError(f"Boss thiếu trường {field}: {boss}")
        if boss["hp"] <= 0 or boss["reward"] <= 0 or boss["weight"] <= 0:
            raise ValueError(f"Boss có chỉ số không hợp lệ: {boss['id']}")
        if boss["wear_min"] <= 0 or boss["wear_max"] < boss["wear_min"]:
            raise ValueError(f"Độ hao mòn không hợp lệ: {boss['id']}")
    if sum(int(boss["weight"]) for boss in bosses) != 1000:
        raise ValueError("Tổng trọng số boss phải bằng 1000")
    for set_id, item in sets.items():
        for field in ("tier", "price", "attack", "crit", "protection", "durability"):
            if field not in item:
                raise ValueError(f"{set_id}: thiếu {field}")
        if item["price"] <= 0 or item["durability"] <= 0:
            raise ValueError(f"{set_id}: giá hoặc độ bền không hợp lệ")
    if 'path = f"inventory.fish.' in entertainment:
        raise ValueError("Câu cá vẫn còn lưu cá vào inventory")
    if "Đã bán tự động" not in entertainment:
        raise ValueError("Thiếu thông báo bán cá tự động")

    common_text = common_path.read_text(encoding="utf-8")
    boss_text = boss_path.read_text(encoding="utf-8")
    required_repair_markers = [
        "armor_durability",
        "weapon_durability",
        "armor_max_penalty",
        "weapon_max_penalty",
        "protection_penalty",
        "attack_penalty",
        "async def repair_equipment",
        "REPAIR_MAX_DURABILITY_LOSS_RATE",
    ]
    missing_repair = [
        marker
        for marker in required_repair_markers
        if marker not in common_text and marker not in boss_text
    ]
    if missing_repair:
        raise ValueError("Thiếu cơ chế sửa chữa: " + ", ".join(missing_repair))

    required_commands = [
        "TaiXiuCommand", "NoHuCommand", "DiceBetCommand", "ShipperCommand",
        "RocketCommand", "LuckShopCommand", "RedeemCodeCommand", "DropCommand",
        "PickupCommand", "PayCommand", "GameStatsCommand", "CreateCodeCommand",
        "DeleteCodeCommand", "SetCoinsCommand", "AllowGroupCommand",
        "DeleteGroupCommand", "GiftCoinsCommand", "LuckyCommand", "UnluckyCommand",
        "EquipmentShopCommand", "BuyEquipmentCommand", "EquipCommand",
        "MergeEquipmentCommand", "RepairEquipmentCommand", "SummonBossCommand", "BossStatusCommand",
        "AttackBossCommand",
    ]
    missing_commands = [name for name in required_commands if name not in commands]
    missing_handlers = [name for name in required_commands if f"BotCommands.{name}" not in handlers]
    if missing_commands:
        raise ValueError("Thiếu command: " + ", ".join(missing_commands))
    if missing_handlers:
        raise ValueError("Thiếu handler: " + ", ".join(missing_handlers))

    required_summon_markers = [
        "_find_boss",
        "_boss_catalog_text",
        "BOSS_RANDOM_SUMMON_COST",
        "BOSS_TARGETED_SUMMON_COST",
        '"summon_mode"',
        '"summon_cost"',
    ]
    missing_summon = [
        marker for marker in required_summon_markers if marker not in boss_text
    ]
    if missing_summon:
        raise ValueError(
            "Thiếu cơ chế gọi boss ngẫu nhiên/chỉ định: "
            + ", ".join(missing_summon)
        )

    print(
        "OK: fish/mine/shipper/rocket hồi 60 giây; "
        "gọi boss ngẫu nhiên 20.000 xu; gọi chỉ định 70.000 xu; "
        "7 set sửa chữa và 14 boss hợp lệ."
    )


if __name__ == "__main__":
    main()
