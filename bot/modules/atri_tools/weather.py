from __future__ import annotations

import asyncio
from pprint import pprint
from typing import Any

import httpx


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_TOOL_DECLARATION: dict[str, Any] = {
    "name": "get_weather",
    "description": (
        "Lấy thời tiết hiện tại và dự báo tại một địa điểm. "
        "Dùng khi người dùng hỏi nhiệt độ, mưa, độ ẩm hoặc gió."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "Tên địa điểm, ví dụ Hà Nội hoặc Đà Nẵng.",
            },
            "forecast_days": {
                "type": "integer",
                "description": "Số ngày dự báo từ 1 đến 7.",
                "minimum": 1,
                "maximum": 7,
            },
        },
        "required": ["location"],
    },
}

WEATHER_CODES = {
    0: "Trời quang",
    1: "Chủ yếu trời quang",
    2: "Có mây rải rác",
    3: "Nhiều mây",
    45: "Sương mù",
    48: "Sương mù đóng băng",
    51: "Mưa phùn nhẹ",
    53: "Mưa phùn vừa",
    55: "Mưa phùn mạnh",
    61: "Mưa nhẹ",
    63: "Mưa vừa",
    65: "Mưa lớn",
    71: "Tuyết nhẹ",
    73: "Tuyết vừa",
    75: "Tuyết lớn",
    80: "Mưa rào nhẹ",
    81: "Mưa rào vừa",
    82: "Mưa rào mạnh",
    95: "Dông",
    96: "Dông kèm mưa đá nhẹ",
    99: "Dông kèm mưa đá mạnh",
}


def weather_description(code: Any) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "Không xác định"

    return WEATHER_CODES.get(code, f"Mã thời tiết {code}")


async def get_weather(
    location: str,
    forecast_days: int = 2,
) -> dict[str, Any]:
    location = str(location or "").strip()

    if len(location) < 2:
        return {"ok": False, "error": "Tên địa điểm không hợp lệ."}

    try:
        forecast_days = int(forecast_days)
    except (TypeError, ValueError):
        forecast_days = 2

    forecast_days = max(1, min(forecast_days, 7))

    timeout = httpx.Timeout(20.0, connect=10.0)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        try:
            response = await client.get(
                GEOCODING_URL,
                params={
                    "name": location,
                    "count": 1,
                    "language": "vi",
                    "format": "json",
                },
            )
            response.raise_for_status()
            places = response.json().get("results") or []
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "ok": False,
                "error": f"Lỗi tìm địa điểm: {exc}",
            }

        if not places:
            return {
                "ok": False,
                "error": f"Không tìm thấy địa điểm: {location}",
            }

        place = places[0]
        latitude = place.get("latitude")
        longitude = place.get("longitude")

        try:
            response = await client.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "timezone": "auto",
                    "forecast_days": forecast_days,
                    "current": (
                        "temperature_2m,"
                        "relative_humidity_2m,"
                        "apparent_temperature,"
                        "precipitation,"
                        "rain,"
                        "weather_code,"
                        "cloud_cover,"
                        "wind_speed_10m"
                    ),
                    "daily": (
                        "weather_code,"
                        "temperature_2m_max,"
                        "temperature_2m_min,"
                        "precipitation_probability_max,"
                        "precipitation_sum,"
                        "wind_speed_10m_max"
                    ),
                },
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "ok": False,
                "error": f"Lỗi lấy thời tiết: {exc}",
            }

    current = data.get("current") or {}
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    forecast = []

    for index, date in enumerate(dates):
        def item(field: str) -> Any:
            values = daily.get(field) or []
            return values[index] if index < len(values) else None

        forecast.append(
            {
                "date": date,
                "condition": weather_description(item("weather_code")),
                "temperature_max_c": item("temperature_2m_max"),
                "temperature_min_c": item("temperature_2m_min"),
                "rain_probability_percent": item(
                    "precipitation_probability_max"
                ),
                "precipitation_mm": item("precipitation_sum"),
                "wind_speed_max_kmh": item("wind_speed_10m_max"),
            }
        )

    return {
        "ok": True,
        "source": "Open-Meteo",
        "location": {
            "requested": location,
            "name": place.get("name"),
            "admin1": place.get("admin1"),
            "country": place.get("country"),
            "latitude": latitude,
            "longitude": longitude,
            "timezone": data.get("timezone"),
        },
        "current": {
            "time": current.get("time"),
            "condition": weather_description(
                current.get("weather_code")
            ),
            "temperature_c": current.get("temperature_2m"),
            "apparent_temperature_c": current.get(
                "apparent_temperature"
            ),
            "humidity_percent": current.get(
                "relative_humidity_2m"
            ),
            "precipitation_mm": current.get("precipitation"),
            "rain_mm": current.get("rain"),
            "cloud_cover_percent": current.get("cloud_cover"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
        },
        "forecast": forecast,
    }


async def execute_weather_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    if name != "get_weather":
        return {
            "ok": False,
            "error": f"Không hỗ trợ công cụ: {name}",
        }

    arguments = arguments or {}

    return await get_weather(
        location=arguments.get("location", ""),
        forecast_days=arguments.get("forecast_days", 2),
    )


async def main() -> None:
    pprint(await get_weather("Hà Nội", 2), sort_dicts=False)


if __name__ == "__main__":
    asyncio.run(main())
