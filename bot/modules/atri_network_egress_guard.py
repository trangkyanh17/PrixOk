from __future__ import annotations

# ATRI_LEGACY_NETWORK_EGRESS_GUARD_V155
# Runtime compatibility guard for legacy modules whose request construction is
# intertwined with large upstream files. MyJD is patched before main() starts;
# the remaining request paths are patched before Telegram handlers register.

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import httpx

from bot.helper.ext_utils.network_utils import (
    NetworkTargetBlocked,
    fetch_public_http_text,
    probe_public_http_url,
    validate_public_http_url,
)


_LOGGER = logging.getLogger("bot")
_EARLY_INSTALLED = False
_INSTALLED = False


class _SafeRssAsyncClient:
    """Compatibility shim for the three legacy rss.AsyncClient GET call sites."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._headers = kwargs.get("headers")
        self._timeout = kwargs.get("timeout", 60.0)

    async def __aenter__(self) -> "_SafeRssAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        request_headers = kwargs.get("headers") or self._headers
        text, final_url = await fetch_public_http_text(
            url,
            headers=request_headers,
            timeout=self._timeout,
        )
        return SimpleNamespace(text=text, url=final_url)


async def _safe_mdisk(link: str, name: str):
    key = str(link).split("/")[-1]
    endpoint = (
        "https://diskuploader.entertainvideo.com/v1/file/cdnurl?param=" + key
    )
    try:
        text, _ = await fetch_public_http_text(endpoint, timeout=60.0, max_bytes=1_048_576)
        payload = json.loads(text)
        source = payload.get("source")
        filename = payload.get("filename")
        if source:
            await asyncio.to_thread(validate_public_http_url, str(source))
            link = str(source)
        if not name and filename:
            name = str(filename)
    except NetworkTargetBlocked:
        raise
    except Exception as exc:
        _LOGGER.warning("V155 mdisk resolver failed safely: %s", exc)
    return name, link


def _install_task_url_guard() -> None:
    from bot.helper.common import TaskConfig

    if getattr(TaskConfig.before_start, "_atri_v155_guarded", False):
        return

    original_before_start = TaskConfig.before_start

    async def guarded_before_start(self):
        result = await original_before_start(self)
        link = getattr(self, "link", "")
        if isinstance(link, str) and link.lower().startswith(("http://", "https://")):
            # Resolve the complete redirect chain under the V155 DNS + connected
            # peer policy, then hand downstream downloaders only the final public
            # URL rather than letting them re-follow the unchecked original URL.
            probe = await probe_public_http_url(link)
            self.link = probe.final_url
        return result

    guarded_before_start._atri_v155_guarded = True
    TaskConfig.before_start = guarded_before_start


def _install_mirror_exception_guard() -> None:
    from bot.modules import mirror_leech

    if getattr(mirror_leech.Mirror.new_event, "_atri_v155_guarded", False):
        return

    original_new_event = mirror_leech.Mirror.new_event

    async def guarded_new_event(self):
        try:
            return await original_new_event(self)
        except NetworkTargetBlocked as exc:
            _LOGGER.warning("V155 mirror network target blocked: %s", exc)
            try:
                from bot.helper.telegram_helper.message_utils import send_message

                await send_message(
                    self.message,
                    "Blocked unsafe network target (localhost/private/internal address or redirect).",
                )
            finally:
                try:
                    await self.remove_from_same_dir()
                except Exception:
                    pass
            return None

    guarded_new_event._atri_v155_guarded = True
    mirror_leech.Mirror.new_event = guarded_new_event


def _install_rss_guard() -> None:
    from bot.modules import rss

    rss.AsyncClient = _SafeRssAsyncClient
    rss.ATRI_NETWORK_EGRESS_GUARD_V155 = True


def _install_ytdlp_guard() -> None:
    from bot.modules import ytdlp

    ytdlp._mdisk = _safe_mdisk
    ytdlp.ATRI_NETWORK_EGRESS_GUARD_V155 = True


def _install_myjd_guard() -> None:
    from myjd.myjdapi import MyJdApi

    if getattr(MyJdApi._session, "_atri_v155_guarded", False):
        return

    def guarded_session(self):
        if self._http_session is not None:
            return self._http_session

        # MyJD is a fixed loopback control plane in this project. TLS verify is
        # irrelevant for its http:// endpoint; redirects and environment proxy
        # inheritance are disabled so localhost cannot bounce elsewhere.
        transport = httpx.AsyncHTTPTransport(retries=10)
        self._http_session = httpx.AsyncClient(
            base_url=self._MyJdApi__api_url,
            transport=transport,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=httpx.Timeout(connect=60, read=60, write=60, pool=None),
            follow_redirects=False,
            trust_env=False,
        )
        return self._http_session

    guarded_session._atri_v155_guarded = True
    MyJdApi._session = guarded_session


def install_atri_early_network_guard() -> None:
    """Patch startup network clients before main() can boot JDownloader."""
    global _EARLY_INSTALLED
    if _EARLY_INSTALLED:
        return
    _install_myjd_guard()
    _EARLY_INSTALLED = True
    _LOGGER.info("ATRI_EARLY_NETWORK_EGRESS_GUARD_V155_INSTALLED")


def install_atri_network_egress_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    install_atri_early_network_guard()
    _install_task_url_guard()
    _install_mirror_exception_guard()
    _install_rss_guard()
    _install_ytdlp_guard()

    _INSTALLED = True
    _LOGGER.info("ATRI_LEGACY_NETWORK_EGRESS_GUARD_V155_INSTALLED")
