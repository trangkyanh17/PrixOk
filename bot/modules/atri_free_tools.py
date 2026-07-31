from __future__ import annotations

import asyncio
import calendar
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pyrogram.types import BotCommand

from bot import LOGGER
from bot.core.config_manager import Config

DATA = Path('/app/atri_data')
DB = DATA / 'atri_free_tools.sqlite3'
TMP = Path('/app/downloads/atri-free-tools')
TZ = ZoneInfo(os.getenv('ATRI_TIMEZONE', 'Asia/Ho_Chi_Minh'))
MAX_MB = max(10, int(os.getenv('ATRI_MAX_MEDIA_MB', '45')))
MAX_BYTES = MAX_MB * 1024 * 1024
MUSIC_MAX = max(60, int(os.getenv('ATRI_MAX_MUSIC_SECONDS', '1200')))
DOUYIN_MAX = max(60, int(os.getenv('ATRI_MAX_DOUYIN_SECONDS', '600')))
COOKIE_YT = DATA / 'youtube_cookies.txt'
COOKIE_DY = DATA / 'douyin_cookies.txt'
DB_LOCK = asyncio.Lock()
MEDIA_LOCK = asyncio.Semaphore(max(1, int(os.getenv('ATRI_MEDIA_CONCURRENCY', '2'))))
STARTED = False
WEEKDAYS = ('Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ Nhật')


def suffix() -> str:
    return str(getattr(Config, 'CMD_SUFFIX', '') or '')


def parts(text: str) -> tuple[str, str]:
    command, _, argument = text.strip().partition(' ')
    return command.split('@', 1)[0].casefold(), argument.strip()


def match(command: str, name: str) -> bool:
    return command == f'/{name}{suffix()}'.casefold()


def reply_arg(message, argument: str) -> str:
    if argument:
        return argument
    reply = getattr(message, 'reply_to_message', None)
    return str(getattr(reply, 'text', '') or getattr(reply, 'caption', '') or '').strip()


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    return con


def db_init() -> None:
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS reminders(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL,
          thread_id INTEGER NOT NULL DEFAULT 0,
          user_id INTEGER NOT NULL,
          user_name TEXT NOT NULL,
          body TEXT NOT NULL,
          due_utc TEXT NOT NULL,
          sent_utc TEXT
        );
        CREATE INDEX IF NOT EXISTS reminders_due ON reminders(sent_utc, due_utc);
        ''')


def db_add(chat_id: int, thread_id: int, user_id: int, user_name: str, body: str, due: datetime) -> int:
    with connect() as con:
        pending = con.execute(
            'SELECT COUNT(*) FROM reminders WHERE chat_id=? AND user_id=? AND sent_utc IS NULL',
            (chat_id, user_id),
        ).fetchone()[0]
        if pending >= 50:
            raise ValueError('Mỗi người chỉ được giữ tối đa 50 lời nhắc đang chờ.')
        cur = con.execute(
            'INSERT INTO reminders(chat_id,thread_id,user_id,user_name,body,due_utc) VALUES(?,?,?,?,?,?)',
            (chat_id, thread_id, user_id, user_name, body, due.astimezone(timezone.utc).isoformat()),
        )
        return int(cur.lastrowid)


def db_list(chat_id: int, user_id: int) -> list[sqlite3.Row]:
    with connect() as con:
        return list(con.execute(
            'SELECT id,body,due_utc FROM reminders WHERE chat_id=? AND user_id=? AND sent_utc IS NULL ORDER BY due_utc LIMIT 50',
            (chat_id, user_id),
        ))


def db_delete(chat_id: int, user_id: int, reminder_id: int) -> int:
    with connect() as con:
        return con.execute(
            'DELETE FROM reminders WHERE id=? AND chat_id=? AND user_id=? AND sent_utc IS NULL',
            (reminder_id, chat_id, user_id),
        ).rowcount


def db_due() -> list[sqlite3.Row]:
    with connect() as con:
        return list(con.execute(
            'SELECT * FROM reminders WHERE sent_utc IS NULL AND due_utc<=? ORDER BY due_utc LIMIT 30',
            (datetime.now(timezone.utc).isoformat(),),
        ))


def db_sent(reminder_id: int) -> None:
    with connect() as con:
        con.execute(
            'UPDATE reminders SET sent_utc=? WHERE id=? AND sent_utc IS NULL',
            (datetime.now(timezone.utc).isoformat(), reminder_id),
        )


async def db_call(function, *args):
    async with DB_LOCK:
        return await asyncio.to_thread(function, *args)


def parse_clock(hour: int, minute: int, tomorrow: bool = False) -> datetime:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError('Giờ không hợp lệ.')
    now = datetime.now(TZ)
    day = now.date() + timedelta(days=1 if tomorrow else 0)
    target = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)
    if not tomorrow and target <= now:
        target += timedelta(days=1)
    return target


def parse_remind(text: str) -> tuple[datetime, str]:
    text = re.sub(r'\s+', ' ', text.strip())
    now = datetime.now(TZ)
    relative = re.match(r'^(\d+)\s*(s|giây|m|phút|h|giờ|d|ngày)\s+(.+)$', text, re.I)
    if relative:
        amount, unit, body = int(relative[1]), relative[2].casefold(), relative[3].strip()
        if amount <= 0:
            raise ValueError('Thời gian phải lớn hơn 0.')
        delta = {
            's': timedelta(seconds=amount), 'giây': timedelta(seconds=amount),
            'm': timedelta(minutes=amount), 'phút': timedelta(minutes=amount),
            'h': timedelta(hours=amount), 'giờ': timedelta(hours=amount),
            'd': timedelta(days=amount), 'ngày': timedelta(days=amount),
        }[unit]
        return now + delta, body
    tomorrow = re.match(r'^(?:mai|ngày mai)\s+(\d{1,2}):(\d{2})\s+(.+)$', text, re.I)
    if tomorrow:
        return parse_clock(int(tomorrow[1]), int(tomorrow[2]), True), tomorrow[3].strip()
    for regex, order in (
        (r'^(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})\s+(.+)$', 'ymd'),
        (r'^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\s+(.+)$', 'dmy'),
    ):
        found = re.match(regex, text)
        if found:
            if order == 'ymd':
                year, month, day = int(found[1]), int(found[2]), int(found[3])
            else:
                day, month, year = int(found[1]), int(found[2]), int(found[3])
            target = datetime(year, month, day, int(found[4]), int(found[5]), tzinfo=TZ)
            if target <= now:
                raise ValueError('Thời điểm nhắc đã ở trong quá khứ.')
            return target, found[6].strip()
    clock = re.match(r'^(\d{1,2}):(\d{2})\s+(.+)$', text)
    if clock:
        return parse_clock(int(clock[1]), int(clock[2])), clock[3].strip()
    raise ValueError(
        f'Sai cú pháp. Ví dụ:\n/remind{suffix()} 30m Uống nước\n'
        f'/remind{suffix()} 20:30 Xem phim\n/remind{suffix()} mai 08:00 Đi làm\n'
        f'/remind{suffix()} 2026-08-05 09:00 Đi khám'
    )


def calendar_text(month: int, year: int) -> str:
    lines = ['T2 T3 T4 T5 T6 T7 CN']
    for week in calendar.Calendar(0).monthdayscalendar(year, month):
        lines.append(' '.join(f'{day:>2}' if day else '  ' for day in week))
    return '\n'.join(lines)


def calendar_args(argument: str) -> tuple[int, int]:
    now = datetime.now(TZ)
    values = argument.split()
    if not values:
        return now.month, now.year
    if len(values) == 1:
        month, year = int(values[0]), now.year
    elif len(values) == 2:
        month, year = map(int, values)
    else:
        raise ValueError(f'Cách dùng: /calendar{suffix()} [tháng] [năm]')
    if not 1 <= month <= 12 or not 1970 <= year <= 2100:
        raise ValueError('Tháng hoặc năm không hợp lệ.')
    return month, year


HOLIDAYS = ((1, 1, 'Tết Dương lịch'), (4, 30, 'Ngày Giải phóng miền Nam'), (5, 1, 'Ngày Quốc tế Lao động'), (9, 2, 'Quốc khánh Việt Nam'))


def holidays() -> list[tuple[date, str]]:
    today = datetime.now(TZ).date()
    result = [(date(year, month, day), name) for year in range(today.year, today.year + 3) for month, day, name in HOLIDAYS if date(year, month, day) >= today]
    return sorted(result)[:8]


def first_url(text: str) -> str:
    found = re.search(r'https?://[^\s<>]+', text)
    return found[0].rstrip('.,;!?)\"\'') if found else ''


def is_douyin(url: str) -> bool:
    host = (urlparse(url).hostname or '').casefold()
    return host == 'douyin.com' or host.endswith('.douyin.com') or host == 'iesdouyin.com' or host.endswith('.iesdouyin.com')


def first_info(info: dict[str, Any]) -> dict[str, Any]:
    entries = info.get('entries')
    if entries:
        return next((item for item in entries if isinstance(item, dict)), {})
    return info


def output_file(folder: Path, suffixes: tuple[str, ...]) -> Path:
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() not in {'.part', '.ytdl', '.json'}]
    for ext in suffixes:
        found = next((p for p in files if p.suffix.casefold() == ext), None)
        if found:
            return found
    if not files:
        raise RuntimeError('yt-dlp không tạo được file đầu ra.')
    return max(files, key=lambda p: p.stat().st_size)


def ydl_base(folder: Path) -> dict[str, Any]:
    return {
        'outtmpl': str(folder / '%(id)s.%(ext)s'), 'noplaylist': True,
        'quiet': True, 'no_warnings': True, 'restrictfilenames': True,
        'socket_timeout': 30, 'retries': 3, 'fragment_retries': 3,
        'concurrent_fragment_downloads': 4, 'cachedir': False,
    }


def download_music(query: str, folder: Path) -> tuple[Path, dict[str, Any]]:
    from yt_dlp import YoutubeDL
    source = query if re.match(r'^https?://', query, re.I) else f'ytsearch1:{query}'
    probe = ydl_base(folder)
    if COOKIE_YT.is_file(): probe['cookiefile'] = str(COOKIE_YT)
    with YoutubeDL(probe) as ydl:
        info = first_info(ydl.extract_info(source, download=False))
    duration = int(info.get('duration') or 0)
    if duration and duration > MUSIC_MAX:
        raise ValueError(f'Bài này dài hơn giới hạn {MUSIC_MAX // 60} phút.')
    target = str(info.get('webpage_url') or info.get('original_url') or source)
    options = ydl_base(folder)
    options.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]})
    if COOKIE_YT.is_file(): options['cookiefile'] = str(COOKIE_YT)
    with YoutubeDL(options) as ydl:
        final = first_info(ydl.extract_info(target, download=True))
    return output_file(folder, ('.mp3', '.m4a', '.opus')), final


def download_douyin(url: str, folder: Path) -> tuple[Path, dict[str, Any]]:
    from yt_dlp import YoutubeDL
    options = ydl_base(folder)
    options.update({'format': 'bv*+ba/b', 'merge_output_format': 'mp4'})
    if COOKIE_DY.is_file(): options['cookiefile'] = str(COOKIE_DY)
    with YoutubeDL(options) as ydl:
        info = first_info(ydl.extract_info(url, download=True))
    duration = int(info.get('duration') or 0)
    if duration and duration > DOUYIN_MAX:
        raise ValueError(f'Video dài hơn giới hạn {DOUYIN_MAX // 60} phút.')
    return output_file(folder, ('.mp4', '.mov', '.mkv', '.webm')), info


def media_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.casefold()
    if 'cookie' in lower or 'login' in lower:
        return 'Nguồn yêu cầu cookie hoặc đăng nhập. Cần cập nhật file cookie trong /app/atri_data.'
    if 'unsupported url' in lower:
        return 'Link này chưa được yt-dlp hỗ trợ.'
    if 'ffmpeg' in lower:
        return 'FFmpeg đang thiếu hoặc xử lý media thất bại.'
    return text if isinstance(exc, ValueError) else 'Không tải được media. Kiểm tra log bot để xem lỗi chi tiết.'


async def run_media(message, kind: str, argument: str) -> None:
    raw = reply_arg(message, argument).strip()
    if kind == 'music':
        if not raw:
            await message.reply_text(f'Cách dùng: /music{suffix()} tên bài hát hoặc link', quote=True, parse_mode=None)
            return
        task, label, prefix = download_music, '🎵 Đang tìm và xử lý bài nhạc...', 'music-'
    else:
        raw = first_url(raw)
        if not raw or not is_douyin(raw):
            await message.reply_text(f'Cách dùng: /douyin{suffix()} <link Douyin>', quote=True, parse_mode=None)
            return
        task, label, prefix = download_douyin, '🎬 Đang lấy video Douyin...', 'douyin-'
    status = await message.reply_text(label, quote=True, parse_mode=None)
    TMP.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=prefix, dir=TMP))
    try:
        async with MEDIA_LOCK:
            path, info = await asyncio.wait_for(asyncio.to_thread(task, raw, folder), 900)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError(f'File lớn hơn giới hạn {MAX_MB} MB.')
        title = str(info.get('track') or info.get('title') or ('Audio' if kind == 'music' else 'Video Douyin'))[:300]
        creator = str(info.get('artist') or info.get('uploader') or info.get('creator') or info.get('channel') or '')[:120]
        if kind == 'music':
            caption = f'🎵 {title}' + (f'\n👤 {creator}' if creator else '')
            await message.reply_audio(str(path), title=title[:128], performer=creator or None, duration=int(info.get('duration') or 0) or None, caption=caption, quote=True, parse_mode=None)
        else:
            caption = f'🎬 {title}' + (f'\n👤 {creator}' if creator else '') + '\nĐã tải luồng tốt nhất mà nguồn cung cấp; không xóa watermark đã chèn trực tiếp vào hình.'
            if path.suffix.casefold() == '.mp4':
                await message.reply_video(str(path), caption=caption, supports_streaming=True, quote=True, parse_mode=None)
            else:
                await message.reply_document(str(path), caption=caption, quote=True, parse_mode=None)
    except asyncio.TimeoutError:
        await message.reply_text('Tác vụ quá thời gian nên đã bị hủy.', quote=True, parse_mode=None)
    except Exception as exc:
        LOGGER.error('Atri %s failed: %s', kind, exc, exc_info=True)
        await message.reply_text(media_error(exc), quote=True, parse_mode=None)
    finally:
        try: await status.delete()
        except Exception: pass
        await asyncio.to_thread(shutil.rmtree, folder, True)


async def atri_free_tools_message(client, message) -> None:
    text = str(getattr(message, 'text', '') or getattr(message, 'caption', '') or '').strip()
    if not text.startswith('/'):
        return
    command, argument = parts(text)
    user = getattr(message, 'from_user', None)
    if user is None:
        return
    if match(command, 'today'):
        now = datetime.now(TZ)
        await message.reply_text(f'📅 {WEEKDAYS[now.weekday()]}, ngày {now:%d/%m/%Y}\n🕒 {now:%H:%M} · Asia/Ho_Chi_Minh', quote=True, parse_mode=None)
    elif match(command, 'calendar'):
        try:
            month, year = calendar_args(argument)
            await message.reply_text(f'📅 Lịch tháng {month}/{year}\n<pre>{calendar_text(month, year)}</pre>', quote=True, parse_mode='html')
        except ValueError as exc:
            await message.reply_text(str(exc), quote=True, parse_mode=None)
    elif match(command, 'holidays'):
        lines = ['🇻🇳 Các ngày lễ cố định sắp tới:'] + [f'• {day:%d/%m/%Y} ({WEEKDAYS[day.weekday()]}): {name}' for day, name in holidays()]
        lines.append('\nKhông gồm ngày âm lịch và lịch nghỉ bù được công bố riêng từng năm.')
        await message.reply_text('\n'.join(lines), quote=True, parse_mode=None)
    elif match(command, 'remind'):
        try:
            due, body = parse_remind(argument)
            name = re.sub(r'\s+', ' ', f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}").strip() or f'user {user.id}'
            reminder_id = await db_call(db_add, int(message.chat.id), int(getattr(message, 'message_thread_id', 0) or 0), int(user.id), name[:120], body[:1000], due)
            await message.reply_text(f'⏰ Đã tạo lời nhắc #{reminder_id}\nThời gian: {due:%H:%M ngày %d/%m/%Y}\nNội dung: {body[:1000]}', quote=True, parse_mode=None)
        except (ValueError, OverflowError) as exc:
            await message.reply_text(str(exc), quote=True, parse_mode=None)
    elif match(command, 'reminders'):
        rows = await db_call(db_list, int(message.chat.id), int(user.id))
        if not rows:
            await message.reply_text('Không có lời nhắc nào đang chờ.', quote=True, parse_mode=None)
        else:
            lines = ['⏰ Các lời nhắc đang chờ:']
            for row in rows:
                due = datetime.fromisoformat(row['due_utc']).astimezone(TZ)
                lines.append(f"#{row['id']} · {due:%H:%M %d/%m/%Y}\n  {row['body']}")
            await message.reply_text('\n'.join(lines), quote=True, parse_mode=None)
    elif match(command, 'delremind'):
        try: reminder_id = int(argument)
        except ValueError:
            await message.reply_text(f'Cách dùng: /delremind{suffix()} <ID>', quote=True, parse_mode=None); return
        deleted = await db_call(db_delete, int(message.chat.id), int(user.id), reminder_id)
        await message.reply_text(f'Đã xóa lời nhắc #{reminder_id}.' if deleted else 'Không tìm thấy lời nhắc đang chờ thuộc về bạn.', quote=True, parse_mode=None)
    elif match(command, 'music'):
        await run_media(message, 'music', argument)
    elif match(command, 'douyin'):
        await run_media(message, 'douyin', argument)


async def reminder_worker(client) -> None:
    while True:
        try:
            for row in await db_call(db_due):
                kwargs = {'message_thread_id': int(row['thread_id'])} if int(row['thread_id'] or 0) else {}
                try:
                    await client.send_message(int(row['chat_id']), f"⏰ Nhắc {str(row['user_name']).replace(chr(10), ' ')[:120]}:\n{row['body']}", parse_mode=None, **kwargs)
                    await db_call(db_sent, int(row['id']))
                except Exception as exc:
                    LOGGER.error('Reminder #%s failed: %s', row['id'], exc, exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error('Reminder worker failed: %s', exc, exc_info=True)
        await asyncio.sleep(15)


COMMANDS = (
    ('today', 'Xem ngày và giờ hiện tại'), ('calendar', 'Xem lịch tháng'),
    ('holidays', 'Xem các ngày lễ cố định'), ('remind', 'Tạo lời nhắc'),
    ('reminders', 'Xem lời nhắc đang chờ'), ('delremind', 'Xóa lời nhắc'),
    ('music', 'Tìm và gửi nhạc'), ('douyin', 'Tải video Douyin'),
)


async def merge_menu(client) -> None:
    await asyncio.sleep(8)
    try:
        current = await client.get_bot_commands()
        merged = {str(item.command).casefold(): str(item.description) for item in current}
        for command, description in COMMANDS:
            merged[f'{command}{suffix()}'.casefold()] = description
        menu = [BotCommand(command, description[:256]) for command, description in merged.items() if re.fullmatch(r'[a-z0-9_]{1,32}', command)][:100]
        await client.set_bot_commands(menu)
        LOGGER.info('Đã thêm nhóm lệnh miễn phí vào menu Telegram (%s lệnh).', len(menu))
    except Exception as exc:
        LOGGER.error('Không cập nhật được menu lệnh miễn phí: %s', exc, exc_info=True)


async def start_free_tools(client) -> None:
    global STARTED
    if STARTED:
        return
    await db_call(db_init)
    TMP.mkdir(parents=True, exist_ok=True)
    STARTED = True
    asyncio.create_task(reminder_worker(client))
    asyncio.create_task(merge_menu(client))
    LOGGER.info('Atri free tools started: calendar, reminders, music, Douyin.')
