from __future__ import annotations

# ATRI_WEBAPP_NETWORK_GUARD_V154
# The webapp skill controls a real browser connected to a production-local CDP
# endpoint. URL validation therefore has to defend the host network, not merely
# reject literal 127.0.0.1 strings. This guard resolves HTTP(S) hostnames before
# navigation and installs a Playwright page route so redirects/subresources are
# checked again before the browser fetches them.

import asyncio
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse


_LOGGER = logging.getLogger("bot")
_INSTALLED = False
_SAFE_NON_NETWORK_SCHEMES = frozenset({"about", "blob", "data"})


def _is_non_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(str(value).split("%", 1)[0])
    return not address.is_global


def _validate_resolved_network_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    scheme = parsed.scheme.casefold()
    if scheme in _SAFE_NON_NETWORK_SCHEMES:
        return url
    if scheme not in {"http", "https"}:
        raise ValueError("WEBAPP_NETWORK_SCHEME_NOT_ALLOWED")
    if not parsed.hostname:
        raise ValueError("WEBAPP_NETWORK_HOST_MISSING")
    if parsed.username or parsed.password:
        raise ValueError("WEBAPP_NETWORK_CREDENTIALS_NOT_ALLOWED")

    host = parsed.hostname.strip().casefold()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("WEBAPP_NETWORK_LOCALHOST_BLOCKED")
    if host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("WEBAPP_NETWORK_LOCAL_NAME_BLOCKED")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_non_public_ip(str(literal)):
            raise ValueError("WEBAPP_NETWORK_NON_PUBLIC_IP_BLOCKED")
        return url

    port = parsed.port or (443 if scheme == "https" else 80)
    try:
        resolved = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("WEBAPP_NETWORK_DNS_FAILED") from exc

    addresses = {
        str(item[4][0])
        for item in resolved
        if item and len(item) >= 5 and item[4]
    }
    if not addresses:
        raise ValueError("WEBAPP_NETWORK_DNS_EMPTY")
    if any(_is_non_public_ip(address) for address in addresses):
        raise ValueError("WEBAPP_NETWORK_DNS_NON_PUBLIC_BLOCKED")
    return url


class _RoutedContextProxy:
    def __init__(self, context: Any) -> None:
        self._context = context

    async def new_page(self) -> Any:
        page = await self._context.new_page()

        async def guard_route(route: Any) -> None:
            request = getattr(route, "request", None)
            url = str(getattr(request, "url", "") or "")
            try:
                await asyncio.to_thread(
                    _validate_resolved_network_url,
                    url,
                )
            except Exception as exc:
                parsed = urlparse(url)
                _LOGGER.warning(
                    "ATRI_WEBAPP_NETWORK_BLOCK_V154 scheme=%s host=%s reason=%s",
                    parsed.scheme[:16],
                    str(parsed.hostname or "")[:200],
                    type(exc).__name__ + ":" + str(exc)[:160],
                )
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        await page.route("**/*", guard_route)
        return page

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


def install_atri_webapp_safety_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from bot.modules import atri_webapp_runtime as runtime

    if getattr(runtime, "_ATRI_V154_NETWORK_GUARD", False):
        _INSTALLED = True
        return

    original_validate = runtime._validate_url
    original_run_one = runtime._run_one

    def guarded_validate_url(url: str) -> str:
        validated = original_validate(url)
        return _validate_resolved_network_url(validated)

    async def guarded_run_one(context: Any, url: str, index: int):
        # Validate once immediately before creating the page and again for every
        # routed browser request. The route also covers HTTP redirects and
        # ordinary subresources initiated by the page.
        await asyncio.to_thread(_validate_resolved_network_url, url)
        return await original_run_one(
            _RoutedContextProxy(context),
            url,
            index,
        )

    runtime._validate_url = guarded_validate_url
    runtime._run_one = guarded_run_one
    runtime._ATRI_V154_NETWORK_GUARD = True
    _INSTALLED = True
    _LOGGER.info("ATRI_WEBAPP_NETWORK_GUARD_V154_INSTALLED")
