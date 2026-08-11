from __future__ import annotations

from contextlib import closing
import asyncio
import calendar
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pyrogram.enums import ParseMode
from pyrogram.types import BotCommand

from bot import LOGGER
from bot.core.config_manager import Config

DATA = Path('/app/atri_data')
DB = DATA / 'atri_free_tools.sqlite3'
TMP = Path('/app/downloads/atri-free-tools')
TZ = ZoneInfo(os.getenv('ATRI_TIMEZONE', 'Asia/Ho_Chi_Minh'))
MAX_MB = max(0, int(os.getenv('ATRI_MAX_MEDIA_MB', '0')))
MAX_BYTES = MAX_MB * 1024 * 1024
MUSIC_MAX = max(0, int(os.getenv('ATRI_MAX_MUSIC_SECONDS', '0')))
DOUYIN_MAX = max(60, int(os.getenv('ATRI_MAX_DOUYIN_SECONDS', '600')))
MEDIA_TIMEOUT = max(0, int(os.getenv('ATRI_MEDIA_TIMEOUT_SECONDS', '0')))
MEDIA_CANCEL_GRACE = max(5, int(os.getenv('ATRI_MEDIA_CANCEL_GRACE_SECONDS', '45')))
MAX_REMINDER_ATTEMPTS = max(1, int(os.getenv('ATRI_REMINDER_MAX_ATTEMPTS', '6')))
COOKIE_YT = DATA / 'youtube_cookies.txt'
COOKIE_MUSIC = DATA / 'music_cookies.txt'
COOKIE_DY = DATA / 'douyin_cookies.txt'
# MUSIC_DRIVE_DEDUP_V1
MUSIC_DRIVE_ENABLED = os.getenv(
    'ATRI_MUSIC_DRIVE_CHECK', '1'
).strip().casefold() not in {'0', 'false', 'no', 'off'}
MUSIC_DRIVE_REMOTE = os.getenv(
    'ATRI_MUSIC_DRIVE_REMOTE', 'BHLNK:'
).strip()
MUSIC_DRIVE_CONFIG = Path(os.getenv(
    'ATRI_MUSIC_DRIVE_CONFIG',
    '/app/atri_data/rclone-music.conf',
))
MUSIC_DRIVE_CACHE_SECONDS = max(60, int(os.getenv(
    'ATRI_MUSIC_DRIVE_CACHE_SECONDS', '900'
)))
MUSIC_DRIVE_SCAN_TIMEOUT = max(30, int(os.getenv(
    'ATRI_MUSIC_DRIVE_SCAN_TIMEOUT_SECONDS', '300'
)))
MUSIC_DRIVE_FAIL_CLOSED = os.getenv(
    'ATRI_MUSIC_DRIVE_FAIL_CLOSED', '1'
).strip().casefold() not in {'0', 'false', 'no', 'off'}
MUSIC_DRIVE_INDEX = DATA / 'music_drive_index.sqlite3'
MUSIC_DRIVE_EXTENSIONS = (
    'mp3', 'm4a', 'opus', 'ogg', 'flac', 'aac', 'wav', 'wma',
)
MUSIC_DRIVE_LOCK = threading.Lock()
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
    with closing(connect()) as con, con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS reminders(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL,
          thread_id INTEGER NOT NULL DEFAULT 0,
          user_id INTEGER NOT NULL,
          user_name TEXT NOT NULL,
          body TEXT NOT NULL,
          due_utc TEXT NOT NULL,
          sent_utc TEXT,
          attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_utc TEXT,
          failed_utc TEXT,
          last_error TEXT
        );
        ''')
        columns = {
            row['name']
            for row in con.execute('PRAGMA table_info(reminders)')
        }
        migrations = {
            'attempts': 'ALTER TABLE reminders ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0',
            'next_attempt_utc': 'ALTER TABLE reminders ADD COLUMN next_attempt_utc TEXT',
            'failed_utc': 'ALTER TABLE reminders ADD COLUMN failed_utc TEXT',
            'last_error': 'ALTER TABLE reminders ADD COLUMN last_error TEXT',
        }
        for column, statement in migrations.items():
            if column not in columns:
                con.execute(statement)
        con.execute('DROP INDEX IF EXISTS reminders_due')
        con.execute(
            'CREATE INDEX reminders_due '
            'ON reminders(sent_utc, failed_utc, next_attempt_utc, due_utc)'
        )


def db_add(chat_id: int, thread_id: int, user_id: int, user_name: str, body: str, due: datetime) -> int:
    with closing(connect()) as con, con:
        pending = con.execute(
            'SELECT COUNT(*) FROM reminders WHERE chat_id=? AND user_id=? AND sent_utc IS NULL AND failed_utc IS NULL',
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
    with closing(connect()) as con, con:
        return list(con.execute(
            'SELECT id,body,due_utc FROM reminders WHERE chat_id=? AND user_id=? AND sent_utc IS NULL AND failed_utc IS NULL ORDER BY due_utc LIMIT 50',
            (chat_id, user_id),
        ))


def db_delete(chat_id: int, user_id: int, reminder_id: int) -> int:
    with closing(connect()) as con, con:
        return con.execute(
            'DELETE FROM reminders WHERE id=? AND chat_id=? AND user_id=? AND sent_utc IS NULL AND failed_utc IS NULL',
            (reminder_id, chat_id, user_id),
        ).rowcount


def db_due() -> list[sqlite3.Row]:
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as con, con:
        return list(con.execute(
            '''SELECT * FROM reminders
               WHERE sent_utc IS NULL
                 AND failed_utc IS NULL
                 AND due_utc<=?
                 AND (next_attempt_utc IS NULL OR next_attempt_utc<=?)
               ORDER BY due_utc LIMIT 30''',
            (now, now),
        ))


def db_sent(reminder_id: int) -> None:
    with closing(connect()) as con, con:
        con.execute(
            '''UPDATE reminders
               SET sent_utc=?, next_attempt_utc=NULL, last_error=NULL
               WHERE id=? AND sent_utc IS NULL''',
            (datetime.now(timezone.utc).isoformat(), reminder_id),
        )


def db_failed(reminder_id: int, error: str) -> tuple[int, bool]:
    now = datetime.now(timezone.utc)
    with closing(connect()) as con, con:
        row = con.execute(
            'SELECT attempts FROM reminders WHERE id=? AND sent_utc IS NULL',
            (reminder_id,),
        ).fetchone()
        if row is None:
            return 0, True
        attempts = int(row['attempts'] or 0) + 1
        terminal = attempts >= MAX_REMINDER_ATTEMPTS
        if terminal:
            con.execute(
                '''UPDATE reminders
                   SET attempts=?, failed_utc=?, next_attempt_utc=NULL, last_error=?
                   WHERE id=? AND sent_utc IS NULL''',
                (attempts, now.isoformat(), error[:1000], reminder_id),
            )
        else:
            delay_seconds = min(3600, 15 * (2 ** (attempts - 1)))
            retry_at = now + timedelta(seconds=delay_seconds)
            con.execute(
                '''UPDATE reminders
                   SET attempts=?, next_attempt_utc=?, last_error=?
                   WHERE id=? AND sent_utc IS NULL''',
                (attempts, retry_at.isoformat(), error[:1000], reminder_id),
            )
        return attempts, terminal


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


def ydl_base(
    folder: Path,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    def cancel_hook(_status: dict[str, Any]) -> None:
        if cancel_event is not None and cancel_event.is_set():
            from yt_dlp.utils import DownloadError

            raise DownloadError('Media task cancelled after timeout')

    return {
        'outtmpl': str(folder / '%(id)s.%(ext)s'), 'noplaylist': True,
        'quiet': True, 'no_warnings': True, 'restrictfilenames': True,
        'socket_timeout': 30, 'retries': 3, 'fragment_retries': 3,
        'concurrent_fragment_downloads': 4, 'cachedir': False,
        'progress_hooks': [cancel_hook],
        'postprocessor_hooks': [cancel_hook],
    }


# MUSIC_SOURCE_SPLIT_V1
YTMUSIC_DIRECT_HOSTS: tuple[tuple[str, str], ...] = (
    ('youtube.com', 'YouTube'),
    ('youtu.be', 'YouTube'),
    ('tiktok.com', 'TikTok'),
    ('douyin.com', 'Douyin'),
)
SCMUSIC_DIRECT_HOSTS: tuple[tuple[str, str], ...] = (
    ('soundcloud.com', 'SoundCloud'),
    ('bandcamp.com', 'Bandcamp'),
    ('audiomack.com', 'Audiomack'),
    ('audius.co', 'Audius'),
    ('mixcloud.com', 'Mixcloud'),
    ('jiosaavn.com', 'JioSaavn'),
    ('bandlab.com', 'BandLab'),
)
MUSIC_SEARCH_RESULTS = max(1, min(
    20,
    int(os.getenv('ATRI_MUSIC_SEARCH_RESULTS', '8')),
))
MUSIC_UNWANTED_MODIFIERS = (
    'nightcore', 'slowed', 'reverb', 'remix', 'cover', 'karaoke',
    'instrumental', 'sped up', 'speed up', '8d', 'bass boosted',
)


def music_help() -> str:
    return (
        'Chọn đúng nguồn để kết quả không bị lẫn:\n\n'
        f'• /ytmusic{suffix()} <tên bài hoặc link>\n'
        '  Tìm tên trên YouTube; nhận link YouTube/YouTube Music, '
        'TikTok và Douyin.\n\n'
        f'• /scmusic{suffix()} <tên bài hoặc link>\n'
        '  Tìm tên trên SoundCloud; nhận link SoundCloud, Bandcamp, '
        'Audiomack, Audius, Mixcloud, JioSaavn và BandLab.\n\n'
        f'/music{suffix()} chỉ hiển thị hướng dẫn này, không tự tìm.'
    )


def ytmusic_help() -> str:
    return (
        f'Cách dùng: /ytmusic{suffix()} <tên bài hoặc link>\n'
        f'Ví dụ: /ytmusic{suffix()} Chiều hôm ấy Qiz\n'
        f'Ví dụ link: /ytmusic{suffix()} https://youtu.be/...\n'
        'Tên bài chỉ được tìm trên YouTube. Link trực tiếp hỗ trợ '
        'YouTube/YouTube Music, TikTok và Douyin.'
    )


def scmusic_help() -> str:
    return (
        f'Cách dùng: /scmusic{suffix()} <tên bài hoặc link>\n'
        f'Ví dụ: /scmusic{suffix()} Chiều hôm ấy remix\n'
        f'Ví dụ link: /scmusic{suffix()} https://soundcloud.com/...\n'
        'Tên bài chỉ được tìm trên SoundCloud. Các nguồn còn lại '
        'chỉ nhận link trực tiếp.'
    )


def _music_host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f'.{domain}')


def _music_url_label(url: str, provider: str) -> str:
    host = (urlparse(url).hostname or '').casefold().rstrip('.')
    if not host:
        raise ValueError('Link nhạc không hợp lệ.')
    if _music_host_matches(host, 'spotify.com'):
        raise ValueError(
            'Không thể lấy audio trực tiếp từ Spotify. '
            'Dùng /ytmusic hoặc /scmusic với tên bài.'
        )
    if _music_host_matches(host, 'music.apple.com'):
        raise ValueError(
            'Không thể lấy bài trực tiếp từ Apple Music. '
            'Dùng /ytmusic hoặc /scmusic với tên bài.'
        )

    hosts = (
        YTMUSIC_DIRECT_HOSTS
        if provider == 'yt'
        else SCMUSIC_DIRECT_HOSTS
    )
    for domain, label in hosts:
        if _music_host_matches(host, domain):
            return label

    command = '/ytmusic' if provider == 'yt' else '/scmusic'
    raise ValueError(
        f'Link này không thuộc nhóm {command}. '
        f'Dùng /music{suffix()} để xem cách chia nguồn.'
    )


def _music_sources(
    query: str,
    provider: str,
) -> list[tuple[str, str]]:
    value = re.sub(r'\s+', ' ', query.strip())
    if not value:
        raise ValueError(
            ytmusic_help() if provider == 'yt' else scmusic_help()
        )
    if re.match(r'^https?://', value, re.I):
        return [(value, _music_url_label(value, provider))]
    if provider == 'yt':
        return [(
            f'ytsearch{MUSIC_SEARCH_RESULTS}:{value}',
            'YouTube',
        )]
    if provider == 'sc':
        return [(
            f'scsearch{MUSIC_SEARCH_RESULTS}:{value}',
            'SoundCloud',
        )]
    raise ValueError('Nhóm nguồn nhạc không hợp lệ.')


def _music_search_normalize(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', str(value or ''))
    ascii_text = ''.join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.sub(
        r'[^a-z0-9]+',
        ' ',
        ascii_text.casefold(),
    ).strip()


def _music_search_score(
    query: str,
    info: dict[str, Any],
) -> int:
    query_norm = _music_search_normalize(query)
    title = _music_search_normalize(
        info.get('track') or info.get('title') or ''
    )
    artist = _music_search_normalize(
        info.get('artist')
        or info.get('uploader')
        or info.get('creator')
        or info.get('channel')
        or ''
    )
    combined = f'{title} {artist}'.strip()
    if not query_norm or not combined:
        return -10000

    query_tokens = set(query_norm.split())
    combined_tokens = set(combined.split())
    overlap = len(query_tokens & combined_tokens)
    missing = len(query_tokens - combined_tokens)

    score = overlap * 25 - missing * 30
    if title == query_norm:
        score += 180
    if combined == query_norm:
        score += 220
    if query_norm in title:
        score += 90
    elif query_norm in combined:
        score += 60

    for modifier in MUSIC_UNWANTED_MODIFIERS:
        modifier_norm = _music_search_normalize(modifier)
        if modifier_norm in combined and modifier_norm not in query_norm:
            score -= 45

    duration = int(info.get('duration') or 0)
    if duration and duration < 30:
        score -= 40
    return score


def _music_best_info(
    payload: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    entries = payload.get('entries')
    if not isinstance(entries, list):
        return payload
    candidates = [
        item for item in entries if isinstance(item, dict)
    ]
    if not candidates:
        return {}
    return max(
        candidates,
        key=lambda item: _music_search_score(query, item),
    )


def _music_cookie(source: str) -> Path | None:
    if COOKIE_MUSIC.is_file():
        return COOKIE_MUSIC
    if source.startswith('ytsearch'):
        return COOKIE_YT if COOKIE_YT.is_file() else None
    if re.match(r'^https?://', source, re.I):
        host = (urlparse(source).hostname or '').casefold().rstrip('.')
        if (
            _music_host_matches(host, 'youtube.com')
            or host == 'youtu.be'
        ):
            return COOKIE_YT if COOKIE_YT.is_file() else None
        if (
            _music_host_matches(host, 'tiktok.com')
            or _music_host_matches(host, 'douyin.com')
        ):
            return COOKIE_DY if COOKIE_DY.is_file() else None
    return None

def _music_thumbnail(info: dict[str, Any]) -> str:
    primary = str(info.get('thumbnail') or '').strip()
    if primary.casefold().startswith(('http://', 'https://')):
        return primary
    thumbnails = info.get('thumbnails') or []
    if isinstance(thumbnails, list):
        for item in reversed(thumbnails):
            if not isinstance(item, dict):
                continue
            url = str(item.get('url') or '').strip()
            if url.casefold().startswith(('http://', 'https://')):
                return url
    return ''



# MUSIC_SINGLE_MESSAGE_COVER_V1
def _music_thumb_file(
    info: dict[str, Any],
    folder: Path,
) -> Path | None:
    """Download and convert album art for Telegram's audio thumbnail."""
    from urllib.request import Request, urlopen

    url = _music_thumbnail(info)
    if not url:
        return None

    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return None

    source = folder / 'music-cover-source'
    output = folder / 'music-cover.jpg'
    request = Request(
        url,
        headers={
            'User-Agent': (
                'Mozilla/5.0 (X11; Linux x86_64) '
                'AppleWebKit/537.36 Chrome/131 Safari/537.36'
            ),
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        },
    )

    with urlopen(request, timeout=20) as response:
        payload = response.read(5 * 1024 * 1024 + 1)

    if not payload or len(payload) > 5 * 1024 * 1024:
        return None

    source.write_bytes(payload)

    for quality in (4, 8, 12, 18, 24):
        result = subprocess.run(
            [
                ffmpeg,
                '-y',
                '-hide_banner',
                '-loglevel',
                'error',
                '-i',
                str(source),
                '-vf',
                'scale=320:320:force_original_aspect_ratio=decrease',
                '-frames:v',
                '1',
                '-q:v',
                str(quality),
                str(output),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        if (
            result.returncode == 0
            and output.is_file()
            and 0 < output.stat().st_size < 200 * 1024
        ):
            return output

    output.unlink(missing_ok=True)
    return None

def _music_duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'


class MusicDriveDuplicate(ValueError):
    pass


class MusicDriveUnavailable(ValueError):
    pass


def _music_drive_normalize(value: str) -> str:
    normalized = unicodedata.normalize('NFKD', str(value or ''))
    ascii_text = ''.join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return re.sub(r'[^a-z0-9]+', ' ', ascii_text.casefold()).strip()


def _music_drive_connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(MUSIC_DRIVE_INDEX, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    con.executescript("""
        CREATE TABLE IF NOT EXISTS music_drive_files(
          path TEXT PRIMARY KEY,
          stem_norm TEXT NOT NULL,
          path_norm TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS music_drive_stem_norm
          ON music_drive_files(stem_norm);
        CREATE TABLE IF NOT EXISTS music_drive_meta(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
    """)
    return con


def _music_drive_last_refresh() -> float:
    with closing(_music_drive_connect()) as con, con:
        row = con.execute(
            "SELECT value FROM music_drive_meta "
            "WHERE key='refreshed_at'"
        ).fetchone()
    try:
        return float(row['value']) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def _music_drive_refresh() -> None:
    if not MUSIC_DRIVE_REMOTE:
        raise RuntimeError('ATRI_MUSIC_DRIVE_REMOTE đang trống.')
    if not MUSIC_DRIVE_CONFIG.is_file():
        raise RuntimeError(
            f'Không tìm thấy rclone config: {MUSIC_DRIVE_CONFIG}'
        )
    rclone = shutil.which('rclone')
    if not rclone:
        raise RuntimeError('Không tìm thấy lệnh rclone trong container.')

    command = [
        rclone,
        'lsf',
        '--config', str(MUSIC_DRIVE_CONFIG),
        '--recursive',
        '--files-only',
    ]
    for extension in MUSIC_DRIVE_EXTENSIONS:
        command.extend(['--include', f'*.{extension}'])
        command.extend(['--include', f'*.{extension.upper()}'])
    command.append(MUSIC_DRIVE_REMOTE)

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=MUSIC_DRIVE_SCAN_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(
            f'rclone lsf thất bại ({result.returncode}): {detail}'
        )

    rows: list[tuple[str, str, str]] = []
    for raw_path in result.stdout.splitlines():
        remote_path = raw_path.strip().lstrip('/')
        if not remote_path:
            continue
        name = remote_path.rsplit('/', 1)[-1]
        stem = name.rsplit('.', 1)[0]
        rows.append((
            remote_path,
            _music_drive_normalize(stem),
            _music_drive_normalize(remote_path),
        ))

    refreshed_at = datetime.now(timezone.utc).timestamp()
    with closing(_music_drive_connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('DELETE FROM music_drive_files')
        con.executemany(
            'INSERT INTO music_drive_files(path,stem_norm,path_norm) '
            'VALUES(?,?,?)',
            rows,
        )
        con.execute(
            "INSERT INTO music_drive_meta(key,value) "
            "VALUES('refreshed_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(refreshed_at),),
        )
        con.execute(
            "INSERT INTO music_drive_meta(key,value) "
            "VALUES('file_count',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(len(rows)),),
        )


def _music_drive_ensure_index() -> None:
    if not MUSIC_DRIVE_ENABLED:
        return
    now = datetime.now(timezone.utc).timestamp()
    if now - _music_drive_last_refresh() < MUSIC_DRIVE_CACHE_SECONDS:
        return
    with MUSIC_DRIVE_LOCK:
        now = datetime.now(timezone.utc).timestamp()
        if now - _music_drive_last_refresh() < MUSIC_DRIVE_CACHE_SECONDS:
            return
        try:
            _music_drive_refresh()
        except Exception as exc:
            LOGGER.error('Atri Drive music index refresh failed: %s', exc)
            if MUSIC_DRIVE_FAIL_CLOSED:
                raise MusicDriveUnavailable(
                    'Không kiểm tra được kho nhạc Drive nên bot đã dừng '
                    'trước khi tải để tránh tạo file trùng.'
                ) from exc


def _music_drive_find_duplicate(info: dict[str, Any]) -> str:
    if not MUSIC_DRIVE_ENABLED:
        return ''
    _music_drive_ensure_index()

    source_ids = {
        _music_drive_normalize(info.get(key) or '')
        for key in ('id', 'display_id')
    }
    source_ids.discard('')

    title = _music_drive_normalize(
        info.get('track') or info.get('title') or ''
    )
    artist = _music_drive_normalize(
        info.get('artist')
        or info.get('uploader')
        or info.get('creator')
        or info.get('channel')
        or ''
    )

    exact = set(source_ids)
    if title:
        exact.add(title)
    if title and artist:
        exact.add(f'{artist} {title}')
        exact.add(f'{title} {artist}')

    with closing(_music_drive_connect()) as con, con:
        if exact:
            placeholders = ','.join('?' for _ in exact)
            row = con.execute(
                f'SELECT path FROM music_drive_files '
                f'WHERE stem_norm IN ({placeholders}) LIMIT 1',
                tuple(exact),
            ).fetchone()
            if row:
                return str(row['path'])

        for source_id in source_ids:
            if len(source_id) < 5:
                continue
            row = con.execute(
                'SELECT path FROM music_drive_files '
                'WHERE instr(path_norm, ?) > 0 LIMIT 1',
                (source_id,),
            ).fetchone()
            if row:
                return str(row['path'])

        if len(title) >= 6 and len(artist) >= 3:
            row = con.execute(
                'SELECT path FROM music_drive_files '
                'WHERE instr(path_norm, ?) > 0 '
                'AND instr(path_norm, ?) > 0 LIMIT 1',
                (title, artist),
            ).fetchone()
            if row:
                return str(row['path'])

    return ''


def _music_drive_duplicate_message(remote_path: str) -> str:
    remote = MUSIC_DRIVE_REMOTE.rstrip('/')
    path = remote_path.lstrip('/')
    return (
        f'⏹ Bài này đã có trên Drive: {remote}/{path}\n'
        'Bot đã dừng trước bước tải để tránh trùng dữ liệu.'
    )


def download_music(
    query: str,
    folder: Path,
    cancel_event: threading.Event | None = None,
    provider: str = 'yt',
) -> tuple[Path, dict[str, Any]]:
    from yt_dlp import YoutubeDL

    attempts = _music_sources(query, provider)
    errors: list[str] = []
    direct_url = bool(re.match(r'^https?://', query.strip(), re.I))

    for source, source_label in attempts:
        try:
            probe = ydl_base(folder, cancel_event)
            cookie = _music_cookie(source)
            if cookie is not None:
                probe['cookiefile'] = str(cookie)

            with YoutubeDL(probe) as ydl:
                payload = ydl.extract_info(source, download=False)

            info = (
                first_info(payload)
                if direct_url
                else _music_best_info(payload, query)
            )
            if not info:
                raise RuntimeError('Không tìm thấy kết quả phù hợp.')

            duration = int(info.get('duration') or 0)
            if MUSIC_MAX > 0 and duration and duration > MUSIC_MAX:
                raise ValueError(
                    f'Bài này dài hơn giới hạn {MUSIC_MAX // 60} phút.'
                )

            duplicate_path = _music_drive_find_duplicate(info)
            if duplicate_path:
                raise MusicDriveDuplicate(
                    _music_drive_duplicate_message(duplicate_path)
                )

            target = str(
                info.get('webpage_url')
                or info.get('original_url')
                or info.get('url')
                or source
            )
            options = ydl_base(folder, cancel_event)
            options.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
            cookie = _music_cookie(target)
            if cookie is not None:
                options['cookiefile'] = str(cookie)

            with YoutubeDL(options) as ydl:
                final = first_info(
                    ydl.extract_info(target, download=True)
                )

            if not final.get('thumbnail') and info.get('thumbnail'):
                final['thumbnail'] = info.get('thumbnail')
            if not final.get('thumbnails') and info.get('thumbnails'):
                final['thumbnails'] = info.get('thumbnails')
            final['_prix_source'] = source_label
            final['_prix_provider'] = provider
            return output_file(
                folder,
                ('.mp3', '.m4a', '.opus'),
            ), final
        except (MusicDriveDuplicate, MusicDriveUnavailable):
            raise
        except ValueError as exc:
            errors.append(f'{source_label}: {exc}')
            if len(attempts) == 1:
                raise
        except Exception as exc:
            errors.append(f'{source_label}: {exc}')
            if len(attempts) == 1:
                raise

    detail = '; '.join(errors)[:800]
    raise RuntimeError(
        f'Không tìm thấy hoặc tải được bài hát. {detail}'
    )


def download_yt_music(
    query: str,
    folder: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[Path, dict[str, Any]]:
    return download_music(
        query,
        folder,
        cancel_event,
        provider='yt',
    )


def download_sc_music(
    query: str,
    folder: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[Path, dict[str, Any]]:
    return download_music(
        query,
        folder,
        cancel_event,
        provider='sc',
    )


def download_douyin(
    url: str,
    folder: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[Path, dict[str, Any]]:
    from yt_dlp import YoutubeDL

    probe = ydl_base(folder, cancel_event)
    if COOKIE_DY.is_file():
        probe['cookiefile'] = str(COOKIE_DY)
    with YoutubeDL(probe) as ydl:
        metadata = first_info(ydl.extract_info(url, download=False))
    duration = int(metadata.get('duration') or 0)
    if duration and duration > DOUYIN_MAX:
        raise ValueError(f'Video dài hơn giới hạn {DOUYIN_MAX // 60} phút.')

    options = ydl_base(folder, cancel_event)
    options.update({'format': 'bv*+ba/b', 'merge_output_format': 'mp4'})
    if COOKIE_DY.is_file():
        options['cookiefile'] = str(COOKIE_DY)
    with YoutubeDL(options) as ydl:
        info = first_info(ydl.extract_info(url, download=True))
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


async def _cleanup_after_media_worker(
    worker: asyncio.Task,
    folder: Path,
) -> None:
    try:
        await worker
    except Exception:
        pass
    await asyncio.to_thread(shutil.rmtree, folder, True)


async def run_media(
    message,
    kind: str,
    argument: str,
) -> None:
    raw = reply_arg(message, argument).strip()
    music_kind = kind in {'ytmusic', 'scmusic'}

    if kind == 'ytmusic':
        if not raw or raw.casefold() in {'help', '?'}:
            await message.reply_text(
                ytmusic_help(), quote=True, parse_mode=None
            )
            return
        task = download_yt_music
        label = '🎵 Đang tìm trên YouTube...'
        prefix = 'ytmusic-'
    elif kind == 'scmusic':
        if not raw or raw.casefold() in {'help', '?'}:
            await message.reply_text(
                scmusic_help(), quote=True, parse_mode=None
            )
            return
        task = download_sc_music
        label = '🎵 Đang tìm trên SoundCloud...'
        prefix = 'scmusic-'
    elif kind == 'douyin':
        raw = first_url(raw)
        if not raw or not is_douyin(raw):
            await message.reply_text(
                f'Cách dùng: /douyin{suffix()} <link Douyin>',
                quote=True,
                parse_mode=None,
            )
            return
        task = download_douyin
        label = '🎬 Đang lấy video Douyin...'
        prefix = 'douyin-'
    else:
        await message.reply_text(
            music_help(), quote=True, parse_mode=None
        )
        return

    status = await message.reply_text(
        label, quote=True, parse_mode=None
    )
    TMP.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=prefix, dir=TMP))
    cancel_event = threading.Event()
    worker: asyncio.Task | None = None
    deferred_cleanup = False

    try:
        async with MEDIA_LOCK:
            worker = asyncio.create_task(
                asyncio.to_thread(
                    task, raw, folder, cancel_event
                )
            )
            if MEDIA_TIMEOUT > 0:
                path, info = await asyncio.wait_for(
                    asyncio.shield(worker),
                    MEDIA_TIMEOUT,
                )
            else:
                path, info = await worker

        if MAX_MB > 0 and path.stat().st_size > MAX_BYTES:
            raise ValueError(
                f'File lớn hơn giới hạn {MAX_MB} MB.'
            )

        title = str(
            info.get('track')
            or info.get('title')
            or ('Audio' if music_kind else 'Video Douyin')
        )[:300]
        creator = str(
            info.get('artist')
            or info.get('uploader')
            or info.get('creator')
            or info.get('channel')
            or ''
        )[:120]

        if music_kind:
            source_name = str(
                info.get('_prix_source')
                or info.get('extractor_key')
                or info.get('extractor')
                or ''
            )[:60]
            duration = int(info.get('duration') or 0)
            caption_lines = [f'🎵 {title}']
            if creator:
                caption_lines.append(f'👤 {creator}')
            if source_name:
                caption_lines.append(f'🌐 Nguồn: {source_name}')
            if duration:
                caption_lines.append(
                    f'⏱ Thời lượng: '
                    f'{_music_duration_text(duration)}'
                )
            caption = '\n'.join(caption_lines)

            thumb_path: Path | None = None
            if _music_thumbnail(info):
                try:
                    thumb_path = await asyncio.to_thread(
                        _music_thumb_file,
                        info,
                        folder,
                    )
                except Exception:
                    LOGGER.warning(
                        'Atri music embedded cover failed',
                        exc_info=True,
                    )

            await message.reply_audio(
                str(path),
                title=title[:128],
                performer=creator or None,
                duration=duration or None,
                caption=caption,
                thumb=str(thumb_path) if thumb_path else None,
                quote=True,
                parse_mode=None,
            )
        else:
            caption = (
                f'🎬 {title}'
                + (f'\n👤 {creator}' if creator else '')
                + '\nĐã tải luồng tốt nhất mà nguồn cung cấp; '
                'không xóa watermark đã chèn trực tiếp vào hình.'
            )
            if path.suffix.casefold() == '.mp4':
                await message.reply_video(
                    str(path),
                    caption=caption,
                    supports_streaming=True,
                    quote=True,
                    parse_mode=None,
                )
            else:
                await message.reply_document(
                    str(path),
                    caption=caption,
                    quote=True,
                    parse_mode=None,
                )
    except asyncio.TimeoutError:
        cancel_event.set()
        if worker is not None and not worker.done():
            deferred_cleanup = True
            asyncio.create_task(
                _cleanup_after_media_worker(worker, folder)
            )
        await message.reply_text(
            'Tác vụ quá thời gian nên đã bị hủy.',
            quote=True,
            parse_mode=None,
        )
    except Exception as exc:
        LOGGER.error(
            'Atri %s failed: %s',
            kind,
            exc,
            exc_info=True,
        )
        await message.reply_text(
            media_error(exc), quote=True, parse_mode=None
        )
    finally:
        try:
            await status.delete()
        except Exception:
            pass
        if not deferred_cleanup:
            await asyncio.to_thread(
                shutil.rmtree, folder, True
            )


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
            await message.reply_text(f'📅 Lịch tháng {month}/{year}\n<pre>{calendar_text(month, year)}</pre>', quote=True, parse_mode=ParseMode.HTML)
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
        await message.reply_text(
            music_help(), quote=True, parse_mode=None
        )
    elif match(command, 'ytmusic'):
        await run_media(message, 'ytmusic', argument)
    elif match(command, 'scmusic'):
        await run_media(message, 'scmusic', argument)
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
                    attempts, terminal = await db_call(
                        db_failed,
                        int(row['id']),
                        f'{type(exc).__name__}: {exc}',
                    )
                    LOGGER.error(
                        'Reminder #%s failed (attempt %s/%s, terminal=%s): %s',
                        row['id'],
                        attempts,
                        MAX_REMINDER_ATTEMPTS,
                        terminal,
                        exc,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.error('Reminder worker failed: %s', exc, exc_info=True)
        await asyncio.sleep(15)


COMMANDS = (
    ('today', 'Xem ngày và giờ hiện tại'), ('calendar', 'Xem lịch tháng'),
    ('holidays', 'Xem các ngày lễ cố định'), ('remind', 'Tạo lời nhắc'),
    ('reminders', 'Xem lời nhắc đang chờ'), ('delremind', 'Xóa lời nhắc'),
    ('music', 'Chọn nguồn nhạc'), ('ytmusic', 'Nhạc YouTube/TikTok'), ('scmusic', 'Nhạc SoundCloud và nguồn khác'), ('douyin', 'Tải video Douyin'),
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
    LOGGER.info('Atri free tools started: calendar, reminders, ytmusic, scmusic, Douyin.')
