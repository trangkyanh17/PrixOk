from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from .atri_provider_config import provider_api_keys
from .atri_provider_request import (
    build_chat_payload,
    build_provider_headers,
)


# ATRI_PROVIDER_CAPABILITIES_V231
STATE_PATH = Path(
    os.environ.get(
        "ATRI_PROVIDER_CAPABILITIES_STATE_PATH",
        "/app/atri_data/atri_provider_capabilities.json",
    )
)
VERTEX_KEY_PATH = Path(
    os.environ.get(
        "ATRI_VERTEX_KEY_PATH",
        "/app/vertex-service-account.json",
    )
)

CANDIDATE_CHOICES: dict[str, tuple[tuple[str, str], ...]] = {
    "cerebras": (
        ("gpt-oss-120b", "OSS120B"),
        ("zai-glm-4.7", "GLM4.7-P"),
    ),
    "groq": (
        ("qwen/qwen3.6-27b", "QWEN3.6"),
        ("openai/gpt-oss-120b", "OSS120B"),
        ("openai/gpt-oss-20b", "OSS20B"),
    ),
    "openrouter": (
        ("openrouter/free", "FREE"),
        ("cohere/north-mini-code:free", "NORTH"),
        ("nvidia/nemotron-3-super-120b-a12b:free", "NEMO3S"),
        ("google/gemma-4-26b-a4b-it:free", "GEMMA4"),
        ("openai/gpt-oss-20b:free", "OSS20B"),
        ("nvidia/nemotron-3-ultra-550b-a55b:free", "NEMO3U"),
    ),
    "vertex": (
        ("auto", "AUTO"),
        ("gemini-3-flash-preview", "3FLASH"),
        ("gemini-3.1-flash-lite", "3.1LITE"),
    ),
}
_DEFAULT_THINKING = (
    "auto",
    "minimal",
    "low",
    "medium",
    "high",
)

THINKING_BY_MODEL: dict[tuple[str, str], tuple[str, ...]] = {
    ("cerebras", "gpt-oss-120b"): (
        "auto",
        "low",
        "medium",
        "high",
    ),
    ("cerebras", "zai-glm-4.7"): _DEFAULT_THINKING,
    ("groq", "qwen/qwen3.6-27b"): _DEFAULT_THINKING,
    ("groq", "openai/gpt-oss-120b"): (
        "auto",
        "low",
        "medium",
        "high",
    ),
    ("groq", "openai/gpt-oss-20b"): (
        "auto",
        "low",
        "medium",
        "high",
    ),
    ("openrouter", "openrouter/free"): ("auto",),
    ("openrouter", "cohere/north-mini-code:free"): _DEFAULT_THINKING,
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"): _DEFAULT_THINKING,
    ("openrouter", "google/gemma-4-26b-a4b-it:free"): _DEFAULT_THINKING,
    ("openrouter", "openai/gpt-oss-20b:free"): _DEFAULT_THINKING,
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"): _DEFAULT_THINKING,
    ("vertex", "auto"): _DEFAULT_THINKING,
    ("vertex", "gemini-3-flash-preview"): _DEFAULT_THINKING,
    ("vertex", "gemini-3.1-flash-lite"): _DEFAULT_THINKING,
}


# ATRI_LAGUNA_DEPRECATED_SKIP_V24
# ATRI_PROVIDER_TASK_REGISTRY_V24
# Privacy class is metadata only. It does not bypass atri_ai.py's existing
# chat-only/raw-current/no-reply privacy boundary.
MODEL_METADATA: dict[tuple[str, str], dict[str, Any]] = {
    ("cerebras", "gpt-oss-120b"): {
        "tier": "free",
        "stability": "production",
        "privacy": "public_only",
        "context": 131072,
        "capabilities": ("chat", "reasoning", "coding"),
    },
    ("cerebras", "zai-glm-4.7"): {
        "tier": "free",
        "stability": "preview",
        "privacy": "public_only",
        "capabilities": ("chat", "reasoning"),
    },
    ("groq", "qwen/qwen3.6-27b"): {
        "tier": "account",
        "stability": "preview",
        "privacy": "public_only",
        "context": 131072,
        "max_output": 16384,
        "capabilities": (
            "chat",
            "reasoning",
            "tools",
            "json",
            "vision",
            "coding",
            "agent",
        ),
        "thinking_adapter": "groq_qwen36",
    },
    ("groq", "openai/gpt-oss-120b"): {
        "tier": "free",
        "stability": "production",
        "privacy": "public_only",
        "capabilities": ("chat", "reasoning", "coding"),
    },
    ("groq", "openai/gpt-oss-20b"): {
        "tier": "free",
        "stability": "production",
        "privacy": "public_only",
        "capabilities": ("chat", "reasoning", "coding"),
    },
    ("openrouter", "openrouter/free"): {
        "tier": "free",
        "stability": "dynamic",
        "privacy": "public_only",
        "capabilities": ("chat",),
        "thinking_adapter": "dynamic_auto_only",
    },
    ("openrouter", "cohere/north-mini-code:free"): {
        "tier": "free",
        "stability": "free_endpoint",
        "privacy": "public_only",
        "context": 262144,
        "max_output": 65536,
        "capabilities": (
            "chat",
            "reasoning",
            "tools",
            "json",
            "coding",
            "agent",
        ),
    },
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"): {
        "tier": "free",
        "stability": "free_endpoint",
        "privacy": "public_only_strict",
        "context": 262144,
        "capabilities": (
            "chat",
            "reasoning",
            "tools",
            "research",
            "long_context",
            "agent",
        ),
    },
    ("openrouter", "google/gemma-4-26b-a4b-it:free"): {
        "tier": "free",
        "stability": "free_endpoint",
        "privacy": "public_only",
        "context": 262144,
        "max_output": 32768,
        "capabilities": (
            "chat",
            "reasoning",
            "tools",
            "json",
            "vision",
        ),
    },
    ("openrouter", "openai/gpt-oss-20b:free"): {
        "tier": "free",
        "stability": "free_endpoint",
        "privacy": "public_only",
        "capabilities": ("chat", "reasoning", "coding"),
    },
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"): {
        "tier": "free",
        "stability": "free_endpoint",
        "privacy": "public_only",
        "capabilities": ("chat", "reasoning", "research", "agent"),
    },
}

TASK_MODEL_ORDER: dict[str, tuple[tuple[str, str], ...]] = {
    "chat": (
        ("groq", "qwen/qwen3.6-27b"),
        ("cerebras", "gpt-oss-120b"),
        ("openrouter", "google/gemma-4-26b-a4b-it:free"),
        ("openrouter", "openrouter/free"),
    ),
    "coding": (
        ("groq", "qwen/qwen3.6-27b"),
        ("cerebras", "gpt-oss-120b"),
        ("openrouter", "cohere/north-mini-code:free"),
    ),
    "coding_agentic": (
        ("openrouter", "cohere/north-mini-code:free"),
        ("groq", "qwen/qwen3.6-27b"),
        ("cerebras", "gpt-oss-120b"),
    ),
    "tools": (
        ("vertex", "auto"),
    ),
    "research": (
        ("groq", "qwen/qwen3.6-27b"),
        ("openrouter", "google/gemma-4-26b-a4b-it:free"),
        ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    ),
    "research_long": (
        ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
        ("openrouter", "google/gemma-4-26b-a4b-it:free"),
        ("groq", "qwen/qwen3.6-27b"),
    ),
}


def model_metadata(provider: str, model: str) -> dict[str, Any]:
    return dict(
        MODEL_METADATA.get(
            (str(provider).casefold(), str(model)),
            {},
        )
    )


def task_model_candidates(
    task: str,
    *,
    require_public_safe: bool = True,
) -> tuple[tuple[str, str], ...]:
    task = str(task or "chat").casefold()
    requested = TASK_MODEL_ORDER.get(task, TASK_MODEL_ORDER["chat"])
    visible: list[tuple[str, str]] = []

    for provider, model in requested:
        if model_status(provider, model) == "dead":
            continue

        metadata = MODEL_METADATA.get((provider, model), {})
        privacy = str(metadata.get("privacy", "public_only"))

        if require_public_safe and not privacy.startswith("public_only"):
            continue

        visible.append((provider, model))

    return tuple(visible)


def _blank_state() -> dict[str, Any]:
    return {
        "version": 2,
        "updated_at": 0,
        "last_audit_at": 0,
        "models": {},
        "discovered": {},
        "alert_snapshot": {},
    }


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    state = _blank_state()
    state.update(data)

    if not isinstance(state.get("models"), dict):
        state["models"] = {}
    if not isinstance(state.get("discovered"), dict):
        state["discovered"] = {}
    if not isinstance(state.get("alert_snapshot"), dict):
        state["alert_snapshot"] = {}

    return state


_STATE = _load_state()


def _save_state() -> None:
    _STATE["updated_at"] = int(time.time())
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        _STATE,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=".atri-provider-capabilities-",
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


def capability_state() -> dict[str, Any]:
    return json.loads(json.dumps(_STATE))


def audit_age_seconds() -> float:
    checked = int(_STATE.get("last_audit_at") or 0)
    if checked <= 0:
        return float("inf")
    return max(0.0, time.time() - checked)


def _provider_bucket(provider: str) -> dict[str, Any]:
    return _STATE.setdefault("models", {}).setdefault(
        str(provider).casefold(),
        {},
    )


def model_record(provider: str, model: str) -> dict[str, Any]:
    record = (
        _STATE.get("models", {})
        .get(str(provider).casefold(), {})
        .get(str(model), {})
    )
    return dict(record) if isinstance(record, dict) else {}


def model_status(provider: str, model: str) -> str:
    if str(provider).casefold() == "vertex" and str(model) == "auto":
        return "ok"

    status = str(
        model_record(provider, model).get("status", "unknown")
    ).casefold()

    return status if status in {"ok", "dead", "unknown"} else "unknown"


def status_icon(provider: str, model: str) -> str:
    return {
        "ok": "✅",
        "dead": "⛔",
        "unknown": "❔",
    }[model_status(provider, model)]


def _set_model_record(
    provider: str,
    model: str,
    *,
    status: str,
    reason: str = "",
    http_status: int | None = None,
) -> None:
    _provider_bucket(provider)[str(model)] = {
        "status": status,
        "reason": str(reason or "")[:240],
        "http_status": http_status,
        "checked_at": int(time.time()),
    }


def mark_model_unavailable(
    provider: str,
    model: str,
    reason: str = "",
) -> None:
    _set_model_record(
        provider,
        model,
        status="dead",
        reason=reason,
    )
    _save_state()


def mark_model_available(
    provider: str,
    model: str,
    reason: str = "",
) -> None:
    _set_model_record(
        provider,
        model,
        status="ok",
        reason=reason,
    )
    _save_state()


def is_terminal_model_error(
    status_code: int | None,
    error_text: str,
) -> bool:
    if status_code in {404, 410}:
        return True

    if status_code != 400:
        return False

    text = str(error_text or "").casefold()

    terminal_hints = (
        "not found",
        "does not exist",
        "doesn't exist",
        "unknown model",
        "invalid model",
        "decommissioned",
        "no longer available",
    )

    return "model" in text and any(
        hint in text
        for hint in terminal_hints
    )


def filter_model_choices(
    provider: str,
    choices: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    visible = tuple(
        (model, label)
        for model, label in choices
        if model_status(provider, model) != "dead"
    )

    if visible:
        return visible

    if str(provider).casefold() == "vertex":
        return (("auto", "AUTO"),)

    return tuple()


def heal_model(
    provider: str,
    selected: str,
    fallback: str,
    choices: tuple[tuple[str, str], ...],
) -> str:
    models = [
        model
        for model, _ in filter_model_choices(provider, choices)
    ]

    if selected in models:
        return selected
    if fallback in models:
        return fallback
    if models:
        return models[0]

    return fallback


def provider_has_live_model(
    provider: str,
    choices: tuple[tuple[str, str], ...],
) -> bool:
    return bool(filter_model_choices(provider, choices))


def supported_thinking_levels(
    provider: str,
    model: str,
) -> tuple[str, ...]:
    return THINKING_BY_MODEL.get(
        (str(provider).casefold(), str(model)),
        _DEFAULT_THINKING,
    )


def _classify_probe(
    status_code: int | None,
    text: str,
) -> tuple[str, str]:
    if status_code is not None and 200 <= status_code < 300:
        return "ok", "live_probe"

    if is_terminal_model_error(status_code, text):
        return "dead", "model_not_available"

    if status_code is None:
        return "unknown", "network_error"
    if status_code == 429:
        return "unknown", "rate_limited"
    if status_code == 401:
        return "unknown", "key_invalid"
    if status_code == 403:
        return "unknown", "auth_or_plan"
    if status_code >= 500:
        return "unknown", "provider_error"

    return "unknown", f"http_{status_code}"


def _classify_key_check(
    status_code: int | None,
) -> tuple[str, str]:
    if status_code is not None and 200 <= status_code < 300:
        return "ok", "key_valid"
    if status_code is None:
        return "unknown", "network_error"
    if status_code == 401:
        return "invalid", "key_invalid"
    if status_code == 403:
        return "denied", "auth_or_plan"
    if status_code == 429:
        return "unknown", "rate_limited"
    if status_code >= 500:
        return "unknown", "provider_error"
    return "unknown", f"http_{status_code}"


async def _check_provider_key(
    client: httpx.AsyncClient,
    *,
    url: str,
    key: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    if not key:
        return {
            "status": "missing",
            "reason": "key_missing",
            "http_status": None,
        }

    try:
        async with semaphore:
            response = await client.get(
                url,
                headers={"Authorization": "Bearer " + key},
            )

        status, reason = _classify_key_check(response.status_code)
        return {
            "status": status,
            "reason": reason,
            "http_status": response.status_code,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "reason": type(exc).__name__,
            "http_status": None,
        }


async def _discover_openai_models(
    client: httpx.AsyncClient,
    *,
    provider: str,
    url: str,
    key: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    try:
        async with semaphore:
            response = await client.get(
                url,
                headers={"Authorization": "Bearer " + key},
            )

        if not response.is_success:
            return {
                "status": "unknown",
                "reason": f"http_{response.status_code}",
                "models": [],
            }

        data = response.json()
        items = data.get("data", []) if isinstance(data, dict) else []

        models = sorted(
            {
                str(item.get("id"))
                for item in items
                if isinstance(item, dict) and item.get("id")
            }
        )

        _STATE.setdefault("discovered", {})[provider] = models
        return {
            "status": "ok",
            "reason": "models_discovered",
            "models": models,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "reason": type(exc).__name__,
            "models": [],
        }


async def _probe_openai_model(
    client: httpx.AsyncClient,
    *,
    url: str,
    provider: str,
    key: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    if not key:
        return {
            "status": "unknown",
            "reason": "key_missing",
            "http_status": None,
        }

    try:
        payload = build_chat_payload(
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": "Reply OK."}],
            thinking_level="medium",
            max_tokens=16,
            temperature=0,
        )
        async with semaphore:
            response = await client.post(
                url,
                headers=build_provider_headers(provider, key),
                json=payload,
            )

        status, reason = _classify_probe(
            response.status_code,
            response.text[:700],
        )

        return {
            "status": status,
            "reason": reason,
            "http_status": response.status_code,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "reason": type(exc).__name__,
            "http_status": None,
        }


def _vertex_access_token() -> tuple[str, str]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    info = json.loads(
        VERTEX_KEY_PATH.read_text(encoding="utf-8")
    )

    project = str(info.get("project_id") or "").strip()

    credentials = service_account.Credentials.from_service_account_file(
        str(VERTEX_KEY_PATH),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )

    credentials.refresh(Request())
    return str(credentials.token), project


async def _probe_vertex_model(
    client: httpx.AsyncClient,
    *,
    token: str,
    project: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    if model == "auto":
        return {
            "status": "ok",
            "reason": "runtime_default",
            "http_status": 200,
        }

    if not token or not project:
        return {
            "status": "unknown",
            "reason": "vertex_credentials_missing",
            "http_status": None,
        }

    location = (
        os.environ.get("GOOGLE_CLOUD_LOCATION")
        or os.environ.get("VERTEX_LOCATION")
        or "global"
    )

    url = (
        "https://aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/"
        "publishers/google/models/"
        f"{model}:generateContent"
    )

    try:
        async with semaphore:
            response = await client.post(
                url,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": "Reply OK."}],
                        }
                    ],
                    "generationConfig": {
                        "maxOutputTokens": 16,
                        "temperature": 0,
                    },
                },
            )

        status, reason = _classify_probe(
            response.status_code,
            response.text[:700],
        )

        return {
            "status": status,
            "reason": reason,
            "http_status": response.status_code,
        }
    except Exception as exc:
        return {
            "status": "unknown",
            "reason": type(exc).__name__,
            "http_status": None,
        }


async def audit_capabilities(
    providers: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    requested = tuple(
        str(x).casefold()
        for x in (
            providers
            or ("cerebras", "groq", "openrouter", "vertex")
        )
    )

    keys = provider_api_keys()

    endpoints = {
        "cerebras": {
            "key": "https://api.cerebras.ai/v1/models",
            "models": "https://api.cerebras.ai/v1/models",
            "chat": "https://api.cerebras.ai/v1/chat/completions",
        },
        "groq": {
            "key": "https://api.groq.com/openai/v1/models",
            "models": "https://api.groq.com/openai/v1/models",
            "chat": "https://api.groq.com/openai/v1/chat/completions",
        },
        "openrouter": {
            "key": "https://openrouter.ai/api/v1/key",
            "models": "https://openrouter.ai/api/v1/models",
            "chat": "https://openrouter.ai/api/v1/chat/completions",
        },
    }

    report: dict[str, Any] = {}

    semaphore = asyncio.Semaphore(4)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=4.0),
        follow_redirects=True,
    ) as client:
        async def audit_openai_provider(provider: str) -> None:
            key = keys.get(provider, "")
            key_result = await _check_provider_key(
                client,
                url=endpoints[provider]["key"],
                key=key,
                semaphore=semaphore,
            )
            provider_report: dict[str, Any] = {
                "key": key_result,
                "models": {},
            }

            if key_result["status"] != "ok":
                for model, _ in CANDIDATE_CHOICES[provider]:
                    result = {
                        "status": "unknown",
                        "reason": key_result["reason"],
                        "http_status": key_result["http_status"],
                    }
                    _set_model_record(
                        provider,
                        model,
                        status=result["status"],
                        reason=result["reason"],
                        http_status=result["http_status"],
                    )
                    provider_report["models"][model] = result
                report[provider] = provider_report
                return

            discovery = await _discover_openai_models(
                client,
                provider=provider,
                url=endpoints[provider]["models"],
                key=key,
                semaphore=semaphore,
            )
            provider_report["discovery"] = {
                "status": discovery["status"],
                "reason": discovery["reason"],
                "count": len(discovery["models"]),
            }
            discovered = set(discovery["models"])

            async def probe(model: str) -> tuple[str, dict[str, Any]]:
                if discovery["status"] == "ok" and model not in discovered:
                    return model, {
                        "status": "dead",
                        "reason": "model_not_listed",
                        "http_status": 404,
                    }

                result = await _probe_openai_model(
                    client,
                    url=endpoints[provider]["chat"],
                    provider=provider,
                    key=key,
                    model=model,
                    semaphore=semaphore,
                )
                return model, result

            model_results = await asyncio.gather(
                *(
                    probe(model)
                    for model, _ in CANDIDATE_CHOICES[provider]
                )
            )

            for model, result in model_results:
                _set_model_record(
                    provider,
                    model,
                    status=result["status"],
                    reason=result["reason"],
                    http_status=result["http_status"],
                )
                provider_report["models"][model] = result

            report[provider] = provider_report

        async def audit_vertex() -> None:
            token = ""
            project = ""

            try:
                token, project = await asyncio.to_thread(_vertex_access_token)
            except Exception:
                pass

            async def probe_vertex(
                model: str,
            ) -> tuple[str, dict[str, Any]]:
                result = await _probe_vertex_model(
                    client,
                    token=token,
                    project=project,
                    model=model,
                    semaphore=semaphore,
                )
                return model, result

            model_results = await asyncio.gather(
                *(
                    probe_vertex(model)
                    for model, _ in CANDIDATE_CHOICES["vertex"]
                )
            )
            provider_report: dict[str, Any] = {
                "key": {
                    "status": "ok" if token and project else "missing",
                    "reason": (
                        "service_account_valid"
                        if token and project
                        else "vertex_credentials_missing"
                    ),
                    "http_status": 200 if token and project else None,
                },
                "models": {},
            }

            for model, result in model_results:
                _set_model_record(
                    "vertex",
                    model,
                    status=result["status"],
                    reason=result["reason"],
                    http_status=result["http_status"],
                )

                provider_report["models"][model] = result

            report["vertex"] = provider_report

        tasks = [
            audit_openai_provider(provider)
            for provider in ("cerebras", "groq", "openrouter")
            if provider in requested
        ]
        if "vertex" in requested:
            tasks.append(audit_vertex())
        await asyncio.gather(*tasks)

    _STATE["last_audit_at"] = int(time.time())
    _save_state()

    return report


def compact_report(report: dict[str, Any]) -> str:
    chunks: list[str] = []

    for provider in ("cerebras", "groq", "openrouter", "vertex"):
        provider_report = report.get(provider)
        if not isinstance(provider_report, dict):
            continue

        items = provider_report.get("models", {})

        counts = {"ok": 0, "dead": 0, "unknown": 0}

        for result in items.values():
            status = str(result.get("status", "unknown"))
            counts[status if status in counts else "unknown"] += 1

        key_status = str(
            provider_report.get("key", {}).get("status", "unknown")
        )
        chunks.append(
            f"{provider}:key={key_status},ok={counts['ok']},"
            f"dead={counts['dead']},"
            f"unknown={counts['unknown']}"
        )

    return " | ".join(chunks)


def audit_report_text(report: dict[str, Any]) -> str:
    labels = {
        "cerebras": "Cerebras",
        "groq": "Groq",
        "openrouter": "OpenRouter",
        "vertex": "Vertex",
    }
    icons = {"ok": "✅", "dead": "❌", "unknown": "⚠️"}
    key_icons = {
        "ok": "✅",
        "missing": "❔",
        "invalid": "❌",
        "denied": "⛔",
        "unknown": "⚠️",
    }
    lines = ["Kết quả audit API/model:"]

    for provider in ("cerebras", "groq", "openrouter", "vertex"):
        provider_report = report.get(provider)
        if not isinstance(provider_report, dict):
            continue

        key_result = provider_report.get("key", {})
        key_status = str(key_result.get("status", "unknown"))
        key_reason = str(key_result.get("reason", "unknown"))
        lines.append(
            f"\n{labels[provider]}: "
            f"{key_icons.get(key_status, '⚠️')} key={key_reason}"
        )

        models = provider_report.get("models", {})
        for model, short_label in CANDIDATE_CHOICES[provider]:
            result = models.get(model, {})
            status = str(result.get("status", "unknown"))
            reason = str(result.get("reason", "not_checked"))
            http_status = result.get("http_status")
            http_text = f" HTTP {http_status}" if http_status else ""
            lines.append(
                f"• {icons.get(status, '⚠️')} {short_label}: "
                f"{reason}{http_text}"
            )

    return "\n".join(lines)[:3900]


def _key_health(status: str) -> str:
    normalized = str(status or "unknown").casefold()
    if normalized == "ok":
        return "ok"
    if normalized in {"missing", "invalid", "denied"}:
        return "bad"
    return "transient"


def build_audit_alert_snapshot(
    report: dict[str, Any],
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}

    for provider in ("cerebras", "groq", "openrouter", "vertex"):
        provider_report = report.get(provider)
        if not isinstance(provider_report, dict):
            continue

        key_result = provider_report.get("key", {})
        if not isinstance(key_result, dict):
            key_result = {}

        key = {
            "status": str(key_result.get("status", "unknown")),
            "reason": str(key_result.get("reason", "unknown")),
            "http_status": key_result.get("http_status"),
        }
        models: dict[str, Any] = {}

        for model, result in provider_report.get("models", {}).items():
            if not isinstance(result, dict):
                continue
            models[str(model)] = {
                "status": str(result.get("status", "unknown")),
                "reason": str(result.get("reason", "unknown")),
                "http_status": result.get("http_status"),
            }

        statuses = [
            str(item.get("status", "unknown"))
            for item in models.values()
        ]
        key_health = _key_health(key["status"])

        if key_health == "bad":
            provider_status = "key_bad"
        elif any(status == "ok" for status in statuses):
            provider_status = "healthy"
        elif statuses and all(status == "dead" for status in statuses):
            provider_status = "all_dead"
        else:
            provider_status = "unavailable"

        snapshot[provider] = {
            "key": key,
            "models": models,
            "provider_status": provider_status,
        }

    return snapshot


def current_audit_alert_snapshot() -> dict[str, Any]:
    stored = _STATE.get("alert_snapshot", {})
    if isinstance(stored, dict) and stored:
        return json.loads(json.dumps(stored))

    legacy_report: dict[str, Any] = {}
    for provider, choices in CANDIDATE_CHOICES.items():
        models = {
            model: model_record(provider, model)
            for model, _ in choices
        }
        legacy_report[provider] = {
            "key": {
                "status": "unknown",
                "reason": "baseline_not_audited",
                "http_status": None,
            },
            "models": models,
        }

    return build_audit_alert_snapshot(legacy_report)


def audit_alert_events(
    report: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = build_audit_alert_snapshot(report)
    previous = (
        previous_snapshot
        if isinstance(previous_snapshot, dict)
        else current_audit_alert_snapshot()
    )
    events: list[dict[str, Any]] = []

    for provider in ("cerebras", "groq", "openrouter", "vertex"):
        before = previous.get(provider)
        after = current.get(provider)
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue

        before_key = before.get("key", {})
        after_key = after.get("key", {})
        before_key_health = _key_health(before_key.get("status", "unknown"))
        after_key_health = _key_health(after_key.get("status", "unknown"))

        if after_key_health == "bad" and before_key_health != "bad":
            events.append({
                "kind": "key_failed",
                "provider": provider,
                **after_key,
            })
        elif before_key_health == "bad" and after_key_health == "ok":
            events.append({
                "kind": "key_recovered",
                "provider": provider,
                **after_key,
            })

        before_models = before.get("models", {})
        after_models = after.get("models", {})
        for model, after_model in after_models.items():
            before_model = before_models.get(model)
            if not isinstance(before_model, dict):
                continue

            before_status = str(before_model.get("status", "unknown"))
            after_status = str(after_model.get("status", "unknown"))

            if after_status == "dead" and before_status != "dead":
                events.append({
                    "kind": "model_dead",
                    "provider": provider,
                    "model": model,
                    **after_model,
                })
            elif before_status == "dead" and after_status == "ok":
                events.append({
                    "kind": "model_recovered",
                    "provider": provider,
                    "model": model,
                    **after_model,
                })

        before_provider = str(before.get("provider_status", "unavailable"))
        after_provider = str(after.get("provider_status", "unavailable"))

        if after_provider == "all_dead" and before_provider != "all_dead":
            events.append({
                "kind": "provider_all_dead",
                "provider": provider,
            })
        elif (
            after_provider == "unavailable"
            and before_provider == "healthy"
        ):
            events.append({
                "kind": "provider_unavailable",
                "provider": provider,
            })
        elif (
            after_provider == "healthy"
            and before_provider in {"all_dead", "unavailable"}
        ):
            events.append({
                "kind": "provider_recovered",
                "provider": provider,
            })

    return events


def commit_audit_alert_snapshot(report: dict[str, Any]) -> None:
    _STATE["alert_snapshot"] = build_audit_alert_snapshot(report)
    _save_state()


def audit_alert_text(events: list[dict[str, Any]]) -> str:
    provider_labels = {
        "cerebras": "Cerebras",
        "groq": "Groq",
        "openrouter": "OpenRouter",
        "vertex": "Vertex",
    }
    model_labels = {
        provider: dict(choices)
        for provider, choices in CANDIDATE_CHOICES.items()
    }
    lines = ["🔔 Thay đổi trạng thái API/model Atri:"]

    for event in events:
        kind = str(event.get("kind", ""))
        provider = str(event.get("provider", ""))
        provider_label = provider_labels.get(provider, provider)
        reason = str(event.get("reason", "unknown"))
        http_status = event.get("http_status")
        http_text = f" (HTTP {http_status})" if http_status else ""
        model = str(event.get("model", ""))
        model_label = model_labels.get(provider, {}).get(model, model)

        if kind == "key_failed":
            lines.append(
                f"• ❌ {provider_label} key lỗi: {reason}{http_text}"
            )
        elif kind == "key_recovered":
            lines.append(f"• ✅ {provider_label} key đã phục hồi")
        elif kind == "model_dead":
            lines.append(
                f"• ⛔ {provider_label}/{model_label} chết: "
                f"{reason}{http_text}"
            )
        elif kind == "model_recovered":
            lines.append(
                f"• ✅ {provider_label}/{model_label} đã phục hồi"
            )
        elif kind == "provider_all_dead":
            lines.append(
                f"• 🚨 {provider_label}: toàn bộ model đã chết"
            )
        elif kind == "provider_unavailable":
            lines.append(
                f"• ⚠️ {provider_label}: tạm thời không khả dụng"
            )
        elif kind == "provider_recovered":
            lines.append(f"• ✅ {provider_label}: đã hoạt động lại")

    return "\n".join(lines)[:3900]
