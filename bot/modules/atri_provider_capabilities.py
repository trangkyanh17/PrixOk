from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx


# ATRI_PROVIDER_CAPABILITIES_V231
STATE_PATH = Path("/app/atri_data/atri_provider_capabilities.json")
ENV_PATH = Path("/home/prix/secrets/prixok/free-providers.env")
VERTEX_KEY_PATH = Path("/app/vertex-service-account.json")

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
        "version": 1,
        "updated_at": 0,
        "last_audit_at": 0,
        "models": {},
        "discovered": {},
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


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}

    if not ENV_PATH.exists():
        return values

    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return values

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


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
    if status_code in {401, 403}:
        return "unknown", "auth_or_plan"
    if status_code >= 500:
        return "unknown", "provider_error"

    return "unknown", f"http_{status_code}"


async def _discover_openai_models(
    client: httpx.AsyncClient,
    *,
    provider: str,
    url: str,
    key: str,
) -> list[str]:
    if not key:
        return []

    try:
        response = await client.get(
            url,
            headers={"Authorization": "Bearer " + key},
        )

        if not response.is_success:
            return []

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
        return models
    except Exception:
        return []


async def _probe_openai_model(
    client: httpx.AsyncClient,
    *,
    url: str,
    key: str,
    model: str,
) -> dict[str, Any]:
    if not key:
        return {
            "status": "unknown",
            "reason": "key_missing",
            "http_status": None,
        }

    try:
        response = await client.post(
            url,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply OK."}],
                "max_tokens": 16,
                "temperature": 0,
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

    values = _read_env()

    keys = {
        "cerebras": values.get("CEREBRAS_API_KEY", ""),
        "groq": values.get("GROQ_API_KEY", ""),
        "openrouter": values.get("OPENROUTER_API_KEY", ""),
    }

    endpoints = {
        "cerebras": {
            "models": "https://api.cerebras.ai/v1/models",
            "chat": "https://api.cerebras.ai/v1/chat/completions",
        },
        "groq": {
            "models": "https://api.groq.com/openai/v1/models",
            "chat": "https://api.groq.com/openai/v1/chat/completions",
        },
        "openrouter": {
            "models": "https://openrouter.ai/api/v1/models",
            "chat": "https://openrouter.ai/api/v1/chat/completions",
        },
    }

    report: dict[str, Any] = {}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(25.0),
        follow_redirects=True,
    ) as client:
        for provider in ("cerebras", "groq", "openrouter"):
            if provider not in requested:
                continue

            key = keys[provider]

            await _discover_openai_models(
                client,
                provider=provider,
                url=endpoints[provider]["models"],
                key=key,
            )

            provider_report = {}

            for model, _ in CANDIDATE_CHOICES[provider]:
                result = await _probe_openai_model(
                    client,
                    url=endpoints[provider]["chat"],
                    key=key,
                    model=model,
                )

                _set_model_record(
                    provider,
                    model,
                    status=result["status"],
                    reason=result["reason"],
                    http_status=result["http_status"],
                )

                provider_report[model] = result
                await asyncio.sleep(0.7)

            report[provider] = provider_report

        if "vertex" in requested:
            token = ""
            project = ""

            try:
                token, project = await asyncio.to_thread(_vertex_access_token)
            except Exception:
                pass

            provider_report = {}

            for model, _ in CANDIDATE_CHOICES["vertex"]:
                result = await _probe_vertex_model(
                    client,
                    token=token,
                    project=project,
                    model=model,
                )

                _set_model_record(
                    "vertex",
                    model,
                    status=result["status"],
                    reason=result["reason"],
                    http_status=result["http_status"],
                )

                provider_report[model] = result
                await asyncio.sleep(0.4)

            report["vertex"] = provider_report

    _STATE["last_audit_at"] = int(time.time())
    _save_state()

    return report


def compact_report(report: dict[str, Any]) -> str:
    chunks: list[str] = []

    for provider in ("cerebras", "groq", "openrouter", "vertex"):
        items = report.get(provider)
        if not isinstance(items, dict):
            continue

        counts = {"ok": 0, "dead": 0, "unknown": 0}

        for result in items.values():
            status = str(result.get("status", "unknown"))
            counts[status if status in counts else "unknown"] += 1

        chunks.append(
            f"{provider}:ok={counts['ok']},"
            f"dead={counts['dead']},"
            f"unknown={counts['unknown']}"
        )

    return " | ".join(chunks)
