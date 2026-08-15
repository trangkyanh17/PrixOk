#!/usr/bin/env python3
"""Isolated V155 smoke probe for the live production source tree."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import socket
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx


def load_guard(live_root: Path) -> ModuleType:
    path = live_root / "bot/helper/ext_utils/network_utils.py"
    spec = importlib.util.spec_from_file_location("atri_v155_network_probe_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load live V155 network guard")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _addr(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))


class _PeerStream:
    def __init__(self, ip: str, port: int = 443):
        self.peer = (ip, port)

    def get_extra_info(self, name: str):
        return self.peer if name == "server_addr" else None


def _response(status: int, *, peer: str = "93.184.216.34", **kwargs: Any):
    extensions = dict(kwargs.pop("extensions", {}))
    extensions["network_stream"] = _PeerStream(peer)
    return httpx.Response(status, extensions=extensions, **kwargs)


def _expect_block(fn, label: str, blocked_type) -> None:
    try:
        fn()
    except blocked_type:
        return
    raise RuntimeError(f"expected network block did not occur: {label}")


async def run_probe(live_root: Path, *, real_public_read: bool) -> dict[str, Any]:
    guard = load_guard(live_root)
    results: dict[str, Any] = {}

    for url in (
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://localhost/",
        "http://service.internal/",
        "http://router.home.arpa/",
        "file:///etc/passwd",
    ):
        _expect_block(
            lambda value=url: guard.validate_public_http_url(value),
            url,
            guard.NetworkTargetBlocked,
        )
    results["literal_private_block"] = True

    original_getaddrinfo = guard.socket.getaddrinfo
    try:
        guard.socket.getaddrinfo = lambda *args, **kwargs: [_addr("10.20.30.40", 80)]
        _expect_block(
            lambda: guard.validate_public_http_url("http://public-looking.example/"),
            "private DNS",
            guard.NetworkTargetBlocked,
        )
        results["private_dns_block"] = True

        guard.socket.getaddrinfo = lambda *args, **kwargs: [
            _addr("93.184.216.34"),
            _addr("127.0.0.1"),
        ]
        _expect_block(
            lambda: guard.validate_public_http_url("https://mixed.example/"),
            "mixed DNS",
            guard.NetworkTargetBlocked,
        )
        results["mixed_dns_block"] = True

        guard.socket.getaddrinfo = lambda *args, **kwargs: [_addr("93.184.216.34")]

        calls: list[str] = []

        async def redirect_private(request: httpx.Request):
            calls.append(str(request.url))
            return _response(
                302,
                headers={"Location": "http://127.0.0.1/private"},
                request=request,
            )

        try:
            await guard.probe_public_http_url(
                "https://example.com/start",
                transport=httpx.MockTransport(redirect_private),
            )
        except guard.NetworkTargetBlocked:
            pass
        else:
            raise RuntimeError("private redirect was not blocked")
        if len(calls) != 1:
            raise RuntimeError("private redirect issued an unsafe second request")
        results["redirect_block"] = True

        async def rebound_peer(request: httpx.Request):
            return _response(200, peer="127.0.0.1", request=request)

        try:
            await guard.probe_public_http_url(
                "https://example.com/start",
                transport=httpx.MockTransport(rebound_peer),
            )
        except guard.NetworkTargetBlocked as exc:
            if "PEER_NON_PUBLIC" not in str(exc):
                raise RuntimeError(f"wrong rebinding block reason: {exc}") from exc
        else:
            raise RuntimeError("connected private peer was not blocked")
        results["peer_rebinding_block"] = True

        async def public_redirect(request: httpx.Request):
            if str(request.url).endswith("/start"):
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

        public_probe = await guard.probe_public_http_url(
            "https://example.com/start",
            transport=httpx.MockTransport(public_redirect),
        )
        if public_probe.final_url != "https://cdn.example.com/file.bin":
            raise RuntimeError("public redirect final URL mismatch")
        if public_probe.content_type != "application/octet-stream":
            raise RuntimeError("public redirect content type mismatch")
        results["public_redirect"] = True

        async def oversized(request: httpx.Request):
            return _response(
                200,
                headers={"Content-Length": "1000"},
                content=b"x",
                request=request,
            )

        try:
            await guard.fetch_public_http_text(
                "https://example.com/feed",
                max_bytes=16,
                transport=httpx.MockTransport(oversized),
            )
        except guard.NetworkResponseTooLarge:
            pass
        else:
            raise RuntimeError("RSS body size cap did not fire")
        results["rss_size_cap"] = True
    finally:
        guard.socket.getaddrinfo = original_getaddrinfo

    main_text = (live_root / "bot/__main__.py").read_text(encoding="utf-8")
    bot_utils_text = (live_root / "bot/helper/ext_utils/bot_utils.py").read_text(
        encoding="utf-8"
    )
    sab_text = (live_root / "sabnzbdapi/requests.py").read_text(encoding="utf-8")
    runtime_text = (live_root / "bot/modules/atri_network_egress_guard.py").read_text(
        encoding="utf-8"
    )

    install_pos = main_text.index("install_atri_network_egress_guard()")
    handlers_pos = main_text.index("add_handlers()", install_pos)
    if install_pos >= handlers_pos:
        raise RuntimeError("V155 guard installs after handlers")
    if "get_content_type_with_final_url" not in bot_utils_text:
        raise RuntimeError("live content-type safe probe missing")
    if "VERIFY_CERTIFICATE: bool = True" not in sab_text:
        raise RuntimeError("live SAB TLS default not hardened")
    if "follow_redirects=False" not in sab_text:
        raise RuntimeError("live SAB redirect default not hardened")
    for marker in (
        "rss.AsyncClient = _SafeRssAsyncClient",
        "ytdlp._mdisk = _safe_mdisk",
        "MyJdApi._session = guarded_session",
        "TaskConfig.before_start = guarded_before_start",
        "mirror_leech.Mirror.new_event = guarded_new_event",
    ):
        if marker not in runtime_text:
            raise RuntimeError(f"runtime guard marker missing: {marker}")
    results["live_source_contract"] = True

    if real_public_read:
        body, final_url = await guard.fetch_public_http_text(
            "https://api.github.com/repos/trangkyanh17/PrixOk",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PrixOk-V155-Probe"},
            timeout=30.0,
            max_bytes=512 * 1024,
        )
        payload = json.loads(body)
        if payload.get("full_name") != "trangkyanh17/PrixOk":
            raise RuntimeError("real public HTTPS probe returned unexpected repository")
        if not final_url.startswith("https://api.github.com/"):
            raise RuntimeError("real public HTTPS probe left trusted public endpoint")
        results["real_public_https"] = True

    return results


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--live-root", default="/app")
    result.add_argument("--no-real-public-read", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        results = asyncio.run(
            run_probe(
                Path(args.live_root),
                real_public_read=not args.no_real_public_read,
            )
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
