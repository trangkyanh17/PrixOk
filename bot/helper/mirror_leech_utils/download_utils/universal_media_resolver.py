from __future__ import annotations

import asyncio
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from bot import LOGGER


# ATRI_UNIVERSAL_MEDIA_RESOLVER_V163
_TIKWM_ENDPOINT = "https://www.tikwm.com/api/"
_TIKWM_ORIGIN = "https://www.tikwm.com/"
_TIKWM_MIN_INTERVAL = 1.10
_TIKWM_FAIL_LIMIT = 3
_TIKWM_COOLDOWN = 120.0
_CACHE_TTL = 600.0

_tikwm_lock = asyncio.Lock()
_tikwm_last_call = 0.0
_tikwm_failures = 0
_tikwm_blocked_until = 0.0

_cache: dict[str, tuple[float, "MediaResolution"]] = {}

# ATRI_SOCIAL_PLATFORM_EXPANSION_V16423
_SOCIAL_DOMAINS = {
    "tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "t.co": "x",
    "reddit.com": "reddit",
    "redd.it": "reddit",
    "threads.com": "threads",
    "threads.net": "threads",
}


@dataclass(frozen=True)
class MediaResolution:
    original_url: str
    resolved_url: str
    platform: str
    backend: str
    title: str = ""
    direct: bool = False


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def detect_platform(url: str) -> str:
    host = _host(url)

    for domain, platform in _SOCIAL_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return platform

    return "generic"


def is_supported_social_url(url: str) -> bool:
    return detect_platform(url) in {
        "tiktok",
        "facebook",
        "instagram",
        "x",
        "reddit",
        "threads",
    }


def extract_bare_social_url(text: str | None) -> str | None:
    value = str(text or "").strip()

    if not value or any(ch.isspace() for ch in value):
        return None

    if not re.match(r"^https?://", value, flags=re.I):
        return None

    return value if is_supported_social_url(value) else None


def _cache_get(url: str) -> MediaResolution | None:
    row = _cache.get(url)
    if not row:
        return None

    expires, result = row

    if time.monotonic() >= expires:
        _cache.pop(url, None)
        return None

    return result


def _cache_put(url: str, result: MediaResolution) -> None:
    if result.direct:
        _cache[url] = (time.monotonic() + _CACHE_TTL, result)


async def _follow_social_redirect(url: str) -> str:
    host = _host(url)

    if not (
        host == "fb.watch"
        or host.endswith(".fb.watch")
        or host == "t.co"
        or host.endswith(".t.co")
        or host == "redd.it"
        or host.endswith(".redd.it")
    ):
        return url

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(12.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/138.0 Mobile Safari/537.36"
                )
            },
        ) as client:
            response = await client.get(url)
            final = str(response.url)

        if final.startswith(("http://", "https://")):
            LOGGER.info(
                "ATRI_MEDIA_REDIRECT platform=%s from=%s to=%s",
                detect_platform(url),
                url,
                final,
            )
            return final

    except Exception as exc:
        LOGGER.warning(
            "ATRI_MEDIA_REDIRECT_FAIL url=%s error=%s",
            url,
            exc,
        )

    return url


async def _resolve_tikwm(url: str) -> MediaResolution | None:
    global _tikwm_last_call
    global _tikwm_failures
    global _tikwm_blocked_until

    now = time.monotonic()

    if now < _tikwm_blocked_until:
        LOGGER.info(
            "ATRI_MEDIA_BACKEND_SKIP backend=tikwm reason=circuit_open remain=%.1f",
            _tikwm_blocked_until - now,
        )
        return None

    async with _tikwm_lock:
        now = time.monotonic()
        wait_for = _TIKWM_MIN_INTERVAL - (now - _tikwm_last_call)

        if wait_for > 0:
            await asyncio.sleep(wait_for)

        _tikwm_last_call = time.monotonic()

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(20.0),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/138.0 Mobile Safari/537.36"
                    ),
                    "Accept": "application/json,text/plain,*/*",
                },
            ) as client:
                response = await client.post(
                    _TIKWM_ENDPOINT,
                    data={"url": url, "hd": "1"},
                )
                response.raise_for_status()
                payload = response.json()

            if payload.get("code") != 0:
                raise RuntimeError(
                    str(payload.get("msg") or "TikWM returned non-zero code")
                )

            data = payload.get("data") or {}
            media = (
                data.get("hdplay")
                or data.get("play")
                or data.get("wmplay")
            )

            if not isinstance(media, str) or not media.strip():
                raise RuntimeError("TikWM returned no playable video URL")

            resolved = urljoin(_TIKWM_ORIGIN, media.strip())
            title = str(data.get("title") or "").strip()

            _tikwm_failures = 0

            LOGGER.info(
                "ATRI_MEDIA_RESOLVED platform=tiktok backend=tikwm direct=1",
            )

            return MediaResolution(
                original_url=url,
                resolved_url=resolved,
                platform="tiktok",
                backend="tikwm",
                title=title,
                direct=True,
            )

        except Exception as exc:
            _tikwm_failures += 1

            LOGGER.warning(
                "ATRI_MEDIA_BACKEND_FAIL platform=tiktok backend=tikwm failures=%d error=%s",
                _tikwm_failures,
                exc,
            )

            if _tikwm_failures >= _TIKWM_FAIL_LIMIT:
                _tikwm_blocked_until = (
                    time.monotonic() + _TIKWM_COOLDOWN
                )
                LOGGER.warning(
                    "ATRI_MEDIA_CIRCUIT_OPEN backend=tikwm cooldown=%s",
                    int(_TIKWM_COOLDOWN),
                )

            return None


async def _resolve_gallery_dl(
    url: str,
    platform: str,
) -> MediaResolution | None:
    cmd = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--no-input",
        "--http-timeout",
        "20",
        "--retries",
        "1",
        "--range",
        "1",
        "-G",
    ]

    cookie = Path("/app/cookies.txt")
    if cookie.is_file():
        cmd += ["-C", str(cookie)]

    cmd.append(url)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd="/app",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=30.0,
        )

        output = stdout.decode("utf-8", "replace")
        error = stderr.decode("utf-8", "replace").strip()

        if process.returncode != 0:
            raise RuntimeError(
                error[-500:]
                or f"gallery-dl exited {process.returncode}"
            )

        candidates = [
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith(("http://", "https://"))
        ]

        if not candidates:
            raise RuntimeError("gallery-dl returned no resolved URL")

        resolved = candidates[0]

        LOGGER.info(
            "ATRI_MEDIA_RESOLVED platform=%s backend=gallery-dl direct=1 candidates=%d",
            platform,
            len(candidates),
        )

        return MediaResolution(
            original_url=url,
            resolved_url=resolved,
            platform=platform,
            backend="gallery-dl",
            direct=True,
        )

    except Exception as exc:
        LOGGER.warning(
            "ATRI_MEDIA_BACKEND_FAIL platform=%s backend=gallery-dl error=%s",
            platform,
            exc,
        )
        return None



def _decode_embedded_url(value: str) -> str:
    value = str(value or "").strip()
    return (
        value
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\u002F", "/")
        .replace("\\u003A", ":")
    )


async def _resolve_threads_web(url: str) -> MediaResolution | None:
    import html
    import re

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(20.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 14) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0 Mobile Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.text

            patterns = (
                r'<meta[^>]+property=["\']og:video(?::url)?["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::url)?["\']',
                r'"video_url"\s*:\s*"([^"]+)"',
                r'"url"\s*:\s*"([^"]+\.mp4(?:\?[^"]*)?)"',
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            )

            candidates = []
            for pattern in patterns:
                for raw in re.findall(pattern, body, flags=re.I):
                    candidate = html.unescape(
                        _decode_embedded_url(raw)
                    )
                    if candidate.startswith(
                        ("http://", "https://")
                    ) and candidate not in candidates:
                        candidates.append(candidate)

            for candidate in candidates[:12]:
                try:
                    async with client.stream(
                        "GET",
                        candidate,
                        headers={"Range": "bytes=0-1023"},
                    ) as media_response:
                        ctype = str(
                            media_response.headers.get(
                                "content-type"
                            )
                            or ""
                        ).lower()

                        if media_response.status_code not in (200, 206):
                            continue

                        if not ctype.startswith(("video/", "image/")):
                            continue

                        async for chunk in media_response.aiter_bytes():
                            if not chunk:
                                continue

                            LOGGER.info(
                                "ATRI_MEDIA_RESOLVED "
                                "platform=threads "
                                "backend=threads-web "
                                "direct=1 type=%s",
                                ctype or "unknown",
                            )

                            return MediaResolution(
                                original_url=url,
                                resolved_url=candidate,
                                platform="threads",
                                backend="threads-web",
                                direct=True,
                            )
                except Exception:
                    continue

    except Exception as exc:
        LOGGER.warning(
            "ATRI_MEDIA_BACKEND_FAIL "
            "platform=threads backend=threads-web "
            "error=%s",
            exc,
        )

    return None


async def resolve_media(url: str) -> MediaResolution:
    url = str(url or "").strip()

    if not url.startswith(("http://", "https://")):
        return MediaResolution(
            original_url=url,
            resolved_url=url,
            platform="generic",
            backend="yt-dlp",
            direct=False,
        )

    cached = _cache_get(url)
    if cached is not None:
        LOGGER.info(
            "ATRI_MEDIA_CACHE_HIT platform=%s backend=%s",
            cached.platform,
            cached.backend,
        )
        return cached

    normalized = await _follow_social_redirect(url)
    platform = detect_platform(normalized)

    if platform == "tiktok":
        result = await _resolve_tikwm(normalized)

        if result is None:
            result = await _resolve_gallery_dl(
                normalized,
                platform,
            )

        if result is not None:
            if normalized != url:
                result = MediaResolution(
                    original_url=url,
                    resolved_url=result.resolved_url,
                    platform=result.platform,
                    backend=result.backend,
                    title=result.title,
                    direct=result.direct,
                )

            _cache_put(url, result)
            return result

    elif platform in {"facebook", "instagram", "x", "reddit"}:
        result = await _resolve_gallery_dl(
            normalized,
            platform,
        )

        if result is not None:
            if normalized != url:
                result = MediaResolution(
                    original_url=url,
                    resolved_url=result.resolved_url,
                    platform=result.platform,
                    backend=result.backend,
                    title=result.title,
                    direct=result.direct,
                )

            _cache_put(url, result)
            return result

    elif platform == "threads":
        result = await _resolve_threads_web(normalized)

        if result is not None:
            if normalized != url:
                result = MediaResolution(
                    original_url=url,
                    resolved_url=result.resolved_url,
                    platform=result.platform,
                    backend=result.backend,
                    title=result.title,
                    direct=result.direct,
                )

            _cache_put(url, result)
            return result

    LOGGER.info(
        "ATRI_MEDIA_FALLBACK platform=%s backend=yt-dlp",
        platform,
    )

    return MediaResolution(
        original_url=url,
        resolved_url=normalized,
        platform=platform,
        backend="yt-dlp",
        direct=False,
    )
