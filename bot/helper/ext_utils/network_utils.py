from __future__ import annotations

# ATRI_PUBLIC_HTTP_EGRESS_GUARD_V155
# Public/user-controlled HTTP(S) fetches must not cross into loopback, private,
# link-local or otherwise non-global address space. DNS is checked before each
# hop and the connected peer is checked after the socket is established, which
# closes the usual pre-resolve DNS-rebinding gap for these httpx requests.

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

import httpx


_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})
_DEFAULT_MAX_REDIRECTS = 5
_DEFAULT_MAX_TEXT_BYTES = 4 * 1024 * 1024


class NetworkTargetBlocked(ValueError):
    """Raised when a requested HTTP target violates the public-egress policy."""


class NetworkResponseTooLarge(ValueError):
    """Raised when a bounded text fetch exceeds its configured byte limit."""


@dataclass(frozen=True)
class PublicHttpProbe:
    final_url: str
    content_type: str | None
    status_code: int


def _blocked(reason: str) -> NetworkTargetBlocked:
    return NetworkTargetBlocked(f"NETWORK_EGRESS_BLOCKED:{reason}")


def _is_non_public_ip(value: str) -> bool:
    raw = str(value or "").split("%", 1)[0]
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise _blocked("PEER_IP_INVALID") from exc
    return not address.is_global


def _normalized_host(parsed) -> str:
    host = str(parsed.hostname or "").strip().casefold().rstrip(".")
    if not host:
        raise _blocked("HOST_MISSING")
    return host


def validate_public_http_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        raise _blocked("URL_EMPTY")

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise _blocked("URL_INVALID") from exc

    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise _blocked("SCHEME_NOT_ALLOWED")
    if parsed.username is not None or parsed.password is not None:
        raise _blocked("URL_CREDENTIALS_NOT_ALLOWED")

    host = _normalized_host(parsed)
    if host in _LOCAL_NAMES or host.endswith(_LOCAL_SUFFIXES):
        raise _blocked("LOCAL_NAME")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise _blocked("PORT_INVALID") from exc

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if _is_non_public_ip(str(literal)):
            raise _blocked("NON_PUBLIC_IP")
        return raw

    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _blocked("DNS_FAILED") from exc

    addresses = {
        str(item[4][0])
        for item in resolved
        if item and len(item) >= 5 and item[4]
    }
    if not addresses:
        raise _blocked("DNS_EMPTY")
    if any(_is_non_public_ip(address) for address in addresses):
        raise _blocked("DNS_NON_PUBLIC")
    return raw


def _peer_ip(response: httpx.Response) -> str:
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        raise _blocked("PEER_UNAVAILABLE")
    peer = stream.get_extra_info("server_addr")
    if isinstance(peer, (tuple, list)) and peer:
        value = peer[0]
    elif isinstance(peer, str):
        value = peer
    else:
        raise _blocked("PEER_UNAVAILABLE")
    return str(value).split("%", 1)[0]


def _assert_public_peer(response: httpx.Response) -> None:
    peer = _peer_ip(response)
    if _is_non_public_ip(peer):
        raise _blocked("PEER_NON_PUBLIC")


def _redirect_target(response: httpx.Response, current_url: str) -> str | None:
    if response.status_code not in _REDIRECT_CODES:
        return None
    location = str(response.headers.get("location") or "").strip()
    if not location:
        return None
    target = urljoin(current_url, location)
    # Validate immediately, before a second request can be created.
    validate_public_http_url(target)
    return target


async def _validate_hop(url: str) -> str:
    return await asyncio.to_thread(validate_public_http_url, url)


def _client(
    *,
    timeout: float | httpx.Timeout,
    transport: httpx.AsyncBaseTransport | None,
) -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": timeout,
        "trust_env": False,
    }
    if transport is not None:
        kwargs["transport"] = transport
    # Do not pass verify=False. The default transport verifies HTTPS
    # certificates against the configured trust store.
    return httpx.AsyncClient(**kwargs)


async def probe_public_http_url(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float | httpx.Timeout = 60.0,
    max_redirects: int = _DEFAULT_MAX_REDIRECTS,
    transport: httpx.AsyncBaseTransport | None = None,
) -> PublicHttpProbe:
    if max_redirects < 0:
        raise ValueError("max_redirects must be >= 0")

    current = str(url or "").strip()
    await _validate_hop(current)

    async with _client(timeout=timeout, transport=transport) as client:
        for redirects in range(max_redirects + 1):
            await _validate_hop(current)
            async with client.stream("GET", current, headers=headers) as response:
                _assert_public_peer(response)
                target = _redirect_target(response, current)
                if target is None:
                    return PublicHttpProbe(
                        final_url=str(response.url),
                        content_type=response.headers.get("Content-Type"),
                        status_code=int(response.status_code),
                    )
            if redirects >= max_redirects:
                raise _blocked("REDIRECT_LIMIT")
            current = target

    raise _blocked("REQUEST_INCOMPLETE")


async def fetch_public_http_text(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float | httpx.Timeout = 60.0,
    max_redirects: int = _DEFAULT_MAX_REDIRECTS,
    max_bytes: int = _DEFAULT_MAX_TEXT_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, str]:
    if max_redirects < 0:
        raise ValueError("max_redirects must be >= 0")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be > 0")

    current = str(url or "").strip()
    await _validate_hop(current)

    async with _client(timeout=timeout, transport=transport) as client:
        for redirects in range(max_redirects + 1):
            await _validate_hop(current)
            async with client.stream("GET", current, headers=headers) as response:
                _assert_public_peer(response)
                target = _redirect_target(response, current)
                if target is None:
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                raise NetworkResponseTooLarge(
                                    "NETWORK_RESPONSE_TOO_LARGE:content-length"
                                )
                        except ValueError:
                            pass

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise NetworkResponseTooLarge(
                                "NETWORK_RESPONSE_TOO_LARGE:stream"
                            )
                        chunks.append(chunk)

                    payload = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
                    return payload.decode(encoding, errors="replace"), str(response.url)

            if redirects >= max_redirects:
                raise _blocked("REDIRECT_LIMIT")
            current = target

    raise _blocked("REQUEST_INCOMPLETE")
