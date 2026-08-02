from __future__ import annotations

import asyncio
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bot import LOGGER
from bot.core.config_manager import Config

DATA = Path(os.getenv("ATRI_DATA_DIR", "/app/atri_data"))
TMP = Path(os.getenv("ATRI_MUSIC_TMP", "/app/downloads/atri-music"))
DRIVE_DB = DATA / "music_drive_index.sqlite3"
COOKIE_YT = DATA / "youtube_cookies.txt"
COOKIE_MUSIC = DATA / "music_cookies.txt"
COOKIE_DY = DATA / "douyin_cookies.txt"

DRIVE_CHECK = os.getenv("ATRI_MUSIC_DRIVE_CHECK", "1").strip().casefold() not in {
    "0",
    "false",
    "no",
    "off",
}
DRIVE_REMOTE = os.getenv("ATRI_MUSIC_DRIVE_REMOTE", "BHLNK:").strip()
DRIVE_CONFIG = Path(
    os.getenv(
        "ATRI_MUSIC_DRIVE_CONFIG",
        "/app/atri_data/rclone-music.conf",
    )
)
DRIVE_CACHE_SECONDS = max(
    60,
    int(os.getenv("ATRI_MUSIC_DRIVE_CACHE_SECONDS", "900")),
)
DRIVE_SCAN_TIMEOUT = max(
    30,
    int(os.getenv("ATRI_MUSIC_DRIVE_SCAN_TIMEOUT_SECONDS", "300")),
)
DRIVE_FAIL_CLOSED = os.getenv(
    "ATRI_MUSIC_DRIVE_FAIL_CLOSED",
    "1",
).strip().casefold() not in {"0", "false", "no", "off"}

MEDIA_CONCURRENCY = max(1, int(os.getenv("ATRI_MEDIA_CONCURRENCY", "2")))
MEDIA_LOCK = asyncio.Semaphore(MEDIA_CONCURRENCY)
DRIVE_LOCK = asyncio.Lock()
IN_FLIGHT_LOCK = asyncio.Lock()
IN_FLIGHT: set[str] = set()

AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".opus",
    ".ogg",
    ".flac",
    ".aac",
    ".wav",
}

YT_DIRECT_HOSTS = {
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "douyin.com",
}
SC_DIRECT_HOSTS = {
    "soundcloud.com",
    "bandcamp.com",
    "audiomack.com",
    "audius.co",
    "mixcloud.com",
    "jiosaavn.com",
    "bandlab.com",
}

UNWANTED_MODIFIERS = {
    "8d",
    "bass boosted",
    "cover",
    "instrumental",
    "karaoke",
    "live",
    "nightcore",
    "reverb",
    "remix",
    "slowed",
    "speed up",
    "sped up",
}


def suffix() -> str:
    return str(getattr(Config, "CMD_SUFFIX", "") or "")


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _source_label(info: dict[str, Any], fallback: str) -> str:
    return str(
        info.get("_prix_source")
        or info.get("extractor_key")
        or info.get("extractor")
        or fallback
    )[:60]


def _creator(info: dict[str, Any]) -> str:
    return str(
        info.get("artist")
        or info.get("uploader")
        or info.get("creator")
        or info.get("channel")
        or ""
    )[:120]


def _title(info: dict[str, Any]) -> str:
    return str(info.get("track") or info.get("title") or "Audio")[:300]


def _thumbnail(info: dict[str, Any]) -> str:
    value = str(info.get("thumbnail") or "").strip()
    if value.startswith(("http://", "https://")):
        return value
    thumbnails = info.get("thumbnails") or []
    if isinstance(thumbnails, list):
        for item in reversed(thumbnails):
            if not isinstance(item, dict):
                continue
            value = str(item.get("url") or "").strip()
            if value.startswith(("http://", "https://")):
                return value
    return ""


def _duration_text(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def music_help() -> str:
    command_suffix = suffix()
    return (
        "Chọn đúng nguồn để tìm chính xác hơn:\n\n"
        f"/ytmusic{command_suffix} <tên bài hoặc link>\n"
        "• Tìm tên bài chỉ trên YouTube.\n"
        "• Nhận link YouTube, YouTube Music, TikTok và Douyin.\n\n"
        f"/scmusic{command_suffix} <tên bài hoặc link>\n"
        "• Tìm tên bài chỉ trên SoundCloud.\n"
        "• Nhận link SoundCloud, Bandcamp, Audiomack, Audius, "
        "Mixcloud, JioSaavn và BandLab."
    )


def _direct_url(value: str, allowed_hosts: set[str]) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and bool(host)
        and any(_host_matches(host, domain) for domain in allowed_hosts)
    )


def _candidate_score(query: str, info: dict[str, Any]) -> int:
    query_norm = _normalize(query)
    query_tokens = set(query_norm.split())
    title = _normalize(info.get("track") or info.get("title"))
    creator = _normalize(
        info.get("artist")
        or info.get("uploader")
        or info.get("creator")
        or info.get("channel")
    )
    combined = f"{title} {creator}".strip()
    combined_tokens = set(combined.split())

    score = 0
    if title == query_norm:
        score += 300
    if combined == query_norm:
        score += 350
    if query_norm and query_norm in title:
        score += 180
    if query_norm and query_norm in combined:
        score += 120
    score += 25 * len(query_tokens & combined_tokens)
    score -= 20 * len(query_tokens - combined_tokens)

    for modifier in UNWANTED_MODIFIERS:
        if modifier in combined and modifier not in query_norm:
            score -= 90

    duration = int(info.get("duration") or 0)
    if duration and duration < 45:
        score -= 60
    return score


def _first_info(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("Nguồn không trả về metadata hợp lệ.")
    entries = value.get("entries")
    if isinstance(entries, list):
        valid = [item for item in entries if isinstance(item, dict)]
        if not valid:
            raise RuntimeError("Không tìm thấy kết quả phù hợp.")
        return valid[0]
    return value


def _select_search_result(query: str, payload: dict[str, Any]) -> dict[str, Any]:
    entries = payload.get("entries") or []
    candidates = [item for item in entries if isinstance(item, dict)]
    if not candidates:
        raise RuntimeError("Không tìm thấy kết quả phù hợp.")
    return max(candidates, key=lambda item: _candidate_score(query, item))


def _ydl_base(folder: Path) -> dict[str, Any]:
    return {
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "cachedir": False,
    }


def _cookie_for(target: str, source_kind: str) -> Path | None:
    if COOKIE_MUSIC.is_file():
        return COOKIE_MUSIC
    if source_kind == "yt":
        parsed = urlparse(target)
        host = (parsed.hostname or "").casefold()
        if not host or _host_matches(host, "youtube.com") or host == "youtu.be":
            return COOKIE_YT if COOKIE_YT.is_file() else None
        if _host_matches(host, "tiktok.com") or _host_matches(host, "douyin.com"):
            return COOKIE_DY if COOKIE_DY.is_file() else None
    return None


def _probe_music(query: str, source_kind: str, folder: Path) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    value = re.sub(r"\s+", " ", query.strip())
    if not value:
        raise ValueError(music_help())

    if value.startswith(("http://", "https://")):
        allowed = YT_DIRECT_HOSTS if source_kind == "yt" else SC_DIRECT_HOSTS
        if not _direct_url(value, allowed):
            raise ValueError("Link không thuộc nhóm nguồn của lệnh này.")
        target = value
        label = "YouTube/TikTok" if source_kind == "yt" else "SoundCloud/Audio"
        options = _ydl_base(folder)
        cookie = _cookie_for(target, source_kind)
        if cookie:
            options["cookiefile"] = str(cookie)
        with YoutubeDL(options) as ydl:
            info = _first_info(ydl.extract_info(target, download=False))
    else:
        prefix = "ytsearch8:" if source_kind == "yt" else "scsearch8:"
        label = "YouTube" if source_kind == "yt" else "SoundCloud"
        options = _ydl_base(folder)
        cookie = _cookie_for("", source_kind)
        if cookie:
            options["cookiefile"] = str(cookie)
        with YoutubeDL(options) as ydl:
            payload = ydl.extract_info(f"{prefix}{value}", download=False)
        if not isinstance(payload, dict):
            raise RuntimeError("Nguồn không trả về danh sách kết quả.")
        info = _select_search_result(value, payload)

    info["_prix_source"] = label
    info["_prix_source_kind"] = source_kind
    info["_prix_target"] = str(
        info.get("webpage_url")
        or info.get("original_url")
        or info.get("url")
        or value
    )
    return info


def _download_probed(info: dict[str, Any], folder: Path) -> tuple[Path, dict[str, Any]]:
    from yt_dlp import YoutubeDL

    source_kind = str(info.get("_prix_source_kind") or "yt")
    target = str(info.get("_prix_target") or "")
    if not target:
        raise RuntimeError("Metadata thiếu URL tải.")

    options = _ydl_base(folder)
    options.update(
        {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    )
    cookie = _cookie_for(target, source_kind)
    if cookie:
        options["cookiefile"] = str(cookie)

    with YoutubeDL(options) as ydl:
        final = _first_info(ydl.extract_info(target, download=True))

    final["_prix_source"] = info.get("_prix_source")
    final["_prix_source_kind"] = source_kind
    files = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
    ]
    if not files:
        raise RuntimeError("yt-dlp không tạo được file audio.")
    return max(files, key=lambda path: path.stat().st_size), final


def _drive_connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DRIVE_DB, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS music_drive_files(
          path TEXT PRIMARY KEY,
          normalized TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS music_drive_meta(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    return connection


def _drive_refresh_needed() -> bool:
    with _drive_connect() as connection:
        row = connection.execute(
            "SELECT value FROM music_drive_meta WHERE key='refreshed_at'"
        ).fetchone()
    if not row:
        return True
    try:
        refreshed_at = float(row[0])
    except (TypeError, ValueError):
        return True
    return time.time() - refreshed_at >= DRIVE_CACHE_SECONDS


def _scan_drive_sync() -> None:
    if not DRIVE_CONFIG.is_file():
        raise RuntimeError(f"Thiếu rclone config: {DRIVE_CONFIG}")
    if not DRIVE_REMOTE:
        raise RuntimeError("ATRI_MUSIC_DRIVE_REMOTE đang trống.")

    command = [
        "rclone",
        "lsf",
        "--config",
        str(DRIVE_CONFIG),
        "--recursive",
        "--files-only",
        DRIVE_REMOTE,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=DRIVE_SCAN_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise RuntimeError(f"Không quét được Drive: {detail}")

    files = []
    for raw in result.stdout.splitlines():
        path = raw.strip()
        if not path or Path(path).suffix.casefold() not in AUDIO_EXTENSIONS:
            continue
        files.append((path, _normalize(Path(path).stem)))

    with _drive_connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM music_drive_files")
        connection.executemany(
            "INSERT INTO music_drive_files(path, normalized) VALUES(?, ?)",
            files,
        )
        connection.execute(
            """
            INSERT INTO music_drive_meta(key, value)
            VALUES('refreshed_at', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(time.time()),),
        )


def _drive_duplicate_sync(info: dict[str, Any]) -> str | None:
    title = _normalize(_title(info))
    creator = _normalize(_creator(info))
    source_id = _normalize(info.get("id"))
    keys = [key for key in (source_id, f"{creator} {title}".strip(), title) if len(key) >= 4]
    if not keys:
        return None

    with _drive_connect() as connection:
        rows = connection.execute(
            "SELECT path, normalized FROM music_drive_files"
        ).fetchall()

    for path, normalized in rows:
        normalized = str(normalized)
        if source_id and len(source_id) >= 6 and source_id in normalized:
            return str(path)
        if title and title == normalized:
            return str(path)
        if title and title in normalized and (not creator or creator in normalized):
            return str(path)
    return None


async def _drive_duplicate(info: dict[str, Any]) -> str | None:
    if not DRIVE_CHECK:
        return None
    async with DRIVE_LOCK:
        try:
            if await asyncio.to_thread(_drive_refresh_needed):
                await asyncio.to_thread(_scan_drive_sync)
            return await asyncio.to_thread(_drive_duplicate_sync, info)
        except Exception:
            LOGGER.exception("Music Drive duplicate check failed")
            if DRIVE_FAIL_CLOSED:
                raise RuntimeError(
                    "Không kiểm tra được Drive nên bot đã dừng để tránh tải trùng."
                )
            return None


def _request_key(info: dict[str, Any]) -> str:
    return "|".join(
        part
        for part in (
            _normalize(info.get("extractor_key") or info.get("extractor")),
            _normalize(info.get("id")),
            _normalize(_creator(info)),
            _normalize(_title(info)),
        )
        if part
    )


async def _run_music(message, source_kind: str) -> None:
    text = str(getattr(message, "text", "") or "").strip()
    _, _, argument = text.partition(" ")
    argument = argument.strip()
    if not argument or argument.casefold() in {"help", "?"}:
        await message.reply_text(music_help(), quote=True, parse_mode=None)
        return

    status = await message.reply_text(
        "🎵 Đang tìm metadata và kiểm tra Drive...",
        quote=True,
        parse_mode=None,
    )
    TMP.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"{source_kind}-", dir=TMP))
    request_key = ""

    try:
        async with MEDIA_LOCK:
            info = await asyncio.to_thread(
                _probe_music,
                argument,
                source_kind,
                folder,
            )
            duplicate = await _drive_duplicate(info)
            if duplicate:
                await message.reply_text(
                    "⏭ Bài này đã có trên Drive nên bot không tải lại.\n"
                    f"📁 {DRIVE_REMOTE}{duplicate}",
                    quote=True,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
                return

            request_key = _request_key(info)
            async with IN_FLIGHT_LOCK:
                if request_key and request_key in IN_FLIGHT:
                    await message.reply_text(
                        "⏳ Bài này đang được một tác vụ khác xử lý.",
                        quote=True,
                        parse_mode=None,
                    )
                    return
                if request_key:
                    IN_FLIGHT.add(request_key)

            path, final = await asyncio.to_thread(
                _download_probed,
                info,
                folder,
            )

        title = _title(final)
        creator = _creator(final)
        duration = int(final.get("duration") or 0)
        source_name = _source_label(final, "YouTube" if source_kind == "yt" else "SoundCloud")
        caption_lines = [f"🎵 {title}"]
        if creator:
            caption_lines.append(f"👤 {creator}")
        caption_lines.append(f"🌐 Nguồn: {source_name}")
        if duration:
            caption_lines.append(f"⏱ Thời lượng: {_duration_text(duration)}")
        caption = "\n".join(caption_lines)

        audio_caption: str | None = caption
        thumbnail = _thumbnail(final)
        if thumbnail:
            try:
                await message.reply_photo(
                    thumbnail,
                    caption=caption,
                    quote=True,
                    parse_mode=None,
                )
                audio_caption = None
            except Exception:
                LOGGER.warning(
                    "Music cover send failed: %s",
                    thumbnail,
                    exc_info=True,
                )

        await message.reply_audio(
            str(path),
            title=title[:128],
            performer=creator or None,
            duration=duration or None,
            caption=audio_caption,
            quote=True,
            parse_mode=None,
        )
    except Exception as exc:
        LOGGER.exception("Music command failed")
        await message.reply_text(
            str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Không tải được bài nhạc. Kiểm tra log bot.",
            quote=True,
            parse_mode=None,
        )
    finally:
        if request_key:
            async with IN_FLIGHT_LOCK:
                IN_FLIGHT.discard(request_key)
        try:
            await status.delete()
        except Exception:
            pass
        await asyncio.to_thread(shutil.rmtree, folder, True)


async def music_command(_, message) -> None:
    await message.reply_text(music_help(), quote=True, parse_mode=None)


async def ytmusic_command(_, message) -> None:
    await _run_music(message, "yt")


async def scmusic_command(_, message) -> None:
    await _run_music(message, "sc")
