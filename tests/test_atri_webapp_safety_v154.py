from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest


def _addr(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))


def test_webapp_network_guard_blocks_literal_private_and_local_names():
    from bot.modules import atri_webapp_safety_guard as guard

    for url in (
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://localhost/",
        "http://service.internal/",
    ):
        with pytest.raises(ValueError):
            guard._validate_resolved_network_url(url)


def test_webapp_network_guard_blocks_hostname_resolving_private(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.modules import atri_webapp_safety_guard as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("127.0.0.1", 80)],
    )
    with pytest.raises(ValueError, match="DNS_NON_PUBLIC_BLOCKED"):
        guard._validate_resolved_network_url("http://public-looking.example/")


def test_webapp_network_guard_accepts_public_dns_and_safe_non_network_scheme(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.modules import atri_webapp_safety_guard as guard

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )
    assert (
        guard._validate_resolved_network_url("https://example.com/path")
        == "https://example.com/path"
    )
    assert guard._validate_resolved_network_url("data:text/plain,ok") == "data:text/plain,ok"


class _FakePage:
    def __init__(self):
        self.route_handler = None

    async def route(self, pattern, handler):
        assert pattern == "**/*"
        self.route_handler = handler


class _FakeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class _FakeRoute:
    def __init__(self, url: str):
        self.request = SimpleNamespace(url=url)
        self.aborted = False
        self.continued = False

    async def abort(self, reason=None):
        assert reason == "blockedbyclient"
        self.aborted = True

    async def continue_(self):
        self.continued = True


def test_page_route_rechecks_redirect_and_subresource_targets(
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.modules import atri_webapp_safety_guard as guard

    page = _FakePage()
    proxy = guard._RoutedContextProxy(_FakeContext(page))
    assert asyncio.run(proxy.new_page()) is page
    assert page.route_handler is not None

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("10.20.30.40", 80)],
    )
    blocked = _FakeRoute("http://redirect-target.example/private")
    asyncio.run(page.route_handler(blocked))
    assert blocked.aborted is True
    assert blocked.continued is False

    monkeypatch.setattr(
        guard.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [_addr("93.184.216.34")],
    )
    allowed = _FakeRoute("https://cdn.example.com/app.js")
    asyncio.run(page.route_handler(allowed))
    assert allowed.continued is True
    assert allowed.aborted is False


def test_webapp_runtime_keeps_loopback_only_cdp_control_channel():
    from pathlib import Path

    source = Path("bot/modules/atri_webapp_runtime.py").read_text(encoding="utf-8")
    assert 'endpoint.startswith("http://127.0.0.1:")' in source
    assert "connect_over_cdp" in source
