from __future__ import annotations

import asyncio
import base64
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials

from bot import LOGGER
from bot.core.config_manager import Config


CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_cloud_credentials = None
_cloud_lock = asyncio.Lock()
_workspace_credentials = None
_workspace_lock = asyncio.Lock()


def _setting(name: str, default: str = "") -> str:
    env_value = os.getenv(name)
    if env_value is not None and env_value.strip():
        return env_value.strip()

    configured = getattr(Config, name, "")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    if isinstance(configured, bool):
        return "1" if configured else "0"
    return default


def _bool_setting(name: str, default: bool = False) -> bool:
    return _setting(name, "1" if default else "0").casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _project_id() -> str:
    return _setting("GOOGLE_CLOUD_PROJECT") or _setting("VERTEX_PROJECT_ID")


def _api_key(*names: str) -> str:
    for name in names:
        value = _setting(name)
        if value:
            return value
    return ""


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def _error(message: str, *, code: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "error": str(message)[:1000]}
    if code:
        result["code"] = code
    return result


async def _json_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: Any = None,
    json_body: Any = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Network error: {type(exc).__name__}") from exc

    if response.status_code >= 400:
        detail = response.text[:800]
        try:
            payload = response.json()
            error_obj = payload.get("error")
            if isinstance(error_obj, dict):
                detail = str(
                    error_obj.get("message")
                    or error_obj.get("status")
                    or detail
                )
        except ValueError:
            pass
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("API returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("API returned unexpected payload.")
    return payload


async def _cloud_token() -> str:
    global _cloud_credentials

    async with _cloud_lock:
        if _cloud_credentials is None:
            credential_path = _setting("GOOGLE_APPLICATION_CREDENTIALS")
            if not credential_path:
                raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS chưa cấu hình.")

            path = Path(credential_path)
            if not path.is_file():
                raise RuntimeError(f"Không tìm thấy credential: {credential_path}")

            _cloud_credentials = (
                service_account.Credentials.from_service_account_file(
                    str(path),
                    scopes=[CLOUD_SCOPE],
                )
            )

        if (
            not _cloud_credentials.valid
            or _cloud_credentials.expired
            or not _cloud_credentials.token
        ):
            await asyncio.to_thread(
                _cloud_credentials.refresh,
                GoogleAuthRequest(),
            )

        return str(_cloud_credentials.token or "")


async def _workspace_token() -> str:
    global _workspace_credentials

    async with _workspace_lock:
        if _workspace_credentials is None:
            client_id = _setting("GOOGLE_OAUTH_CLIENT_ID")
            client_secret = _setting("GOOGLE_OAUTH_CLIENT_SECRET")
            refresh_token = _setting("GOOGLE_OAUTH_REFRESH_TOKEN")

            if client_id and client_secret and refresh_token:
                _workspace_credentials = UserCredentials(
                    token=None,
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=client_id,
                    client_secret=client_secret,
                    scopes=WORKSPACE_SCOPES,
                )
            elif _bool_setting("GOOGLE_WORKSPACE_SERVICE_ACCOUNT", False):
                credential_path = _setting("GOOGLE_APPLICATION_CREDENTIALS")
                if not credential_path:
                    raise RuntimeError("Workspace service account chưa cấu hình.")

                creds = service_account.Credentials.from_service_account_file(
                    credential_path,
                    scopes=WORKSPACE_SCOPES,
                )
                subject = _setting("GOOGLE_WORKSPACE_SUBJECT")
                if subject:
                    creds = creds.with_subject(subject)
                _workspace_credentials = creds
            else:
                raise RuntimeError(
                    "Workspace OAuth chưa cấu hình. Cần GOOGLE_OAUTH_CLIENT_ID, "
                    "GOOGLE_OAUTH_CLIENT_SECRET và GOOGLE_OAUTH_REFRESH_TOKEN."
                )

        if (
            not _workspace_credentials.valid
            or _workspace_credentials.expired
            or not _workspace_credentials.token
        ):
            await asyncio.to_thread(
                _workspace_credentials.refresh,
                GoogleAuthRequest(),
            )

        return str(_workspace_credentials.token or "")


def _owner_only(message) -> bool:
    if message is None:
        return False
    user = getattr(message, "from_user", None)
    user_id = int(getattr(user, "id", 0) or 0)
    return user_id == int(Config.OWNER_ID)


YOUTUBE_SEARCH_DECLARATION: dict[str, Any] = {
    "name": "google_youtube_search",
    "description": (
        "Tìm video công khai trên YouTube. Dùng khi người dùng yêu cầu tìm "
        "video, kênh hoặc nội dung YouTube."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Từ khóa tìm kiếm YouTube."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            "region_code": {
                "type": "string",
                "description": "Mã quốc gia ISO-3166-1 alpha-2, ví dụ VN.",
            },
        },
        "required": ["query"],
    },
}

SAFE_BROWSING_DECLARATION: dict[str, Any] = {
    "name": "google_safe_browsing",
    "description": (
        "Kiểm tra URL có nằm trong danh sách URL nguy hiểm của Google "
        "Safe Browsing hay không."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách URL http/https cần kiểm tra.",
            }
        },
        "required": ["urls"],
    },
}

PLACES_SEARCH_DECLARATION: dict[str, Any] = {
    "name": "google_places_search",
    "description": (
        "Tìm địa điểm, cửa hàng, nhà hàng, khách sạn và POI bằng Google Places."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Mô tả địa điểm cần tìm."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            "latitude": {"type": "number"},
            "longitude": {"type": "number"},
            "radius_meters": {"type": "number"},
        },
        "required": ["query"],
    },
}

ROUTE_DECLARATION: dict[str, Any] = {
    "name": "google_route",
    "description": (
        "Tính quãng đường và thời gian di chuyển bằng Google Routes. "
        "Dùng khi hỏi đường, khoảng cách hoặc thời gian đi."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "origin_latitude": {"type": "number"},
            "origin_longitude": {"type": "number"},
            "destination_latitude": {"type": "number"},
            "destination_longitude": {"type": "number"},
            "travel_mode": {
                "type": "string",
                "enum": ["DRIVE", "TWO_WHEELER", "BICYCLE", "WALK", "TRANSIT"],
            },
        },
        "required": [],
    },
}

TRANSLATE_DECLARATION: dict[str, Any] = {
    "name": "google_translate",
    "description": "Dịch văn bản bằng Google Cloud Translation.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "target_language": {"type": "string"},
            "source_language": {"type": "string"},
        },
        "required": ["text", "target_language"],
    },
}

BOOKS_SEARCH_DECLARATION: dict[str, Any] = {
    "name": "google_books_search",
    "description": "Tìm sách, tác giả, ISBN và metadata sách bằng Google Books.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
}

DRIVE_SEARCH_DECLARATION: dict[str, Any] = {
    "name": "google_drive_search",
    "description": (
        "Tìm file trong Google Drive riêng của chủ bot. Chỉ dùng khi chủ bot yêu cầu."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    },
}

DRIVE_READ_DECLARATION: dict[str, Any] = {
    "name": "google_drive_read_text",
    "description": (
        "Đọc nội dung văn bản của file Google Drive sau khi đã tìm thấy. "
        "Chỉ dành cho chủ bot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "mime_type": {"type": "string"},
        },
        "required": ["file_id", "mime_type"],
    },
}

CALENDAR_DECLARATION: dict[str, Any] = {
    "name": "google_calendar_events",
    "description": "Đọc Google Calendar của chủ bot. Chỉ dùng khi chủ bot yêu cầu.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "time_min": {"type": "string", "description": "RFC3339."},
            "time_max": {"type": "string", "description": "RFC3339."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": [],
    },
}

GMAIL_SEARCH_DECLARATION: dict[str, Any] = {
    "name": "google_gmail_search",
    "description": (
        "Tìm email trong Gmail riêng của chủ bot bằng cú pháp tìm kiếm Gmail."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
}

GMAIL_READ_DECLARATION: dict[str, Any] = {
    "name": "google_gmail_read",
    "description": (
        "Đọc một email Gmail bằng message_id từ google_gmail_search. "
        "Chỉ dành cho chủ bot."
    ),
    "parameters": {
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
    },
}

TTS_DECLARATION: dict[str, Any] = {
    "name": "google_tts_speak",
    "description": (
        "Chuyển văn bản thành giọng nói và gửi voice Telegram. "
        "Chỉ gọi khi người dùng yêu cầu trả lời bằng giọng."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "language_code": {"type": "string"},
            "voice_name": {"type": "string"},
        },
        "required": ["text"],
    },
}

GEOCODE_DECLARATION: dict[str, Any] = {
    "name": "google_geocode",
    "description": (
        "Chuyển địa chỉ/tên địa điểm thành tọa độ và địa chỉ chuẩn bằng Google Geocoding."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "address": {"type": "string"},
            "language": {"type": "string"},
        },
        "required": ["address"],
    },
}

VISION_OCR_DECLARATION: dict[str, Any] = {
    "name": "google_vision_ocr",
    "description": (
        "OCR ảnh/tài liệu ảnh đang gửi hoặc đang reply bằng Google Cloud Vision. "
        "Dùng khi cần trích xuất chữ chính xác từ ảnh."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

DOCUMENT_AI_DECLARATION: dict[str, Any] = {
    "name": "google_document_ai",
    "description": (
        "Phân tích tài liệu/PDF/ảnh đang gửi hoặc đang reply bằng Google Document AI. "
        "Chỉ dùng khi Document AI processor đã cấu hình."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

SHEETS_READ_DECLARATION: dict[str, Any] = {
    "name": "google_sheets_read",
    "description": (
        "Đọc một vùng dữ liệu Google Sheets riêng của chủ bot. Chỉ dành cho chủ bot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spreadsheet_id": {"type": "string"},
            "range": {"type": "string", "description": "A1 notation, ví dụ Sheet1!A1:D20."},
        },
        "required": ["spreadsheet_id", "range"],
    },
}

CAPABILITIES_DECLARATION: dict[str, Any] = {
    "name": "google_capabilities",
    "description": "Kiểm tra nhóm Google tool nào của Atri đã có credential/config.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

GOOGLE_TOOL_DECLARATIONS: list[dict[str, Any]] = [
    YOUTUBE_SEARCH_DECLARATION,
    SAFE_BROWSING_DECLARATION,
    TRANSLATE_DECLARATION,
    BOOKS_SEARCH_DECLARATION,
    DRIVE_SEARCH_DECLARATION,
    DRIVE_READ_DECLARATION,
    CALENDAR_DECLARATION,
    GMAIL_SEARCH_DECLARATION,
    GMAIL_READ_DECLARATION,
    TTS_DECLARATION,
    GEOCODE_DECLARATION,
    VISION_OCR_DECLARATION,
    DOCUMENT_AI_DECLARATION,
    SHEETS_READ_DECLARATION,
    CAPABILITIES_DECLARATION,
]
GOOGLE_TOOL_NAMES = {item["name"] for item in GOOGLE_TOOL_DECLARATIONS}
PRIVATE_TOOL_NAMES = {
    "google_drive_search",
    "google_drive_read_text",
    "google_calendar_events",
    "google_gmail_search",
    "google_gmail_read",
    "google_sheets_read",
}


async def youtube_search(
    query: str,
    max_results: int = 5,
    region_code: str = "VN",
) -> dict[str, Any]:
    key = _api_key("YOUTUBE_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return _error(
            "Thiếu YOUTUBE_API_KEY hoặc GOOGLE_API_KEY.",
            code="NOT_CONFIGURED",
        )

    query = str(query or "").strip()
    if not query:
        return _error("Query YouTube rỗng.")

    payload = await _json_request(
        "GET",
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": _clamp_int(max_results, 1, 10, 5),
            "regionCode": str(region_code or "VN").strip().upper()[:2],
            "safeSearch": "moderate",
            "key": key,
        },
    )

    results = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        identity = item.get("id") or {}
        snippet = item.get("snippet") or {}
        if not isinstance(identity, dict) or not isinstance(snippet, dict):
            continue
        video_id = str(identity.get("videoId") or "").strip()
        if not video_id:
            continue
        results.append(
            {
                "video_id": video_id,
                "title": snippet.get("title"),
                "channel": snippet.get("channelTitle"),
                "published_at": snippet.get("publishedAt"),
                "description": str(snippet.get("description") or "")[:400],
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )

    return _ok(source="YouTube Data API v3", query=query, results=results)


async def safe_browsing(
    urls: list[str],
) -> dict[str, Any]:
    key = _api_key(
        "SAFE_BROWSING_API_KEY",
        "GOOGLE_API_KEY",
    )
    if not key:
        return _error(
            "Thiếu SAFE_BROWSING_API_KEY hoặc GOOGLE_API_KEY.",
            code="NOT_CONFIGURED",
        )

    cleaned = []

    for raw in urls or []:
        value = str(raw or "").strip()

        if value.startswith(("http://", "https://")):
            cleaned.append(value)

    cleaned = cleaned[:50]

    if not cleaned:
        return _error(
            "Không có URL http/https hợp lệ."
        )

    params: list[tuple[str, str]] = [
        ("key", key),
    ]

    params.extend(
        ("urls[]", url)
        for url in cleaned
    )

    payload = await _json_request(
        "GET",
        "https://safebrowsing.googleapis.com/v5/urls:search",
        headers={
            "Accept": "application/json",
        },
        params=params,
    )

    threats = payload.get("threats") or []

    return _ok(
        source="Google Safe Browsing v5",
        checked_urls=cleaned,
        unsafe=bool(threats),
        threats=threats,
        cache_duration=payload.get("cacheDuration"),
    )


async def places_search(
    query: str,
    max_results: int = 5,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_meters: float = 5000,
) -> dict[str, Any]:
    key = _api_key("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return _error(
            "Thiếu GOOGLE_MAPS_API_KEY hoặc GOOGLE_API_KEY.",
            code="NOT_CONFIGURED",
        )

    query = str(query or "").strip()
    if not query:
        return _error("Query địa điểm rỗng.")

    body: dict[str, Any] = {
        "textQuery": query,
        "pageSize": _clamp_int(max_results, 1, 10, 5),
        "languageCode": "vi",
        "regionCode": _setting("GOOGLE_DEFAULT_REGION", "VN").upper()[:2],
    }

    if latitude is not None and longitude is not None:
        try:
            body["locationBias"] = {
                "circle": {
                    "center": {
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                    },
                    "radius": max(100.0, min(50000.0, float(radius_meters or 5000))),
                }
            }
        except (TypeError, ValueError):
            pass

    field_mask = ",".join(
        [
            "places.id",
            "places.displayName",
            "places.formattedAddress",
            "places.location",
            "places.googleMapsUri",
            "places.rating",
            "places.userRatingCount",
        ]
    )
    payload = await _json_request(
        "POST",
        "https://places.googleapis.com/v1/places:searchText",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": field_mask,
        },
        json_body=body,
    )

    results = []
    for place in payload.get("places") or []:
        if not isinstance(place, dict):
            continue
        display_name = place.get("displayName") or {}
        results.append(
            {
                "place_id": place.get("id"),
                "name": (
                    display_name.get("text")
                    if isinstance(display_name, dict)
                    else display_name
                ),
                "address": place.get("formattedAddress"),
                "location": place.get("location"),
                "rating": place.get("rating"),
                "user_rating_count": place.get("userRatingCount"),
                "google_maps_url": place.get("googleMapsUri"),
            }
        )

    return _ok(source="Google Places API (New)", query=query, results=results)


def _waypoint(text: str, latitude: Any = None, longitude: Any = None) -> dict[str, Any] | None:
    if latitude is not None and longitude is not None:
        try:
            return {
                "location": {
                    "latLng": {
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                    }
                }
            }
        except (TypeError, ValueError):
            pass
    text = str(text or "").strip()
    return {"address": text} if text else None


async def route_lookup(
    *,
    origin: str = "",
    destination: str = "",
    origin_latitude: Any = None,
    origin_longitude: Any = None,
    destination_latitude: Any = None,
    destination_longitude: Any = None,
    travel_mode: str = "DRIVE",
) -> dict[str, Any]:
    key = _api_key("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return _error(
            "Thiếu GOOGLE_MAPS_API_KEY hoặc GOOGLE_API_KEY.",
            code="NOT_CONFIGURED",
        )

    origin_waypoint = _waypoint(origin, origin_latitude, origin_longitude)
    destination_waypoint = _waypoint(
        destination,
        destination_latitude,
        destination_longitude,
    )
    if origin_waypoint is None or destination_waypoint is None:
        return _error("Cần đủ điểm xuất phát và điểm đến.")

    mode = str(travel_mode or "DRIVE").strip().upper()
    if mode not in {"DRIVE", "TWO_WHEELER", "BICYCLE", "WALK", "TRANSIT"}:
        mode = "DRIVE"

    body: dict[str, Any] = {
        "origin": origin_waypoint,
        "destination": destination_waypoint,
        "travelMode": mode,
        "languageCode": "vi",
        "units": "METRIC",
    }
    if mode in {"DRIVE", "TWO_WHEELER"}:
        body["routingPreference"] = "TRAFFIC_AWARE"

    payload = await _json_request(
        "POST",
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": (
                "routes.distanceMeters,routes.duration,routes.staticDuration"
            ),
        },
        json_body=body,
    )
    return _ok(
        source="Google Routes API",
        travel_mode=mode,
        routes=(payload.get("routes") or [])[:3],
    )


def _chunk_text(text: str, size: int = 900) -> list[str]:
    text = str(text or "").strip()
    chunks = []
    while text:
        if len(text) <= size:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, size)
        if cut < size // 3:
            cut = text.rfind(" ", 0, size)
        if cut < size // 3:
            cut = size
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return [chunk for chunk in chunks if chunk][:20]


async def translate_text(
    text: str,
    target_language: str,
    source_language: str = "",
) -> dict[str, Any]:
    project = _project_id()
    if not project:
        return _error(
            "Thiếu VERTEX_PROJECT_ID/GOOGLE_CLOUD_PROJECT.",
            code="NOT_CONFIGURED",
        )

    target_language = str(target_language or "").strip()
    if not target_language:
        return _error("Thiếu target_language.")
    chunks = _chunk_text(text)
    if not chunks:
        return _error("Văn bản cần dịch rỗng.")

    token = await _cloud_token()
    body: dict[str, Any] = {
        "contents": chunks,
        "mimeType": "text/plain",
        "targetLanguageCode": target_language,
    }
    source_language = str(source_language or "").strip()
    if source_language:
        body["sourceLanguageCode"] = source_language

    payload = await _json_request(
        "POST",
        (
            "https://translation.googleapis.com/v3/"
            f"projects/{quote(project, safe='')}/locations/global:translateText"
        ),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json_body=body,
    )
    translations = []
    detected = None
    for item in payload.get("translations") or []:
        if not isinstance(item, dict):
            continue
        translations.append(str(item.get("translatedText") or ""))
        if detected is None:
            detected = item.get("detectedLanguageCode")

    return _ok(
        source="Google Cloud Translation v3",
        target_language=target_language,
        detected_language=detected,
        translated_text="\n".join(translations).strip(),
    )


async def books_search(query: str, max_results: int = 5) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        return _error("Query sách rỗng.")

    params: dict[str, Any] = {
        "q": query,
        "maxResults": _clamp_int(max_results, 1, 10, 5),
        "printType": "books",
    }
    key = _api_key("GOOGLE_API_KEY")
    if key:
        params["key"] = key

    payload = await _json_request(
        "GET",
        "https://www.googleapis.com/books/v1/volumes",
        params=params,
    )
    results = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        info = item.get("volumeInfo") or {}
        if not isinstance(info, dict):
            continue
        results.append(
            {
                "id": item.get("id"),
                "title": info.get("title"),
                "authors": info.get("authors"),
                "publisher": info.get("publisher"),
                "published_date": info.get("publishedDate"),
                "description": str(info.get("description") or "")[:600],
                "isbn": [
                    ident.get("identifier")
                    for ident in (info.get("industryIdentifiers") or [])
                    if isinstance(ident, dict)
                ],
                "info_link": info.get("infoLink"),
            }
        )
    return _ok(source="Google Books API", query=query, results=results)


def _drive_query(value: str) -> str:
    escaped = str(value or "").replace("\\", "\\\\").replace("'", "\\'").strip()
    if not escaped:
        return "trashed = false"
    return f"fullText contains '{escaped}' and trashed = false"


async def drive_search(query: str, max_results: int = 10) -> dict[str, Any]:
    token = await _workspace_token()
    payload = await _json_request(
        "GET",
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "q": _drive_query(query),
            "pageSize": _clamp_int(max_results, 1, 20, 10),
            "orderBy": "modifiedTime desc",
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size)",
        },
    )
    return _ok(
        source="Google Drive API v3",
        query=query,
        files=payload.get("files") or [],
    )


async def drive_read_text(file_id: str, mime_type: str) -> dict[str, Any]:
    token = await _workspace_token()
    file_id = str(file_id or "").strip()
    mime_type = str(mime_type or "").strip()
    if not file_id:
        return _error("Thiếu file_id.")

    headers = {"Authorization": f"Bearer {token}"}
    if mime_type.startswith("application/vnd.google-apps."):
        url = (
            "https://www.googleapis.com/drive/v3/files/"
            f"{quote(file_id, safe='')}/export"
        )
        params = {"mimeType": "text/plain"}
    else:
        url = "https://www.googleapis.com/drive/v3/files/" + quote(file_id, safe="")
        params = {"alt": "media"}

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return _error(f"Không đọc được file Drive: {exc}")

    raw = response.content[:2_000_000]
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return _error("File không có nội dung text đọc được.")
    return _ok(
        source="Google Drive API v3",
        file_id=file_id,
        content=text[:20000],
        truncated=len(text) > 20000,
    )


def _rfc3339_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def calendar_events(
    query: str = "",
    time_min: str = "",
    time_max: str = "",
    max_results: int = 10,
) -> dict[str, Any]:
    token = await _workspace_token()
    if not time_min:
        time_min = _rfc3339_now()
    if not time_max:
        time_max = (
            datetime.now(timezone.utc) + timedelta(days=7)
        ).isoformat().replace("+00:00", "Z")

    params: dict[str, Any] = {
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": _clamp_int(max_results, 1, 20, 10),
    }
    if str(query or "").strip():
        params["q"] = str(query).strip()

    payload = await _json_request(
        "GET",
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )
    events = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        events.append(
            {
                "id": item.get("id"),
                "summary": item.get("summary"),
                "description": str(item.get("description") or "")[:1000],
                "location": item.get("location"),
                "start": item.get("start"),
                "end": item.get("end"),
                "status": item.get("status"),
                "html_link": item.get("htmlLink"),
            }
        )
    return _ok(
        source="Google Calendar API v3",
        time_min=time_min,
        time_max=time_max,
        events=events,
    )


def _header_map(headers: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for header in headers or []:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name") or "").casefold()
        if name:
            result[name] = str(header.get("value") or "")
    return result


async def gmail_search(query: str, max_results: int = 5) -> dict[str, Any]:
    token = await _workspace_token()
    payload = await _json_request(
        "GET",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "q": str(query or "").strip(),
            "maxResults": _clamp_int(max_results, 1, 10, 5),
        },
    )

    messages = []
    for ref in payload.get("messages") or []:
        if not isinstance(ref, dict):
            continue
        message_id = str(ref.get("id") or "").strip()
        if not message_id:
            continue
        detail = await _json_request(
            "GET",
            (
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                + quote(message_id, safe="")
            ),
            headers={"Authorization": f"Bearer {token}"},
            params=[
                ("format", "metadata"),
                ("metadataHeaders", "Subject"),
                ("metadataHeaders", "From"),
                ("metadataHeaders", "To"),
                ("metadataHeaders", "Date"),
            ],
        )
        headers_map = _header_map(((detail.get("payload") or {}).get("headers") or []))
        messages.append(
            {
                "message_id": message_id,
                "thread_id": detail.get("threadId"),
                "subject": headers_map.get("subject"),
                "from": headers_map.get("from"),
                "to": headers_map.get("to"),
                "date": headers_map.get("date"),
                "snippet": detail.get("snippet"),
                "label_ids": detail.get("labelIds"),
            }
        )
    return _ok(source="Gmail API v1", query=query, messages=messages)


def _gmail_body_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    mime_type = str(payload.get("mimeType") or "")
    body = payload.get("body") or {}
    if mime_type.startswith("text/plain"):
        data = body.get("data") if isinstance(body, dict) else None
        if data:
            try:
                padding = "=" * (-len(data) % 4)
                return base64.urlsafe_b64decode(data + padding).decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                return ""

    plain_parts = []
    fallback_parts = []
    for part in payload.get("parts") or []:
        if not isinstance(part, dict):
            continue
        text = _gmail_body_text(part)
        if not text:
            continue
        if str(part.get("mimeType") or "").startswith("text/plain"):
            plain_parts.append(text)
        else:
            fallback_parts.append(text)
    return "\n".join(plain_parts or fallback_parts)


async def gmail_read(message_id: str) -> dict[str, Any]:
    token = await _workspace_token()
    message_id = str(message_id or "").strip()
    if not message_id:
        return _error("Thiếu message_id.")

    detail = await _json_request(
        "GET",
        (
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
            + quote(message_id, safe="")
        ),
        headers={"Authorization": f"Bearer {token}"},
        params={"format": "full"},
    )
    payload = detail.get("payload") or {}
    headers_map = _header_map(payload.get("headers") or [])
    body_text = _gmail_body_text(payload).strip()
    return _ok(
        source="Gmail API v1",
        message_id=message_id,
        subject=headers_map.get("subject"),
        from_=headers_map.get("from"),
        to=headers_map.get("to"),
        date=headers_map.get("date"),
        body=body_text[:20000],
        truncated=len(body_text) > 20000,
    )


async def text_to_speech(
    text: str,
    *,
    language_code: str = "vi-VN",
    voice_name: str = "",
) -> bytes:
    token = await _cloud_token()
    text = str(text or "").strip()[:3000]
    if not text:
        raise RuntimeError("Nội dung TTS rỗng.")

    voice: dict[str, Any] = {"languageCode": str(language_code or "vi-VN").strip()}
    voice_name = str(voice_name or "").strip()
    if voice_name:
        voice["name"] = voice_name

    payload = await _json_request(
        "POST",
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json_body={
            "input": {"text": text},
            "voice": voice,
            "audioConfig": {"audioEncoding": "OGG_OPUS"},
        },
    )
    encoded = str(payload.get("audioContent") or "")
    if not encoded:
        raise RuntimeError("TTS không trả audioContent.")
    return base64.b64decode(encoded)


async def send_tts_voice(
    message,
    text: str,
    *,
    language_code: str = "vi-VN",
    voice_name: str = "",
) -> dict[str, Any]:
    if message is None:
        return _error("Không có Telegram message để gửi voice.")

    audio = await text_to_speech(
        text,
        language_code=language_code,
        voice_name=voice_name,
    )
    stream = BytesIO(audio)
    stream.name = "atri-google-tts.ogg"
    await message.reply_voice(stream, quote=True)
    return _ok(
        source="Google Cloud Text-to-Speech",
        sent=True,
        bytes=len(audio),
    )


async def transcribe_telegram_message(message) -> str:
    media = getattr(message, "voice", None) or getattr(message, "audio", None)
    if media is None:
        return ""

    project = _project_id()
    if not project:
        return ""

    downloaded = await message.download(in_memory=True)
    if downloaded is None:
        return ""
    if hasattr(downloaded, "getvalue"):
        data = downloaded.getvalue()
    elif isinstance(downloaded, (bytes, bytearray)):
        data = bytes(downloaded)
    else:
        return ""
    if not data or len(data) > 10 * 1024 * 1024:
        return ""

    token = await _cloud_token()
    payload = await _json_request(
        "POST",
        (
            "https://speech.googleapis.com/v2/projects/"
            f"{quote(project, safe='')}/locations/global/recognizers/_:recognize"
        ),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json_body={
            "config": {
                "autoDecodingConfig": {},
                "model": "long",
                "languageCodes": ["vi-VN", "en-US"],
                "features": {"enableAutomaticPunctuation": True},
                "model": "long",
            },
            "content": base64.b64encode(data).decode("ascii"),
        },
    )

    parts = []
    for result in payload.get("results") or []:
        if not isinstance(result, dict):
            continue
        alternatives = result.get("alternatives") or []
        if alternatives and isinstance(alternatives[0], dict):
            transcript = str(alternatives[0].get("transcript") or "").strip()
            if transcript:
                parts.append(transcript)
    return " ".join(parts).strip()


async def build_gemini_audio_part(message) -> dict[str, Any] | None:
    media = getattr(message, "voice", None) or getattr(message, "audio", None)
    if media is None:
        return None

    downloaded = await message.download(in_memory=True)
    if downloaded is None:
        return None
    if hasattr(downloaded, "getvalue"):
        data = downloaded.getvalue()
    elif isinstance(downloaded, (bytes, bytearray)):
        data = bytes(downloaded)
    else:
        return None
    if not data or len(data) > 20 * 1024 * 1024:
        return None

    mime_type = str(getattr(media, "mime_type", "") or "audio/ogg")
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }



async def geocode_address(
    address: str,
    language: str = "vi",
) -> dict[str, Any]:
    key = _api_key("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY")
    if not key:
        return _error(
            "Thiếu GOOGLE_MAPS_API_KEY hoặc GOOGLE_API_KEY.",
            code="NOT_CONFIGURED",
        )

    address = str(address or "").strip()
    if not address:
        return _error("Địa chỉ rỗng.")

    payload = await _json_request(
        "GET",
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={
            "address": address,
            "language": str(language or "vi").strip(),
            "key": key,
        },
    )
    status = str(payload.get("status") or "")
    if status and status != "OK":
        return _error(
            f"Geocoding status={status}: {payload.get('error_message') or ''}"
        )

    results = []
    for item in (payload.get("results") or [])[:8]:
        if not isinstance(item, dict):
            continue
        geometry = item.get("geometry") or {}
        results.append(
            {
                "formatted_address": item.get("formatted_address"),
                "place_id": item.get("place_id"),
                "location": geometry.get("location") if isinstance(geometry, dict) else None,
                "location_type": geometry.get("location_type") if isinstance(geometry, dict) else None,
                "types": item.get("types"),
            }
        )
    return _ok(source="Google Geocoding API", query=address, results=results)


async def _telegram_attachment(message) -> tuple[bytes, str] | None:
    if message is None:
        return None

    target = message
    if not (
        getattr(target, "photo", None)
        or getattr(target, "document", None)
    ):
        reply = getattr(message, "reply_to_message", None)
        if reply is not None and (
            getattr(reply, "photo", None)
            or getattr(reply, "document", None)
        ):
            target = reply
        else:
            return None

    downloaded = await target.download(in_memory=True)
    if downloaded is None:
        return None
    if hasattr(downloaded, "getvalue"):
        data = downloaded.getvalue()
    elif isinstance(downloaded, (bytes, bytearray)):
        data = bytes(downloaded)
    else:
        return None

    if not data or len(data) > 15 * 1024 * 1024:
        return None

    document = getattr(target, "document", None)
    mime_type = str(
        getattr(document, "mime_type", "")
        or "image/jpeg"
    )
    return data, mime_type


async def vision_ocr(message) -> dict[str, Any]:
    attachment = await _telegram_attachment(message)
    if attachment is None:
        return _error("Hãy gửi/reply một ảnh hoặc tài liệu ảnh để OCR.")

    data, mime_type = attachment
    if not mime_type.startswith("image/"):
        return _error("Cloud Vision OCR trực tiếp chỉ nhận ảnh trong tool này.")

    token = await _cloud_token()
    payload = await _json_request(
        "POST",
        "https://vision.googleapis.com/v1/images:annotate",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json_body={
            "requests": [
                {
                    "image": {"content": base64.b64encode(data).decode("ascii")},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "imageContext": {"languageHints": ["vi", "en"]},
                }
            ]
        },
    )
    responses = payload.get("responses") or []
    first = responses[0] if responses and isinstance(responses[0], dict) else {}
    error_obj = first.get("error") or {}
    if error_obj:
        return _error(str(error_obj.get("message") or error_obj))
    annotation = first.get("fullTextAnnotation") or {}
    text = str(annotation.get("text") or "").strip()
    return _ok(
        source="Google Cloud Vision OCR",
        text=text[:30000],
        truncated=len(text) > 30000,
        pages=len(annotation.get("pages") or []) if isinstance(annotation, dict) else 0,
    )


async def document_ai_process(message) -> dict[str, Any]:
    project = _project_id()
    processor = _setting("GOOGLE_DOCUMENT_AI_PROCESSOR_ID")
    location = _setting("GOOGLE_DOCUMENT_AI_LOCATION", "us")
    if not project or not processor:
        return _error(
            "Thiếu GOOGLE_DOCUMENT_AI_PROCESSOR_ID hoặc project.",
            code="NOT_CONFIGURED",
        )

    attachment = await _telegram_attachment(message)
    if attachment is None:
        return _error("Hãy gửi/reply PDF hoặc ảnh cần phân tích.")
    data, mime_type = attachment

    token = await _cloud_token()
    host = f"{location}-documentai.googleapis.com"
    url = (
        f"https://{host}/v1/projects/{quote(project, safe='')}/"
        f"locations/{quote(location, safe='')}/processors/"
        f"{quote(processor, safe='')}:process"
    )
    payload = await _json_request(
        "POST",
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json_body={
            "rawDocument": {
                "content": base64.b64encode(data).decode("ascii"),
                "mimeType": mime_type,
            }
        },
    )
    document = payload.get("document") or {}
    text = str(document.get("text") or "").strip() if isinstance(document, dict) else ""
    entities = []
    if isinstance(document, dict):
        for entity in (document.get("entities") or [])[:100]:
            if not isinstance(entity, dict):
                continue
            entities.append(
                {
                    "type": entity.get("type"),
                    "mention_text": entity.get("mentionText"),
                    "confidence": entity.get("confidence"),
                    "normalized_value": entity.get("normalizedValue"),
                }
            )
    return _ok(
        source="Google Document AI",
        text=text[:30000],
        truncated=len(text) > 30000,
        entities=entities,
    )


async def sheets_read(
    spreadsheet_id: str,
    range_a1: str,
) -> dict[str, Any]:
    token = await _workspace_token()
    spreadsheet_id = str(spreadsheet_id or "").strip()
    range_a1 = str(range_a1 or "").strip()
    if not spreadsheet_id or not range_a1:
        return _error("Thiếu spreadsheet_id hoặc range.")

    payload = await _json_request(
        "GET",
        (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(spreadsheet_id, safe='')}/values/{quote(range_a1, safe='!:$')}"
        ),
        headers={"Authorization": f"Bearer {token}"},
        params={"majorDimension": "ROWS"},
    )
    values = payload.get("values") or []
    return _ok(
        source="Google Sheets API v4",
        spreadsheet_id=spreadsheet_id,
        range=payload.get("range") or range_a1,
        values=values[:500],
        truncated=len(values) > 500,
    )


def google_capabilities() -> dict[str, Any]:
    workspace_oauth = bool(
        _setting("GOOGLE_OAUTH_CLIENT_ID")
        and _setting("GOOGLE_OAUTH_CLIENT_SECRET")
        and _setting("GOOGLE_OAUTH_REFRESH_TOKEN")
    ) or _bool_setting("GOOGLE_WORKSPACE_SERVICE_ACCOUNT", False)
    cloud = bool(
        _project_id()
        and _setting("GOOGLE_APPLICATION_CREDENTIALS")
    )
    maps = bool(_api_key("GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY"))
    return _ok(
        configured={
            "vertex_web_search": bool(_project_id()),
            "youtube": bool(_api_key("YOUTUBE_API_KEY", "GOOGLE_API_KEY")),
            "safe_browsing": bool(_api_key("SAFE_BROWSING_API_KEY", "GOOGLE_API_KEY")),
            "places_routes_geocoding": maps,
            "translation_speech_tts_vision": cloud,
            "document_ai": bool(cloud and _setting("GOOGLE_DOCUMENT_AI_PROCESSOR_ID")),
            "gmail_drive_calendar_sheets": workspace_oauth,
            "books": True,
        }
    )


async def execute_google_tool(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    message=None,
) -> dict[str, Any]:
    arguments = arguments or {}

    if name in PRIVATE_TOOL_NAMES and not _owner_only(message):
        return _error("Công cụ này chỉ dành cho chủ bot.", code="OWNER_ONLY")

    try:
        if name == "google_youtube_search":
            return await youtube_search(
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 5),
                region_code=arguments.get(
                    "region_code",
                    _setting("GOOGLE_DEFAULT_REGION", "VN"),
                ),
            )
        if name == "google_safe_browsing":
            return await safe_browsing(list(arguments.get("urls") or []))
        if name == "google_places_search":
            return await places_search(
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 5),
                latitude=arguments.get("latitude"),
                longitude=arguments.get("longitude"),
                radius_meters=arguments.get("radius_meters", 5000),
            )
        if name == "google_route":
            return await route_lookup(
                origin=arguments.get("origin", ""),
                destination=arguments.get("destination", ""),
                origin_latitude=arguments.get("origin_latitude"),
                origin_longitude=arguments.get("origin_longitude"),
                destination_latitude=arguments.get("destination_latitude"),
                destination_longitude=arguments.get("destination_longitude"),
                travel_mode=arguments.get("travel_mode", "DRIVE"),
            )
        if name == "google_translate":
            return await translate_text(
                text=arguments.get("text", ""),
                target_language=arguments.get("target_language", ""),
                source_language=arguments.get("source_language", ""),
            )
        if name == "google_books_search":
            return await books_search(
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 5),
            )
        if name == "google_drive_search":
            return await drive_search(
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 10),
            )
        if name == "google_drive_read_text":
            return await drive_read_text(
                file_id=arguments.get("file_id", ""),
                mime_type=arguments.get("mime_type", ""),
            )
        if name == "google_calendar_events":
            return await calendar_events(
                query=arguments.get("query", ""),
                time_min=arguments.get("time_min", ""),
                time_max=arguments.get("time_max", ""),
                max_results=arguments.get("max_results", 10),
            )
        if name == "google_gmail_search":
            return await gmail_search(
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 5),
            )
        if name == "google_gmail_read":
            return await gmail_read(message_id=arguments.get("message_id", ""))
        if name == "google_tts_speak":
            return await send_tts_voice(
                message,
                text=arguments.get("text", ""),
                language_code=arguments.get("language_code", "vi-VN"),
                voice_name=arguments.get("voice_name", ""),
            )
        if name == "google_geocode":
            return await geocode_address(
                address=arguments.get("address", ""),
                language=arguments.get("language", "vi"),
            )
        if name == "google_vision_ocr":
            return await vision_ocr(message)
        if name == "google_document_ai":
            return await document_ai_process(message)
        if name == "google_sheets_read":
            return await sheets_read(
                spreadsheet_id=arguments.get("spreadsheet_id", ""),
                range_a1=arguments.get("range", ""),
            )
        if name == "google_capabilities":
            return google_capabilities()
        return _error(f"Không hỗ trợ Google tool: {name}")
    except Exception as exc:
        LOGGER.exception("Atri Google tool failed: %s", name)
        return _error(f"{name} lỗi: {exc}")


# ATRI_SAFE_BROWSING_V5_FINAL
async def safe_browsing(
    urls: list[str],
) -> dict[str, Any]:
    key = _api_key(
        "SAFE_BROWSING_API_KEY",
        "GOOGLE_API_KEY",
    )

    if not key:
        return _error(
            "Thiếu SAFE_BROWSING_API_KEY.",
            code="NOT_CONFIGURED",
        )

    cleaned: list[str] = []

    for raw in urls or []:
        value = str(raw or "").strip()

        if value.startswith(("http://", "https://")):
            cleaned.append(value)

    cleaned = cleaned[:50]

    if not cleaned:
        return _error(
            "Không có URL http/https hợp lệ."
        )

    params: list[tuple[str, str]] = [
        ("key", key),
    ]

    params.extend(
        ("urls[]", url)
        for url in cleaned
    )

    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                "https://safebrowsing.googleapis.com/v5/urls:search",
                params=params,
                headers={
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        return _error(
            f"Safe Browsing network error: {type(exc).__name__}"
        )

    if response.status_code >= 400:
        detail = response.text[:800]

        try:
            payload = response.json()
            error_obj = payload.get("error") or {}

            if isinstance(error_obj, dict):
                detail = str(
                    error_obj.get("message")
                    or detail
                )
        except ValueError:
            pass

        return _error(
            f"Safe Browsing HTTP "
            f"{response.status_code}: {detail}"
        )

    raw_body = response.content.strip()

    if not raw_body:
        payload = {}
    else:
        try:
            payload = response.json()
        except ValueError:
            return _error(
                "Safe Browsing trả response không phải JSON: "
                + response.text[:300]
            )

    threats = payload.get("threats") or []

    return _ok(
        source="Google Safe Browsing v5",
        checked_urls=cleaned,
        unsafe=bool(threats),
        threats=threats,
        cache_duration=payload.get("cacheDuration"),
    )



# ATRI_SAFE_BROWSING_V4_STABLE
async def safe_browsing(
    urls: list[str],
) -> dict[str, Any]:
    key = _api_key(
        "SAFE_BROWSING_API_KEY",
        "GOOGLE_API_KEY",
    )

    if not key:
        return _error(
            "Thiếu SAFE_BROWSING_API_KEY.",
            code="NOT_CONFIGURED",
        )

    cleaned: list[str] = []

    for raw in urls or []:
        value = str(raw or "").strip()

        if value.startswith(("http://", "https://")):
            cleaned.append(value)

    cleaned = cleaned[:50]

    if not cleaned:
        return _error(
            "Không có URL http/https hợp lệ."
        )

    payload = await _json_request(
        "POST",
        "https://safebrowsing.googleapis.com/v4/threatMatches:find",
        params={
            "key": key,
        },
        json_body={
            "client": {
                "clientId": "prixok-atri",
                "clientVersion": "5.3",
            },
            "threatInfo": {
                "threatTypes": [
                    "MALWARE",
                    "SOCIAL_ENGINEERING",
                    "UNWANTED_SOFTWARE",
                    "POTENTIALLY_HARMFUL_APPLICATION",
                ],
                "platformTypes": [
                    "ANY_PLATFORM",
                ],
                "threatEntryTypes": [
                    "URL",
                ],
                "threatEntries": [
                    {
                        "url": url,
                    }
                    for url in cleaned
                ],
            },
        },
    )

    matches = payload.get("matches") or []

    return _ok(
        source="Google Safe Browsing v4",
        checked_urls=cleaned,
        unsafe=bool(matches),
        matches=matches,
    )
