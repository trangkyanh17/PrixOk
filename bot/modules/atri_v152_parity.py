from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from bot import LOGGER, bot_loop

_SCHEMA_VERSION = 1
_QUEUE_MAX = 256
_HTTP_TIMEOUT_SECONDS = 0.75
_ENABLE_FILE = Path("/root/.local/state/atri-v152-parity/enabled")
_READY_FILE = Path("/root/.local/state/atri-v152-parity/ready.json")

_queue: asyncio.Queue[dict[str, Any]] | None = None
_worker_task: asyncio.Task | None = None
_last_error_log = 0.0
_drop_count = 0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def parity_enabled() -> bool:
    return _env_bool("ATRI_V152_PARITY", False) or _ENABLE_FILE.is_file()


def tool_profile_for_mode(mode: str) -> str:
    return {
        "chat": "none",
        "web": "google_search",
        "tools": "tool_functions",
        "code": "code_plugins",
    }.get(str(mode or "chat").casefold(), "none")


def _parity_url() -> str:
    explicit = os.getenv("ATRI_V152_PARITY_URL", "").strip()
    if explicit:
        url = explicit
    else:
        addr = os.getenv("ATRI_TELEGRAM_SHADOW_ADDR", "127.0.0.1:18750").strip()
        url = f"http://{addr}/v1/atri/parity"

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise RuntimeError("V152 parity endpoint must be loopback HTTP")
    host = parsed.hostname.casefold()
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise RuntimeError("V152 parity endpoint must be loopback HTTP")
        except ValueError as exc:
            raise RuntimeError("V152 parity endpoint must be loopback HTTP") from exc
    return url


def _shadow_secret() -> str:
    return os.getenv("ATRI_TELEGRAM_SHADOW_SECRET", "").strip()


def _ready_file() -> Path:
    explicit = os.getenv("ATRI_V152_PARITY_READY_FILE", "").strip()
    return Path(explicit) if explicit else _READY_FILE


def _publish_ready_marker() -> bool:
    path = _ready_file()
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    payload = {
        "version": _SCHEMA_VERSION,
        "mode": "decision-shadow",
        "side_effects": False,
        "pid": os.getpid(),
        "ready_at": int(time.time()),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return True
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        LOGGER.warning("ATRI_V152_PARITY_READY_WRITE_ERROR error=%s", exc)
        return False


def _clear_ready_marker() -> None:
    try:
        _ready_file().unlink(missing_ok=True)
    except OSError as exc:
        LOGGER.warning("ATRI_V152_PARITY_READY_CLEAR_ERROR error=%s", exc)


def initialize_v152_parity() -> bool:
    """Start the localhost-only decision publisher. It never executes AI/tools."""
    global _queue, _worker_task
    if not parity_enabled():
        _clear_ready_marker()
        return False
    if _worker_task is not None:
        return True

    try:
        _parity_url()
    except Exception as exc:
        LOGGER.warning("ATRI_V152_PARITY_ENDPOINT_REJECTED error=%s", exc)
        _clear_ready_marker()
        return False

    _queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _worker_task = bot_loop.create_task(
        _parity_worker(),
        name="atri-v152-parity-shadow",
    )
    ready = _publish_ready_marker()
    LOGGER.info(
        "ATRI_V152_PARITY_ENABLED queue=%s mode=decision-shadow side_effects=0 ready=%s",
        _QUEUE_MAX,
        int(ready),
    )
    return True


def _post_sync(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        _parity_url(),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    secret = _shadow_secret()
    if secret:
        request.add_header("X-Atri-Shadow-Secret", secret)
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
        if response.status != 202:
            raise RuntimeError(f"V152 parity ingress returned HTTP {response.status}")


def _log_transport_error(exc: BaseException) -> None:
    global _last_error_log
    now = time.monotonic()
    if now - _last_error_log < 60:
        return
    _last_error_log = now
    LOGGER.warning("ATRI_V152_PARITY_TRANSPORT_ERROR error=%s", exc)


async def _parity_worker() -> None:
    assert _queue is not None
    while True:
        payload = await _queue.get()
        try:
            await asyncio.to_thread(_post_sync, payload)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            _log_transport_error(exc)
        except Exception as exc:  # parity must never break production
            _log_transport_error(exc)
        finally:
            _queue.task_done()


def _enqueue(payload: dict[str, Any]) -> None:
    global _drop_count
    if _queue is None and not initialize_v152_parity():
        return
    assert _queue is not None
    try:
        _queue.put_nowait(payload)
    except asyncio.QueueFull:
        _drop_count += 1
        if _drop_count == 1 or _drop_count % 100 == 0:
            LOGGER.warning(
                "ATRI_V152_PARITY_QUEUE_FULL dropped=%s max=%s",
                _drop_count,
                _QUEUE_MAX,
            )


def publish_route_decision(
    *,
    route_text: str,
    attachment_route: str,
    actual_mode: str,
    force_github_mcp: bool,
) -> None:
    _enqueue(
        {
            "version": _SCHEMA_VERSION,
            "stage": "route",
            "route_text": str(route_text or "")[:6000],
            "attachment_route": str(attachment_route or "").casefold(),
            "actual_mode": str(actual_mode or "").casefold(),
            "force_github_mcp": bool(force_github_mcp),
        }
    )


def publish_vertex_plan(
    *,
    mode: str,
    runtime_model: str,
    base_model: str,
    resolved_model: str,
    thinking_auto: bool,
    thinking_levels: dict[str, str],
    base_thinking: str,
    provider_model: str,
    provider_thinking: str,
    resolved_thinking: str,
    tool_profile: str,
) -> None:
    levels = {
        str(key).casefold(): str(value).casefold()
        for key, value in dict(thinking_levels or {}).items()
        if str(key).casefold() in {"chat", "web", "tools", "code"}
    }
    _enqueue(
        {
            "version": _SCHEMA_VERSION,
            "stage": "vertex_plan",
            "mode": str(mode or "chat").casefold(),
            "runtime_model": str(runtime_model or ""),
            "base_model": str(base_model or ""),
            "resolved_model": str(resolved_model or ""),
            "thinking_auto": bool(thinking_auto),
            "thinking_levels": levels,
            "base_thinking": str(base_thinking or "").casefold(),
            "provider_model": str(provider_model or ""),
            "provider_thinking": str(provider_thinking or "").casefold(),
            "resolved_thinking": str(resolved_thinking or "").casefold(),
            "tool_profile": str(tool_profile or "none").casefold(),
        }
    )


def publish_tool_observation(*, mode: str, tool_profile: str, tool_name: str) -> None:
    _enqueue(
        {
            "version": _SCHEMA_VERSION,
            "stage": "tool",
            "mode": str(mode or "chat").casefold(),
            "tool_profile": str(tool_profile or "none").casefold(),
            "tool_name": str(tool_name or "")[:160],
        }
    )
