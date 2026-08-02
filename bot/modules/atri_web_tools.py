from __future__ import annotations

import asyncio
import html
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pyrogram.types import BotCommand

from bot import LOGGER
from bot.core.config_manager import Config
from bot.helper.telegram_helper.bot_commands import BotCommands


SEARXNG_URL = os.getenv(
    "ATRI_SEARXNG_URL",
    "http://127.0.0.1:8080",
).rstrip("/")
SEARXNG_LANGUAGE = os.getenv("ATRI_SEARXNG_LANGUAGE", "vi").strip() or "vi"

MAX_SEARCH_RESULTS = 6
MAX_SEARCH_QUERY_CHARS = 500
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def _validated_searxng_url() -> str:
    parsed = urlparse(SEARXNG_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("ATRI_SEARXNG_URL phải là URL http/https hợp lệ")
    if parsed.username or parsed.password:
        raise RuntimeError("Không đặt credential trực tiếp trong ATRI_SEARXNG_URL")
    return SEARXNG_URL


def _suffix() -> str:
    return str(getattr(Config, "CMD_SUFFIX", "") or "")


def _command_parts(text: str) -> tuple[str, str]:
    first, _, rest = text.strip().partition(" ")
    command = first.split("@", 1)[0].casefold()
    return command, rest.strip()


def _matches(command: str, base: str) -> bool:
    return command == f"/{base}{_suffix()}".casefold()


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def _send_chunks(message, text: str) -> None:
    remaining = text.strip()

    while remaining:
        if len(remaining) <= 4000:
            chunk = remaining
            remaining = ""
        else:
            cut = remaining.rfind("\n", 0, 4000)

            if cut < 1000:
                cut = remaining.rfind(" ", 0, 4000)

            if cut < 1000:
                cut = 4000

            chunk = remaining[:cut].rstrip()
            remaining = remaining[cut:].lstrip()

        await message.reply_text(
            chunk,
            quote=True,
            parse_mode=None,
            disable_web_page_preview=True,
        )


async def _searxng_search(
    query: str,
) -> list[dict[str, Any]]:
    query = re.sub(r"\s+", " ", query).strip()
    if not query:
        return []
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise ValueError(
            f"Nội dung tìm kiếm vượt quá {MAX_SEARCH_QUERY_CHARS} ký tự."
        )

    params = {
        "q": query,
        "format": "json",
        "language": SEARXNG_LANGUAGE,
        "safesearch": 1,
    }
    endpoint = f"{_validated_searxng_url()}/search"
    last_error: Exception | None = None

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "AtriBot/1.0"},
    ) as client:
        for attempt in range(3):
            try:
                response = await client.get(endpoint, params=params)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(0.75 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("SearXNG trả về payload không phải object")
                results = payload.get("results") or []
                if not isinstance(results, list):
                    raise RuntimeError("SearXNG trả về trường results không hợp lệ")
                return [
                    item
                    for item in results
                    if isinstance(item, dict) and item.get("url")
                ][:MAX_SEARCH_RESULTS]
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.75 * (attempt + 1))
                    continue
                break

    raise RuntimeError(f"SearXNG request thất bại: {last_error}")


def _format_search_results(
    query: str,
    results: list[dict[str, Any]],
) -> str:
    if not results:
        return (
            "Không tìm thấy kết quả phù hợp cho: "
            f"{query}"
        )

    lines = [
        f"🔎 Kết quả tìm kiếm: {query}",
        "",
    ]

    for index, item in enumerate(results, 1):
        title = (
            _clean_text(item.get("title"))
            or "Không có tiêu đề"
        )
        snippet = _clean_text(item.get("content"))
        url = str(item.get("url") or "").strip()
        engine = _clean_text(item.get("engine"))

        lines.append(f"{index}. {title}")

        if snippet:
            lines.append(snippet[:420])

        if engine:
            lines.append(
                f"Nguồn tìm kiếm: {engine}"
            )

        lines.append(url)
        lines.append("")

    return "\n".join(lines).strip()


_WMO_DESCRIPTION = {
    0: "trời quang",
    1: "chủ yếu quang",
    2: "có mây rải rác",
    3: "nhiều mây",
    45: "sương mù",
    48: "sương mù đóng băng",
    51: "mưa phùn nhẹ",
    53: "mưa phùn vừa",
    55: "mưa phùn dày",
    56: "mưa phùn đóng băng nhẹ",
    57: "mưa phùn đóng băng mạnh",
    61: "mưa nhẹ",
    63: "mưa vừa",
    65: "mưa lớn",
    66: "mưa đóng băng nhẹ",
    67: "mưa đóng băng mạnh",
    71: "tuyết nhẹ",
    73: "tuyết vừa",
    75: "tuyết dày",
    77: "hạt tuyết",
    80: "mưa rào nhẹ",
    81: "mưa rào vừa",
    82: "mưa rào mạnh",
    85: "mưa tuyết nhẹ",
    86: "mưa tuyết mạnh",
    95: "dông",
    96: "dông kèm mưa đá nhẹ",
    99: "dông kèm mưa đá mạnh",
}


def _weather_description(code: Any) -> str:
    try:
        return _WMO_DESCRIPTION.get(
            int(code),
            "không xác định",
        )
    except (TypeError, ValueError):
        return "không xác định"


def _number(
    value: Any,
    digits: int = 0,
) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


async def _weather_lookup(
    location_query: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        geo_response = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={
                "name": location_query,
                "count": 1,
                "language": "vi",
                "format": "json",
            },
        )
        geo_response.raise_for_status()
        geo_payload = geo_response.json()

        locations = geo_payload.get("results") or []

        if not locations:
            raise ValueError(
                "Không tìm thấy địa điểm: "
                f"{location_query}"
            )

        location = locations[0]

        forecast_response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": (
                    "temperature_2m,"
                    "apparent_temperature,"
                    "relative_humidity_2m,"
                    "weather_code,"
                    "wind_speed_10m"
                ),
                "daily": (
                    "weather_code,"
                    "temperature_2m_max,"
                    "temperature_2m_min,"
                    "precipitation_probability_max"
                ),
                "forecast_days": 3,
                "timezone": "auto",
            },
        )
        forecast_response.raise_for_status()

    return location, forecast_response.json()


def _location_label(
    location: dict[str, Any],
) -> str:
    values = [
        location.get("name"),
        location.get("admin1"),
        location.get("country"),
    ]

    unique: list[str] = []

    for value in values:
        cleaned = _clean_text(value)

        if cleaned and cleaned not in unique:
            unique.append(cleaned)

    return ", ".join(unique)


def _day_label(
    date_value: str,
    index: int,
) -> str:
    if index == 0:
        return "Hôm nay"

    if index == 1:
        return "Ngày mai"

    try:
        return datetime.strptime(
            date_value,
            "%Y-%m-%d",
        ).strftime("%d/%m")
    except ValueError:
        return date_value


def _format_weather(
    location: dict[str, Any],
    forecast: dict[str, Any],
) -> str:
    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}

    lines = [
        f"🌦 Thời tiết: {_location_label(location)}",
        (
            "Hiện tại: "
            f"{_number(current.get('temperature_2m'))}°C, "
            "cảm giác "
            f"{_number(current.get('apparent_temperature'))}°C, "
            f"{_weather_description(current.get('weather_code'))}."
        ),
        (
            "Độ ẩm: "
            f"{_number(current.get('relative_humidity_2m'))}% · "
            "Gió: "
            f"{_number(current.get('wind_speed_10m'))} km/h"
        ),
        "",
        "Dự báo 3 ngày:",
    ]

    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rain = (
        daily.get("precipitation_probability_max")
        or []
    )

    for index, date_value in enumerate(dates[:3]):
        code = (
            codes[index]
            if index < len(codes)
            else None
        )
        high = (
            highs[index]
            if index < len(highs)
            else None
        )
        low = (
            lows[index]
            if index < len(lows)
            else None
        )
        rain_probability = (
            rain[index]
            if index < len(rain)
            else None
        )

        lines.append(
            f"• {_day_label(str(date_value), index)}: "
            f"{_weather_description(code)}, "
            f"{_number(low)}–{_number(high)}°C, "
            "xác suất mưa tối đa "
            f"{_number(rain_probability)}%."
        )

    lines.extend(
        [
            "",
            "Nguồn dữ liệu: Open-Meteo",
        ]
    )

    return "\n".join(lines)


async def atri_tools_message(
    client,
    message,
) -> None:
    raw_text = str(
        getattr(message, "text", "")
        or getattr(message, "caption", "")
        or ""
    ).strip()

    if not raw_text.startswith("/"):
        return

    command, argument = _command_parts(raw_text)

    if _matches(command, "websearch"):
        if not argument:
            await message.reply_text(
                "Cách dùng: "
                f"/websearch{_suffix()} nội_dung_cần_tìm",
                quote=True,
                parse_mode=None,
            )
            return

        try:
            results = await _searxng_search(
                argument
            )
            await _send_chunks(
                message,
                _format_search_results(
                    argument,
                    results,
                ),
            )
        except Exception as exc:
            LOGGER.error(
                "Atri web search failed: %s",
                exc,
                exc_info=True,
            )
            await message.reply_text(
                "Công cụ tìm kiếm đang lỗi hoặc "
                "chưa kết nối được với SearXNG.",
                quote=True,
                parse_mode=None,
            )

        return

    if _matches(command, "weather"):
        if not argument:
            await message.reply_text(
                f"Cách dùng: /weather{_suffix()} Hà Nội",
                quote=True,
                parse_mode=None,
            )
            return

        try:
            location, forecast = (
                await _weather_lookup(argument)
            )
            await _send_chunks(
                message,
                _format_weather(
                    location,
                    forecast,
                ),
            )
        except ValueError as exc:
            await message.reply_text(
                str(exc),
                quote=True,
                parse_mode=None,
            )
        except Exception as exc:
            LOGGER.error(
                "Atri weather lookup failed: %s",
                exc,
                exc_info=True,
            )
            await message.reply_text(
                "Không lấy được dữ liệu thời tiết "
                "lúc này.",
                quote=True,
                parse_mode=None,
            )


_CORE_DESCRIPTIONS = {
    "StartCommand": "Khởi động bot",
    "MirrorCommand": "Mirror liên kết lên cloud",
    "QbMirrorCommand": "Mirror torrent bằng qBittorrent",
    "JdMirrorCommand": "Mirror bằng JDownloader",
    "YtdlCommand": "Tải video bằng yt-dlp",
    "GallerydlCommand": "Tải gallery bằng gallery-dl",
    "NzbMirrorCommand": "Mirror NZB",
    "LeechCommand": "Tải và gửi tệp lên Telegram",
    "QbLeechCommand": "Leech torrent bằng qBittorrent",
    "JdLeechCommand": "Leech bằng JDownloader",
    "YtdlLeechCommand": "Leech video bằng yt-dlp",
    "GallerydlLeechCommand": "Leech gallery bằng gallery-dl",
    "NzbLeechCommand": "Leech NZB",
    "CloneCommand": "Sao chép tệp cloud",
    "CountCommand": "Đếm tệp và dung lượng",
    "DeleteCommand": "Xóa tệp trên Drive",
    "CancelTaskCommand": "Hủy tác vụ",
    "CancelAllCommand": "Hủy nhiều tác vụ",
    "ForceStartCommand": "Buộc chạy tác vụ",
    "ListCommand": "Tìm tệp trên Drive",
    "SearchCommand": "Tìm torrent",
    "StatusCommand": "Xem trạng thái tác vụ",
    "UsersCommand": "Quản lý người dùng",
    "AuthorizeCommand": "Cấp quyền sử dụng bot",
    "UnAuthorizeCommand": "Thu hồi quyền sử dụng",
    "AddSudoCommand": "Thêm sudo user",
    "RmSudoCommand": "Xóa sudo user",
    "PingCommand": "Kiểm tra độ trễ bot",
    "RestartCommand": "Khởi động lại bot",
    "StatsCommand": "Xem thống kê hệ thống",
    "HelpCommand": "Xem hướng dẫn",
    "LogCommand": "Lấy log bot",
    "ShellCommand": "Chạy lệnh shell",
    "AExecCommand": "Chạy Python bất đồng bộ",
    "ExecCommand": "Chạy Python",
    "ClearLocalsCommand": "Xóa biến thực thi",
    "BotSetCommand": "Mở cài đặt bot",
    "UserSetCommand": "Mở cài đặt người dùng",
    "SelectCommand": "Chọn tệp torrent",
    "RssCommand": "Quản lý RSS",
    "NzbSearchCommand": "Tìm NZB",
}


def _primary_command(value: Any) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, (list, tuple)) and value:
        return str(value[0])

    return ""


def _menu_entries() -> list[tuple[str, str]]:
    suffix = _suffix()

    entries: list[tuple[str, str]] = [
        (
            f"ai{suffix}",
            "Hỏi Atri",
        ),
        (
            f"atri{suffix}",
            "Bật, tắt hoặc xem trạng thái Atri",
        ),
        (
            f"resetai{suffix}",
            "Xóa ngữ cảnh trò chuyện Atri",
        ),
        (
            f"amodel{suffix}",
            "Chọn model cho Atri",
        ),
        (
            f"athink{suffix}",
            "Chọn mức suy luận của Atri",
        ),
        (
            f"websearch{suffix}",
            "Tìm kiếm web, không dùng Gemini",
        ),
        (
            f"weather{suffix}",
            "Xem thời tiết, không dùng Gemini",
        ),
        (
            f"stickerlearn{suffix}",
            "Bật hoặc tắt học sticker",
        ),
        (
            f"stickerreply{suffix}",
            "Bật hoặc tắt gửi sticker",
        ),
        (
            f"stickerchance{suffix}",
            "Đặt xác suất gửi sticker",
        ),
        (
            f"stickercooldown{suffix}",
            "Đặt thời gian chờ sticker",
        ),
        (
            f"stickerlimit{suffix}",
            "Đặt giới hạn sticker",
        ),
        (
            f"stickerstats{suffix}",
            "Xem thống kê sticker",
        ),
    ]

    for attribute, value in vars(
        BotCommands
    ).items():
        if attribute.startswith("_"):
            continue

        command = _primary_command(value)

        if not command:
            continue

        description = _CORE_DESCRIPTIONS.get(
            attribute,
            f"Lệnh {command}",
        )

        entries.append(
            (
                command,
                description,
            )
        )

    unique: list[tuple[str, str]] = []
    seen: set[str] = set()

    for command, description in entries:
        command = (
            command.casefold().lstrip("/")
        )

        if not re.fullmatch(
            r"[a-z0-9_]{1,32}",
            command,
        ):
            continue

        if command in seen:
            continue

        seen.add(command)

        unique.append(
            (
                command,
                description[:256],
            )
        )

    return unique[:100]


async def sync_bot_command_menu(
    client,
) -> None:
    await asyncio.sleep(2)

    try:
        commands = [
            BotCommand(
                command=command,
                description=description,
            )
            for command, description
            in _menu_entries()
        ]

        await client.set_bot_commands(commands)

        LOGGER.info(
            "Đã đồng bộ %s lệnh vào menu Telegram.",
            len(commands),
        )
    except Exception as exc:
        LOGGER.error(
            "Không đồng bộ được menu lệnh "
            "Telegram: %s",
            exc,
            exc_info=True,
        )
