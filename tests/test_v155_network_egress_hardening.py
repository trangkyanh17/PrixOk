from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest


TARGET_TLS_FILES = (
    "bot/modules/rss.py",
    "bot/helper/ext_utils/bot_utils.py",
    "bot/modules/ytdlp.py",
    "myjd/myjdapi.py",
)


def _addr(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))


class _PeerStream:
    def __init__(self, ip: str, port: int = 443):
        self._peer = (ip, port)

    def get_extra_info(self, name: str):
        if name == "server_addr":
            return self._peer
        return None


def _response(status: int = 200, *, peer: str = "93.184.216.34", **kwargs):
    extensions = dict(kwargs.pop("extensions", {}))
    extensions["network_stream"] = _PeerStream(peer)
    return httpx.Response(status, extensions=extensions, **kwargs)


def test_baseline_has_no_literal_tls_verification_bypass():
    for filename in TARGET_TLS_FILES:
        source = Path(filename).read_text(encoding="utf-8")
        assert "verify=False" not in source, filename


def test_sabnzbd_tls_verification_is_secure_by_default():
    source = Path("sabnzbdapi/requests.py").read_text(encoding="utf-8")
    assert "VERIFY_CERTIFICATE: bool = True" in source


def test_rss_and_content_type_use_public_network_guard():
    rss = Path("bot/modules/rss.py").read_text(encoding="utf-8")
    bot_utils = Path("bot/helper/ext_utils/bot_utils.py").read_text(encoding="utf-8")
    mirror = Path("bot/modules/mirror_leech.py").read_text(encoding="utf-8")

    assert "fetch_public_http_text" in rss
    assert "AsyncClient(" not in rss
    assert "probe_public_http_url" in bot_utils
    assert "NetworkTargetBlocked" in mirror
    assert "get_content_type_with_final_url" in mirror


def test_public_url_guard_blocks_literal_private_local_credentials_and_schemes(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.helper.ext_utils import network_utils as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )

    blocked = (
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://localhost/",
        "http://service.internal/",
        "http://user:pass@example.com/",
        "file:///etc/passwd",
        "ftp://example.com/file",
    )
    for url in blocked:
        with pytest.raises(guard.NetworkTargetBlocked):
            guard.validate_public_http_url(url)


def test_public_url_guard_blocks_hostname_resolving_private(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.helper.ext_utils import network_utils as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("10.20.30.40", 80)],
    )
    with pytest.raises(guard.NetworkTargetBlocked, match="DNS_NON_PUBLIC"):
        guard.validate_public_http_url("http://public-looking.example/feed")


def test_public_url_guard_accepts_public_dns(monkeypatch: pytest.MonkeyPatch):
    from bot.helper.ext_utils import network_utils as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )
    assert (
        guard.validate_public_http_url("https://example.com/feed")
        == "https://example.com/feed"
    )


def test_probe_revalidates_redirect_before_second_request(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.helper.ext_utils import network_utils as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )
    calls: list[str] = []

    async def handler(request: httpx.Request):
        calls.append(str(request.url))
        return _response(
            302,
            headers={"Location": "http://127.0.0.1/private"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    with pytest.raises(guard.NetworkTargetBlocked):
        asyncio.run(
            guard.probe_public_http_url(
                "https://example.com/start",
                transport=transport,
            )
        )
    assert len(calls) == 1


def test_probe_blocks_dns_rebinding_by_checking_connected_peer(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.helper.ext_utils import network_utils as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )

    async def handler(request: httpx.Request):
        return _response(200, peer="127.0.0.1", request=request)

    with pytest.raises(guard.NetworkTargetBlocked, match="PEER_NON_PUBLIC"):
        asyncio.run(
            guard.probe_public_http_url(
                "https://example.com/start",
                transport=httpx.MockTransport(handler),
            )
        )


def test_probe_returns_final_public_url_and_content_type(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.helper.ext_utils import network_utils as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )
    calls: list[str] = []

    async def handler(request: httpx.Request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return _response(
                302,
                headers={"Location": "https://cdn.example.com/file.bin"},
                request=request,
            )
        return _response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            request=request,
        )

    probe = asyncio.run(
        guard.probe_public_http_url(
            "https://example.com/start",
            transport=httpx.MockTransport(handler),
        )
    )
    assert probe.final_url == "https://cdn.example.com/file.bin"
    assert probe.content_type == "application/octet-stream"
    assert probe.status_code == 200
    assert len(calls) == 2


def test_rss_text_fetch_has_size_limit(monkeypatch: pytest.MonkeyPatch):
    from bot.helper.ext_utils import network_utils as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )

    async def handler(request: httpx.Request):
        return _response(
            200,
            headers={"Content-Type": "application/rss+xml; charset=utf-8"},
            content=b"x" * 32,
            request=request,
        )

    with pytest.raises(guard.NetworkResponseTooLarge):
        asyncio.run(
            guard.fetch_public_http_text(
                "https://example.com/feed",
                max_bytes=16,
                transport=httpx.MockTransport(handler),
            )
        )
