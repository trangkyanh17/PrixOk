from ..helper.ext_utils.bot_utils import COMMAND_USAGE, new_task
from ..helper.ext_utils.help_messages import (
    CLONE_HELP_DICT,
    GDL_HELP_DICT,
    MIRROR_HELP_DICT,
    YT_HELP_DICT,
)
from ..helper.telegram_helper.bot_commands import BotCommands
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import (
    delete_message,
    edit_message,
    send_message,
)


def _cmd(value) -> str:
    if isinstance(value, (list, tuple)):
        return " · ".join(f"<code>/{item}</code>" for item in value)
    return f"<code>/{value}</code>"


def _row(value, description: str) -> str:
    return f"{_cmd(value)} — {description}"


MIRROR_HELP_TABLE = "\n".join(
    [
        "☁️ <b>BẢNG LỆNH MIRROR / LEECH</b>",
        "",
        "<b>Tải lên cloud</b>",
        _row(BotCommands.MirrorCommand, "Mirror bằng trình tải mặc định."),
        _row(BotCommands.QbMirrorCommand, "Mirror torrent bằng qBittorrent."),
        _row(BotCommands.JdMirrorCommand, "Mirror bằng JDownloader."),
        _row(BotCommands.NzbMirrorCommand, "Mirror NZB bằng Sabnzbd."),
        _row(BotCommands.YtdlCommand, "Mirror video/âm thanh bằng yt-dlp."),
        _row(BotCommands.GallerydlCommand, "Mirror album bằng gallery-dl."),
        "",
        "<b>Gửi file lên Telegram</b>",
        _row(BotCommands.LeechCommand, "Leech bằng trình tải mặc định."),
        _row(BotCommands.QbLeechCommand, "Leech torrent bằng qBittorrent."),
        _row(BotCommands.JdLeechCommand, "Leech bằng JDownloader."),
        _row(BotCommands.NzbLeechCommand, "Leech NZB bằng Sabnzbd."),
        _row(BotCommands.YtdlLeechCommand, "Leech bằng yt-dlp."),
        _row(BotCommands.GallerydlLeechCommand, "Leech bằng gallery-dl."),
        "",
        "<b>Cloud, tìm kiếm và tác vụ</b>",
        _row(BotCommands.CloneCommand, "Sao chép file/thư mục cloud."),
        _row(BotCommands.CountCommand, "Đếm file, thư mục và dung lượng."),
        _row(BotCommands.DeleteCommand, "Xóa file/thư mục cloud."),
        _row(BotCommands.ListCommand, "Tìm file trong Google Drive."),
        _row(BotCommands.SearchCommand, "Tìm torrent."),
        _row(BotCommands.NzbSearchCommand, "Tìm nội dung NZB."),
        _row(BotCommands.StatusCommand, "Xem các tác vụ đang chạy."),
        _row(BotCommands.CancelTaskCommand, "Hủy tác vụ theo GID hoặc reply."),
        _row(BotCommands.CancelAllCommand, "Hủy nhiều/toàn bộ tác vụ."),
        _row(BotCommands.ForceStartCommand, "Ép tác vụ trong hàng đợi chạy."),
        _row(BotCommands.SelectCommand, "Chọn file torrent/NZB."),
        "",
        "<b>Thông tin và cài đặt</b>",
        _row(BotCommands.StartCommand, "Khởi động bot."),
        _row(BotCommands.HelpCommand, "Mở hai bảng lệnh này."),
        _row(BotCommands.PingCommand, "Kiểm tra độ trễ bot."),
        _row(BotCommands.StatsCommand, "Xem tài nguyên máy chủ."),
        _row(BotCommands.UserSetCommand, "Cài đặt cá nhân."),
        _row(BotCommands.RssCommand, "Quản lý RSS."),
        "",
        "<b>Quản trị mirror</b>",
        _row(BotCommands.AuthorizeCommand, "Cấp quyền user/chat [Sudo]."),
        _row(BotCommands.UnAuthorizeCommand, "Gỡ quyền user/chat [Sudo]."),
        _row(BotCommands.UsersCommand, "Quản lý user [Sudo]."),
        _row(BotCommands.BotSetCommand, "Cài đặt bot [Sudo]."),
        _row(BotCommands.AddSudoCommand, "Thêm Sudo [Owner]."),
        _row(BotCommands.RmSudoCommand, "Gỡ Sudo [Owner]."),
        _row(BotCommands.RestartCommand, "Khởi động lại bot [Sudo]."),
        _row(BotCommands.LogCommand, "Lấy log bot [Sudo]."),
        _row(BotCommands.ShellCommand, "Chạy shell [Owner]."),
        _row(BotCommands.ExecCommand, "Chạy Python đồng bộ [Owner]."),
        _row(BotCommands.AExecCommand, "Chạy Python async [Owner]."),
        _row(BotCommands.ClearLocalsCommand, "Xóa biến exec [Owner]."),
        "",
        "Gửi riêng từng lệnh không kèm tham số để xem hướng dẫn chi tiết.",
    ]
)


ENTERTAINMENT_HELP_TABLE = "\n".join(
    [
        "🎮 <b>BẢNG LỆNH GIẢI TRÍ</b>",
        "",
        "<b>Kiếm xu, XP và hồ sơ</b>",
        _row(BotCommands.FishCommand, "Câu cá; thêm river hoặc sea."),
        _row(BotCommands.MineCommand, "Đào mỏ; thêm metal hoặc nonmetal."),
        _row(BotCommands.ShipperCommand, "Làm nhiệm vụ giao hàng."),
        _row(BotCommands.RocketCommand, "Phóng tên lửa nhận xu và XP."),
        "4 hoạt động trên nhận x3 xu và x3 XP cơ bản; thưởng boss giữ nguyên.",
        _row(BotCommands.GameProfileCommand, "Xem hồ sơ game."),
        _row(BotCommands.InventoryCommand, "Xem kho; thêm fish hoặc minerals."),
        _row(BotCommands.GameTopCommand, "Xem bảng xếp hạng."),
        _row(BotCommands.GameStatsCommand, "Xem chỉ số V5/V6 đầy đủ."),
        "",
        "<b>Cược, may mắn và chuyển xu</b>",
        _row(BotCommands.TaiXiuCommand, "[tai|xiu] [xu|all]."),
        _row(BotCommands.NoHuCommand, "[xu|all]."),
        _row(BotCommands.DiceBetCommand, "[1-6] [xu|all]."),
        _row(
            BotCommands.LuckShopCommand,
            "Shop bùa 8 giờ: mayman, exp, tancong, phongthu hoặc nedon.",
        ),
        _row(BotCommands.RedeemCodeCommand, "[mã] để nhận quà."),
        _row(BotCommands.PayCommand, "[@user|ID] [xu|all]."),
        _row(BotCommands.DropCommand, "[xu|all] để thả rương trong nhóm."),
        _row(BotCommands.PickupCommand, "Reply rương để nhặt."),
        "",
        "<b>Trang bị</b>",
        _row(BotCommands.EquipmentShopCommand, "Xem cửa hàng set."),
        _row(BotCommands.BuyEquipmentCommand, "[set_id] để mua set."),
        _row(BotCommands.EquipCommand, "Xem hoặc chọn set đang dùng."),
        _row(BotCommands.MergeEquipmentCommand, "[set_id] để hợp nhất."),
        _row(BotCommands.RepairEquipmentCommand, "[giap|vukhi]."),
        "",
        "<b>Boss</b>",
        _row(BotCommands.SummonBossCommand, "list, random hoặc boss_id."),
        _row(BotCommands.BossStatusCommand, "Xem boss đang hoạt động."),
        _row(BotCommands.AttackBossCommand, "Tấn công boss; delay 0 giây."),
        _row(BotCommands.ExecuteBossCommand, "Kết liễu boss siêu cấp trả phí."),
        "",
        "<b>Quản trị game [Owner]</b>",
        _row(BotCommands.CreateCodeCommand, "[xu] để tạo code."),
        _row(BotCommands.DeleteCodeCommand, "[mã] để xóa code."),
        _row(BotCommands.SetCoinsCommand, "[user|all] [xu]."),
        _row(BotCommands.GiftCoinsCommand, "[user] [xu]."),
        _row(BotCommands.LuckyCommand, "[user] [0-100]."),
        _row(BotCommands.UnluckyCommand, "[user]."),
        _row(BotCommands.AllowGroupCommand, "Duyệt nhóm dùng bot."),
        _row(BotCommands.DeleteGroupCommand, "Gỡ quyền nhóm."),
        "",
        "<b>Chỉ số nhân vật</b>",
        "Cấp tối đa 500 · Cấp 1: 2.000 HP, 100 tấn công, "
        "200 phòng thủ, 1% né.",
        "Mỗi cấp: +500 HP · +40 tấn công · +50 phòng thủ.",
        "Hồi HP: 20 HP/s ở cấp 1 · mỗi cấp tăng thêm 30 HP/s.",
        "Bị boss hạ: chờ 1 phút để đánh tiếp.",
    ]
)


@new_task
async def arg_usage(_, query):
    data = query.data.split()
    message = query.message
    if data[1] == "close":
        await delete_message(message)
    elif data[1] == "back":
        if data[2] == "m":
            await edit_message(
                message,
                COMMAND_USAGE["mirror"][0],
                COMMAND_USAGE["mirror"][1],
            )
        elif data[2] == "y":
            await edit_message(
                message,
                COMMAND_USAGE["yt"][0],
                COMMAND_USAGE["yt"][1],
            )
        elif data[2] == "g":
            await edit_message(
                message,
                COMMAND_USAGE["gdl"][0],
                COMMAND_USAGE["gdl"][1],
            )
        elif data[2] == "c":
            await edit_message(
                message,
                COMMAND_USAGE["clone"][0],
                COMMAND_USAGE["clone"][1],
            )
    elif data[1] == "mirror":
        buttons = ButtonMaker()
        buttons.data_button("Back", "help back m")
        button = buttons.build_menu()
        await edit_message(message, MIRROR_HELP_DICT[data[2]], button)
    elif data[1] == "yt":
        buttons = ButtonMaker()
        buttons.data_button("Back", "help back y")
        button = buttons.build_menu()
        await edit_message(message, YT_HELP_DICT[data[2]], button)
    elif data[1] == "gdl":
        buttons = ButtonMaker()
        buttons.data_button("Back", "help back g")
        button = buttons.build_menu()
        await edit_message(message, GDL_HELP_DICT[data[2]], button)
    elif data[1] == "clone":
        buttons = ButtonMaker()
        buttons.data_button("Back", "help back c")
        button = buttons.build_menu()
        await edit_message(message, CLONE_HELP_DICT[data[2]], button)


@new_task
async def bot_help(_, message):
    await send_message(message, MIRROR_HELP_TABLE)
    await send_message(message, ENTERTAINMENT_HELP_TABLE)
