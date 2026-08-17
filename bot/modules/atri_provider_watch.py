from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from bot import LOGGER

from .atri_provider_capabilities import (
    CANDIDATE_CHOICES,
    _vertex_access_token,
    audit_age_seconds,
    audit_capabilities,
    capability_state,
)


# ATRI_PROVIDER_EXPIRY_DISCOVERY_WATCH_V141
STATE_PATH = Path(
    os.getenv(
        "ATRI_PROVIDER_WATCH_STATE",
        "/app/atri_data/atri_provider_watch.json",
    )
)
ENV_PATH = Path(
    os.getenv(
        "ATRI_PROVIDER_ENV_PATH",
        "/home/prix/secrets/prixok/free-providers.env",
    )
)
CHECK_INTERVAL_SECONDS = max(
    900,
    int(os.getenv("ATRI_PROVIDER_WATCH_INTERVAL", str(6 * 3600))),
)
DEEP_AUDIT_SECONDS = max(
    3600,
    int(os.getenv("ATRI_PROVIDER_DEEP_AUDIT_INTERVAL", str(24 * 3600))),
)
HTTP_TIMEOUT_SECONDS = 25.0
_WATCH_LOCK = asyncio.Lock()

_PROVIDER_LABELS = {
    "cerebras": "Cerebras",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "vertex": "Vertex AI",
}
_STATE_LABELS = {
    "healthy": "hoạt động bình thường",
    "missing": "thiếu API key/credential",
    "invalid": "API key đã hết hiệu lực hoặc bị thu hồi",
    "quota_exhausted": "đã hết quota/credit",
    "blocked": "bị chặn bởi quyền hoặc gói tài khoản",
    "rate_limited": "đang bị giới hạn tốc độ",
}
_ACTIONABLE_PROVIDER_STATES = {
    "missing",
    "invalid",
    "quota_exhausted",
    "blocked",
    "rate_limited",
}


def watch_interval_seconds() -> int:
    return CHECK_INTERVAL_SECONDS


def _blank_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": 0,
        "providers": {},
        "models": {},
        "seen_free_models": [],
        "last_cycle_at": 0,
        "last_deep_audit_at": 0,
    }


def _load_state() -> dict[str, Any]:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    state = _blank_state()
    if isinstance(raw, dict):
        state.update(raw)
    if not isinstance(state.get("providers"), dict):
        state["providers"] = {}
    if not isinstance(state.get("models"), dict):
        state["models"] = {}
    if not isinstance(state.get("seen_free_models"), list):
        state["seen_free_models"] = []
    return state


def _save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = int(time.time())
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".atri-provider-watch-",
        suffix=".json",
        dir=str(STATE_PATH.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, STATE_PATH)
        os.chmod(STATE_PATH, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _load_provider_env() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    for key in ("CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        value = str(os.getenv(key) or "").strip()
        if value:
            values[key] = value
    return values


def _safe_detail(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    for token in ("Bearer ", "api_key=", "apikey=", "token="):
        if token.casefold() in text.casefold():
            return "chi tiết đã được ẩn"
    return text[:limit]


def _http_state(status: int, body: Any = None) -> tuple[str, str]:
    detail = _safe_detail(body)
    if 200 <= status < 300:
        return "healthy", ""
    if status == 401:
        return "invalid", detail
    if status == 402:
        return "quota_exhausted", detail
    if status == 403:
        return "blocked", detail
    if status == 429:
        return "rate_limited", detail
    if status >= 500:
        return "transient", detail
    return "unknown", detail


async def _request_json(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    response = await client.get(url, headers=headers or {})
    try:
        body: Any = response.json()
    except Exception:
        body = response.text[:240]
    return response.status_code, body


def _error_message(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return _safe_detail(error.get("message") or error.get("code"))
        return _safe_detail(error or body.get("message"))
    return _safe_detail(body)


async def _provider_checks() -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    env = _load_provider_env()
    results: dict[str, dict[str, str]] = {}
    openrouter_models: list[dict[str, Any]] = []
    limits = httpx.Limits(max_connections=5, max_keepalive_connections=3)
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": "AtriProviderWatch/1.4.1"},
    ) as client:
        endpoints = {
            "cerebras": (
                "CEREBRAS_API_KEY",
                "https://api.cerebras.ai/v1/models",
            ),
            "groq": (
                "GROQ_API_KEY",
                "https://api.groq.com/openai/v1/models",
            ),
        }
        for provider, (env_key, url) in endpoints.items():
            key = env.get(env_key, "")
            if not key:
                results[provider] = {"state": "missing", "detail": ""}
                continue
            try:
                status, body = await _request_json(
                    client,
                    url,
                    {"Authorization": f"Bearer {key}"},
                )
                state, _ = _http_state(status, body)
                results[provider] = {
                    "state": state,
                    "detail": _error_message(body) if state != "healthy" else "",
                }
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                results[provider] = {
                    "state": "transient",
                    "detail": type(exc).__name__,
                }

        openrouter_key = env.get("OPENROUTER_API_KEY", "")
        if not openrouter_key:
            results["openrouter"] = {"state": "missing", "detail": ""}
        else:
            try:
                status, body = await _request_json(
                    client,
                    "https://api.openrouter.ai/api/v1/key",
                    {"Authorization": f"Bearer {openrouter_key}"},
                )
                state, _ = _http_state(status, body)
                if state == "healthy" and isinstance(body, dict):
                    data = body.get("data")
                    if isinstance(data, dict):
                        remaining = data.get("limit_remaining")
                        if remaining is not None:
                            try:
                                if float(remaining) <= 0:
                                    state = "quota_exhausted"
                            except (TypeError, ValueError):
                                pass
                results["openrouter"] = {
                    "state": state,
                    "detail": _error_message(body) if state != "healthy" else "",
                }
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                results["openrouter"] = {
                    "state": "transient",
                    "detail": type(exc).__name__,
                }

        try:
            _, body = await _request_json(
                client,
                "https://api.openrouter.ai/api/v1/models",
            )
            if isinstance(body, dict) and isinstance(body.get("data"), list):
                openrouter_models = [
                    item for item in body["data"] if isinstance(item, dict)
                ]
        except (httpx.TimeoutException, httpx.NetworkError):
            openrouter_models = []

    try:
        token, _ = await asyncio.to_thread(_vertex_access_token)
        results["vertex"] = {
            "state": "healthy" if token else "invalid",
            "detail": "",
        }
    except FileNotFoundError:
        results["vertex"] = {"state": "missing", "detail": ""}
    except Exception as exc:
        detail = _safe_detail(exc)
        folded = detail.casefold()
        if any(word in folded for word in ("invalid_grant", "unauthorized", "invalid credential")):
            state = "invalid"
        elif any(word in folded for word in ("permission", "forbidden", "access denied")):
            state = "blocked"
        elif any(word in folded for word in ("quota", "resource_exhausted")):
            state = "quota_exhausted"
        else:
            state = "transient"
        results["vertex"] = {"state": state, "detail": detail}
    return results, openrouter_models


def _numeric_zero(value: Any) -> bool:
    try:
        return float(str(value)) == 0.0
    except (TypeError, ValueError):
        return False


def _is_free_model(item: dict[str, Any]) -> bool:
    model_id = str(item.get("id") or "")
    if model_id.endswith(":free"):
        return True
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        return False
    return _numeric_zero(pricing.get("prompt")) and _numeric_zero(
        pricing.get("completion")
    )


def _candidate_score(item: dict[str, Any]) -> tuple[int, int, str]:
    params = {
        str(value).casefold()
        for value in item.get("supported_parameters", [])
        if isinstance(value, str)
    }
    score = 0
    for name, points in (
        ("tools", 8),
        ("tool_choice", 6),
        ("structured_outputs", 5),
        ("response_format", 4),
        ("reasoning", 4),
    ):
        if name in params:
            score += points
    architecture = item.get("architecture")
    modalities: list[Any] = []
    if isinstance(architecture, dict):
        raw_modalities = architecture.get("input_modalities")
        if isinstance(raw_modalities, list):
            modalities = raw_modalities
    if any(str(value).casefold() == "image" for value in modalities):
        score += 3
    try:
        context = int(item.get("context_length") or 0)
    except (TypeError, ValueError):
        context = 0
    score += min(context // 32768, 8)
    return score, context, str(item.get("id") or "")


def _free_candidates(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    registered = {
        model
        for model, _ in CANDIDATE_CHOICES.get("openrouter", ())
    }
    candidates = [
        item
        for item in models
        if _is_free_model(item)
        and str(item.get("id") or "") not in registered
        and str(item.get("id") or "") != "openrouter/free"
    ]
    candidates.sort(key=_candidate_score, reverse=True)
    selected: list[dict[str, Any]] = []
    for item in candidates[:5]:
        selected.append(
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or item.get("id") or ""),
                "context": int(item.get("context_length") or 0),
            }
        )
    return selected


def _provider_events(
    previous: dict[str, Any],
    current: dict[str, dict[str, str]],
) -> tuple[list[str], dict[str, Any]]:
    events: list[str] = []
    merged = dict(previous)
    now = int(time.time())
    for provider, record in current.items():
        state = str(record.get("state") or "unknown")
        if state in {"transient", "unknown"}:
            LOGGER.info(
                "ATRI_PROVIDER_WATCH_TRANSIENT provider=%s detail=%s",
                provider,
                _safe_detail(record.get("detail")),
            )
            continue
        old = merged.get(provider)
        old_state = str(old.get("state") or "unknown") if isinstance(old, dict) else "unknown"
        if state != old_state:
            label = _PROVIDER_LABELS.get(provider, provider)
            if state in _ACTIONABLE_PROVIDER_STATES:
                events.append(f"• {label}: {_STATE_LABELS[state]}.")
            elif state == "healthy" and old_state in _ACTIONABLE_PROVIDER_STATES:
                events.append(f"• {label}: đã phục hồi, hoạt động bình thường.")
        merged[provider] = {"state": state, "checked_at": now}
    return events, merged


def _model_events(
    previous: dict[str, Any],
    capability: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    events: list[str] = []
    merged = dict(previous)
    model_state = capability.get("models")
    if not isinstance(model_state, dict):
        return events, merged
    now = int(time.time())
    for provider, choices in CANDIDATE_CHOICES.items():
        bucket = model_state.get(provider)
        if not isinstance(bucket, dict):
            bucket = {}
        for model, _ in choices:
            if model in {"auto", "openrouter/free"}:
                continue
            raw = bucket.get(model)
            current = str(raw.get("status") or "unknown") if isinstance(raw, dict) else "unknown"
            if current not in {"ok", "dead"}:
                continue
            key = f"{provider}:{model}"
            old = merged.get(key)
            old_status = str(old.get("status") or "unknown") if isinstance(old, dict) else "unknown"
            if current != old_status:
                label = _PROVIDER_LABELS.get(provider, provider)
                if current == "dead":
                    events.append(f"• Model chết: {label} / {model}.")
                elif old_status == "dead":
                    events.append(f"• Model phục hồi: {label} / {model}.")
            merged[key] = {"status": current, "checked_at": now}
    return events, merged


async def _notify_owner(message: str) -> bool:
    try:
        from pyrogram import enums
        from bot.core.config_manager import Config
        from bot.core.telegram_manager import TgClient

        owner_id = int(getattr(Config, "OWNER_ID", 0) or 0)
        bot = getattr(TgClient, "bot", None)
        if owner_id <= 0 or bot is None:
            raise RuntimeError("owner or Telegram bot client unavailable")
        await bot.send_message(
            owner_id,
            message[:4000],
            parse_mode=enums.ParseMode.DISABLED,
            disable_web_page_preview=True,
        )
        return True
    except Exception as exc:
        LOGGER.warning(
            "ATRI_PROVIDER_WATCH_NOTIFY_FAILED %s:%s",
            type(exc).__name__,
            _safe_detail(exc),
        )
        return False


def _compose_message(events: list[str], candidates: list[dict[str, Any]]) -> str:
    parts = ["Atri Provider Watch"]
    if events:
        parts.append("\nCảnh báo thay đổi trạng thái:\n" + "\n".join(events))
    if candidates:
        lines = []
        for item in candidates:
            context = int(item.get("context") or 0)
            suffix = f" — context {context:,}" if context else ""
            lines.append(f"• {item['id']}{suffix}")
        parts.append(
            "\nModel OpenRouter miễn phí mới có thể thử:\n"
            + "\n".join(lines)
            + "\nChỉ là đề xuất; em chưa tự thêm vào Atri."
        )
    return "\n".join(parts)


async def run_provider_watch_cycle() -> str:
    if _WATCH_LOCK.locked():
        return "overlap_skipped=1"
    async with _WATCH_LOCK:
        state = _load_state()
        checks, catalog = await _provider_checks()
        deep_due = audit_age_seconds() >= DEEP_AUDIT_SECONDS
        if deep_due:
            try:
                await audit_capabilities(
                    ("cerebras", "groq", "openrouter", "vertex")
                )
                state["last_deep_audit_at"] = int(time.time())
            except Exception as exc:
                LOGGER.warning(
                    "ATRI_PROVIDER_WATCH_DEEP_AUDIT_FAILED %s:%s",
                    type(exc).__name__,
                    _safe_detail(exc),
                )

        provider_events, providers = _provider_events(
            state.get("providers", {}),
            checks,
        )
        model_events, models = _model_events(
            state.get("models", {}),
            capability_state(),
        )
        ranked = _free_candidates(catalog)
        seen = {
            str(value)
            for value in state.get("seen_free_models", [])
            if value
        }
        new_candidates = [
            item for item in ranked if item["id"] not in seen
        ]
        events = provider_events + model_events
        if events or new_candidates:
            sent = await _notify_owner(_compose_message(events, new_candidates))
            if not sent:
                return (
                    f"notify_retry=1 events={len(events)} "
                    f"new_free={len(new_candidates)} deep={int(deep_due)}"
                )
        state["providers"] = providers
        state["models"] = models
        state["seen_free_models"] = sorted(
            seen | {item["id"] for item in ranked}
        )[-500:]
        state["last_cycle_at"] = int(time.time())
        _save_state(state)
        digest = hashlib.sha256(
            json.dumps(checks, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return (
            f"providers={len(checks)} events={len(events)} "
            f"new_free={len(new_candidates)} deep={int(deep_due)} "
            f"state={digest}"
        )
