from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from bot import LOGGER
from .atri_provider_capabilities import (
    is_terminal_model_error,
    mark_model_unavailable,
)
from .atri_provider_control import (
    naturalize_system_instruction,
    resolve_provider_mode,
    resolve_provider_model,
    resolve_provider_thinking,
)


# ATRI_FREE_POOL_V1
# Safe scope:
# - Only plain-text CHAT is offloaded to the free pool.
# - WEB / TOOLS / CODE stay on Vertex.
# - Multimodal requests stay on Vertex.
# - Missing keys or provider failures always fall back to Vertex.

ENV_PATH = Path(
    os.getenv(
        "ATRI_FREE_PROVIDERS_ENV",
        "/home/prix/secrets/prixok/free-providers.env",
    )
)

DEFAULT_CHAIN = (
    "novita_ling",
    "cerebras_gptoss",
    "groq_gptoss",
    "novita_macaron",
    "openrouter_free",
)

_PROVIDER_DEFS: dict[str, dict[str, str]] = {
    "novita_ling": {
        "provider": "novita",
        "key_name": "NOVITA_API_KEY",
        "url": "https://api.novita.ai/openai/v1/chat/completions",
        "model": "inclusionai/ling-3.0-flash",
    },
    "novita_macaron": {
        "provider": "novita",
        "key_name": "NOVITA_API_KEY",
        "url": "https://api.novita.ai/openai/v1/chat/completions",
        "model": "mindai/macaron-v1-venti",
    },
    "cerebras_gptoss": {
        "provider": "cerebras",
        "key_name": "CEREBRAS_API_KEY",
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "model": "gpt-oss-120b",
    },
    "groq_gptoss": {
        "provider": "groq",
        "key_name": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "openai/gpt-oss-120b",
    },
    "openrouter_free": {
        "provider": "openrouter",
        "key_name": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "openrouter/free",
    },
}


# ATRI_ACTIVE_TASK_ROUTER_V243_POOL
_PROVIDER_DEFS.update(
    {
        "openrouter_gemma4": {
            "provider": "openrouter",
            "key_name": "OPENROUTER_API_KEY",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "model": "google/gemma-4-26b-a4b-it:free",
        },
        "openrouter_north": {
            "provider": "openrouter",
            "key_name": "OPENROUTER_API_KEY",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "model": "cohere/north-mini-code:free",
        },
        "openrouter_nemotron_super": {
            "provider": "openrouter",
            "key_name": "OPENROUTER_API_KEY",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "model": "nvidia/nemotron-3-super-120b-a12b:free",
        },
    }
)

_ATRI_TASK_CHAINS: dict[str, tuple[str, ...]] = {
    "chat": (
        "groq_gptoss",
        "cerebras_gptoss",
        "openrouter_gemma4",
        "openrouter_free",
    ),
    "coding": (
        "groq_gptoss",
        "cerebras_gptoss",
        "openrouter_north",
    ),
    "coding_agentic": (
        "openrouter_north",
        "groq_gptoss",
        "cerebras_gptoss",
    ),
    "research": (
        "groq_gptoss",
        "openrouter_gemma4",
        "openrouter_nemotron_super",
    ),
    "research_long": (
        "openrouter_nemotron_super",
        "openrouter_gemma4",
        "groq_gptoss",
    ),
}

_ATRI_TASK_FIXED_MODELS: dict[str, dict[str, str]] = {
    "chat": {
        "groq_gptoss": "qwen/qwen3.6-27b",
        "cerebras_gptoss": "gpt-oss-120b",
        "openrouter_gemma4": "google/gemma-4-26b-a4b-it:free",
        "openrouter_free": "openrouter/free",
    },
    "coding": {
        "groq_gptoss": "qwen/qwen3.6-27b",
        "cerebras_gptoss": "gpt-oss-120b",
        "openrouter_north": "cohere/north-mini-code:free",
    },
    "coding_agentic": {
        "openrouter_north": "cohere/north-mini-code:free",
        "groq_gptoss": "qwen/qwen3.6-27b",
        "cerebras_gptoss": "gpt-oss-120b",
    },
    "research": {
        "groq_gptoss": "qwen/qwen3.6-27b",
        "openrouter_gemma4": "google/gemma-4-26b-a4b-it:free",
        "openrouter_nemotron_super": "nvidia/nemotron-3-super-120b-a12b:free",
    },
    "research_long": {
        "openrouter_nemotron_super": "nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter_gemma4": "google/gemma-4-26b-a4b-it:free",
        "groq_gptoss": "qwen/qwen3.6-27b",
    },
}


def _atri_normalize_task_type(task_type: str) -> str:
    value = str(task_type or "chat").strip().casefold()
    if value not in _ATRI_TASK_CHAINS:
        return "chat"
    return value


def _atri_task_chain(task_type: str) -> tuple[str, ...]:
    task = _atri_normalize_task_type(task_type)
    return _ATRI_TASK_CHAINS[task]


def _atri_task_fixed_model(task_type: str, name: str) -> str:
    task = _atri_normalize_task_type(task_type)
    return str(_ATRI_TASK_FIXED_MODELS.get(task, {}).get(name, ""))


# ATRI_OPENROUTER_COOLDOWN_POLICY_V2431
def _atri_task_global_cooldown_key(
    spec: dict[str, str],
) -> str:
    if str(spec.get("provider", "")).casefold() == "openrouter":
        return "openrouter_free_global"
    return ""


def _atri_task_failure_cooldown_key(
    name: str,
    spec: dict[str, str],
    status_code: int | None,
) -> str:
    # Only a real rate-limit/quota response is shared across the
    # OpenRouter free family. Model-local failures must not suppress
    # sibling free models.
    global_key = _atri_task_global_cooldown_key(spec)
    if status_code == 429 and global_key:
        return global_key
    return name


def _atri_task_cooldown_until(
    name: str,
    spec: dict[str, str],
) -> float:
    local_until = float(_COOLDOWN_UNTIL.get(name, 0.0) or 0.0)
    global_key = _atri_task_global_cooldown_key(spec)

    if not global_key:
        return local_until

    global_until = float(
        _COOLDOWN_UNTIL.get(global_key, 0.0) or 0.0
    )
    return max(local_until, global_until)

_ENV_CACHE: dict[str, str] = {}
_ENV_MTIME_NS = -1
_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOCK = asyncio.Lock()
_COOLDOWN_UNTIL: dict[str, float] = {}

# ATRI_FREE_SMART_ROUTER_V2
_ROUTER_LATENCY_MS: dict[str, float] = {}
_ROUTER_REQUEST_RATIO: dict[str, float] = {}
_ROUTER_TOKEN_RATIO: dict[str, float] = {}
_ROUTER_CURRENT_WEIGHT: dict[str, float] = {
    "cerebras_gptoss": 0.0,
    "groq_gptoss": 0.0,
}

# ATRI_FREE_SMART_ROUTER_V21_RESET_AWARE
_ROUTER_REQUEST_RESET_AT: dict[str, float] = {}
_ROUTER_TOKEN_RESET_AT: dict[str, float] = {}

# ATRI_FREE_SMART_ROUTER_V22B_TOKEN_BUCKET
_ROUTER_WINDOW_RATIO_V22B: dict[str, dict[str, float]] = {}
_ROUTER_WINDOW_OBSERVED_AT_V22B: dict[str, dict[str, float]] = {}
_ROUTER_WINDOW_SECONDS_V22B: dict[str, dict[str, float]] = {}
_ROUTER_BOTTLENECK_V22B: dict[str, str] = {}

# ATRI_FREE_SMART_ROUTER_V22_MULTIWINDOW
_ROUTER_WINDOW_RATIO: dict[str, dict[str, float]] = {}
_ROUTER_WINDOW_EXPIRES_AT: dict[str, dict[str, float]] = {}
_ROUTER_BOTTLENECK: dict[str, str] = {}


@dataclass(frozen=True)
class FreeReply:
    text: str
    provider: str
    model: str


class FreeProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enable",
        "enabled",
    }


def _load_env_file() -> dict[str, str]:
    global _ENV_CACHE, _ENV_MTIME_NS

    try:
        stat = ENV_PATH.stat()
        mtime_ns = stat.st_mtime_ns
    except FileNotFoundError:
        _ENV_CACHE = {}
        _ENV_MTIME_NS = -1
        return {}

    if mtime_ns == _ENV_MTIME_NS:
        return dict(_ENV_CACHE)

    values: dict[str, str] = {}

    for raw_line in ENV_PATH.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        if key:
            values[key] = value

    _ENV_CACHE = values
    _ENV_MTIME_NS = mtime_ns
    return dict(values)


def _config() -> dict[str, str]:
    values = _load_env_file()
    for key, value in os.environ.items():
        if key.startswith(
            (
                "ATRI_FREE_",
                "NOVITA_",
                "CEREBRAS_",
                "GROQ_",
                "OPENROUTER_",
            )
        ):
            values[key] = value
    return values


def _get_int(
    values: dict[str, str],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(values.get(key, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _thinking_effort(level: str) -> str:
    level = str(level or "medium").casefold()
    if level in {"minimal", "low"}:
        return "low"
    if level == "high":
        return "high"
    return "medium"


async def _get_client() -> httpx.AsyncClient:
    global _CLIENT

    client = _CLIENT
    if client is not None and not client.is_closed:
        return client

    async with _CLIENT_LOCK:
        client = _CLIENT
        if client is not None and not client.is_closed:
            return client

        _CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=6,
                max_keepalive_connections=4,
            ),
            follow_redirects=True,
        )
        return _CLIENT


def _extract_text_parts(parts: Any) -> tuple[str, bool]:
    if not isinstance(parts, list):
        return "", False

    text_parts: list[str] = []
    has_non_text = False

    for part in parts:
        if not isinstance(part, dict):
            continue

        text = part.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)

        for key in (
            "inlineData",
            "fileData",
            "functionCall",
            "functionResponse",
            "audio",
            "image",
        ):
            if key in part:
                has_non_text = True

    return "\n".join(text_parts).strip(), has_non_text


def _build_messages(
    *,
    system_instruction: str,
    history: list[dict[str, Any]],
    current_parts: list[dict[str, Any]],
) -> list[dict[str, str]] | None:
    current_text, current_non_text = _extract_text_parts(current_parts)

    if current_non_text or not current_text:
        return None

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": str(system_instruction or "").strip(),
        }
    ]

    for item in history:
        if not isinstance(item, dict):
            continue

        role = str(item.get("role") or "").casefold()
        if role == "model":
            role = "assistant"
        elif role != "user":
            continue

        text, _ = _extract_text_parts(item.get("parts"))
        if text:
            messages.append(
                {
                    "role": role,
                    "content": text,
                }
            )

    messages.append(
        {
            "role": "user",
            "content": current_text,
        }
    )

    return messages


def _extract_response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    choice = choices[0]
    if not isinstance(choice, dict):
        return ""

    message = choice.get("message")
    if not isinstance(message, dict):
        return ""

    content = message.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks: list[str] = []

        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)

        return "\n".join(chunks).strip()

    return ""


async def _call_provider(
    *,
    spec: dict[str, str],
    api_key: str,
    messages: list[dict[str, str]],
    thinking_level: str,
    max_tokens: int,
    timeout_seconds: int,
) -> str:
    provider = spec["provider"]

    payload: dict[str, Any] = {
        "model": spec["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }

    # ATRI_PROVIDER_REASONING_V1
    if provider == "cerebras":
        level = str(thinking_level or "medium").casefold()

        if spec["model"] == "zai-glm-4.7" and level == "minimal":
            payload["reasoning_effort"] = "none"
        else:
            payload["reasoning_effort"] = _thinking_effort(level)

    elif provider == "groq":
        payload["reasoning_effort"] = _thinking_effort(
            thinking_level
        )

    elif provider == "openrouter":
        # ATRI_OPENROUTER_DYNAMIC_ROUTER_REASONING_FIX_V1
        # Dynamic OpenRouter routers do not expose reasoning controls.
        # Fixed reasoning-capable models still receive the requested
        # provider-specific thinking effort.
        if spec["model"] not in {
            "openrouter/free",
            "openrouter/auto",
        }:
            effort = str(
                thinking_level or "medium"
            ).casefold()

            if effort not in {
                "minimal",
                "low",
                "medium",
                "high",
            }:
                effort = "medium"

            payload["reasoning"] = {
                "effort": effort,
                "exclude": True,
            }

    elif provider == "novita":
        payload["enable_thinking"] = (
            str(thinking_level).casefold() != "minimal"
        )
        payload["separate_reasoning"] = True

    # ATRI_GROQ_QWEN36_REASONING_V241_ADAPTIVE
    # Applied after generic provider reasoning so Qwen's
    # none/default semantics win without rewriting old branches.
    if (
        provider == "groq"
        and str(spec.get("model", "")) == "qwen/qwen3.6-27b"
    ):
        qwen_level = str(thinking_level or "medium").casefold()
        if qwen_level in {"minimal", "low"}:
            payload["reasoning_effort"] = "none"
            payload.pop("reasoning_format", None)
        else:
            payload["reasoning_effort"] = "default"
            payload["reasoning_format"] = "hidden"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if provider == "openrouter":
        headers["X-Title"] = "Atri AI"

    client = await _get_client()

    timeout = httpx.Timeout(
        float(timeout_seconds),
        connect=min(5.0, float(timeout_seconds)),
    )

    try:
        response = await client.post(
            spec["url"],
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise FreeProviderError(
            f"network:{type(exc).__name__}"
        ) from exc

    _capture_rate_headers(provider, response.headers)

    if response.status_code >= 400:
        body = response.text.replace("\n", " ")[:240]
        raise FreeProviderError(
            f"http:{response.status_code}:{body}",
            status_code=response.status_code,
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise FreeProviderError("invalid_json") from exc

    text = _extract_response_text(data)

    if not text:
        raise FreeProviderError("empty_text")

    return text



def _router_safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _router_ratio(remaining: Any, limit: Any) -> float | None:
    r = _router_safe_float(remaining)
    l = _router_safe_float(limit)
    if r is None or l is None or l <= 0:
        return None
    return max(0.0, min(1.0, r / l))


def _router_reset_seconds(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value).strip().lower()
    if not text:
        return None

    try:
        return max(0.0, float(text))
    except Exception:
        pass

    total = 0.0
    number = ""

    try:
        for ch in text:
            if ch.isdigit() or ch == ".":
                number += ch
                continue

            if not number:
                continue

            amount = float(number)
            number = ""

            if ch == "h":
                total += amount * 3600.0
            elif ch == "m":
                total += amount * 60.0
            elif ch == "s":
                total += amount

        if number:
            total += float(number)

        return max(0.0, total)
    except Exception:
        return None


def _store_window_ratio_v22b(
    name: str,
    window: str,
    ratio: float | None,
    *,
    window_seconds: float,
) -> None:
    if ratio is None:
        return

    now = time.monotonic()
    _ROUTER_WINDOW_RATIO_V22B.setdefault(name, {})[window] = ratio
    _ROUTER_WINDOW_OBSERVED_AT_V22B.setdefault(name, {})[window] = now
    _ROUTER_WINDOW_SECONDS_V22B.setdefault(name, {})[window] = max(
        1.0,
        window_seconds,
    )


def _current_window_ratios_v22b(name: str) -> dict[str, float]:
    now = time.monotonic()
    ratios = _ROUTER_WINDOW_RATIO_V22B.get(name, {})
    observed = _ROUTER_WINDOW_OBSERVED_AT_V22B.get(name, {})
    windows = _ROUTER_WINDOW_SECONDS_V22B.get(name, {})

    current: dict[str, float] = {}
    fully_refilled: list[str] = []

    for window, base_ratio in list(ratios.items()):
        seen_at = observed.get(window, now)
        window_s = max(1.0, windows.get(window, 1.0))

        # Cerebras uses continuously replenished token buckets. Without a
        # newer response header, estimate recovered headroom linearly.
        estimated = min(
            1.0,
            max(
                0.0,
                base_ratio + max(0.0, now - seen_at) / window_s,
            ),
        )

        current[window] = estimated

        if estimated >= 0.999999:
            fully_refilled.append(window)

    for window in fully_refilled:
        ratios.pop(window, None)
        observed.pop(window, None)
        windows.pop(window, None)

    if not ratios:
        _ROUTER_WINDOW_RATIO_V22B.pop(name, None)
        _ROUTER_WINDOW_OBSERVED_AT_V22B.pop(name, None)
        _ROUTER_WINDOW_SECONDS_V22B.pop(name, None)

    return current


def _capture_rate_headers(
    provider: str,
    headers: httpx.Headers,
) -> None:
    if provider == "cerebras":
        name = "cerebras_gptoss"

        windows = {
            "req_minute": (
                headers.get("x-ratelimit-remaining-requests-minute"),
                headers.get("x-ratelimit-limit-requests-minute"),
                60.0,
            ),
            "req_hour": (
                headers.get("x-ratelimit-remaining-requests-hour"),
                headers.get("x-ratelimit-limit-requests-hour"),
                3600.0,
            ),
            "req_day": (
                headers.get("x-ratelimit-remaining-requests-day"),
                headers.get("x-ratelimit-limit-requests-day"),
                86400.0,
            ),
            "tok_minute": (
                headers.get("x-ratelimit-remaining-tokens-minute"),
                headers.get("x-ratelimit-limit-tokens-minute"),
                60.0,
            ),
            "tok_hour": (
                headers.get("x-ratelimit-remaining-tokens-hour"),
                headers.get("x-ratelimit-limit-tokens-hour"),
                3600.0,
            ),
            "tok_day": (
                headers.get("x-ratelimit-remaining-tokens-day"),
                headers.get("x-ratelimit-limit-tokens-day"),
                86400.0,
            ),
        }

        for window, (remaining, limit, window_s) in windows.items():
            _store_window_ratio_v22b(
                name,
                window,
                _router_ratio(remaining, limit),
                window_seconds=window_s,
            )

        current = _current_window_ratios_v22b(name)
        if current:
            _ROUTER_BOTTLENECK_V22B[name] = min(
                current,
                key=current.get,
            )
        return

    if provider == "groq":
        name = "groq_gptoss"

        request_ratio = _router_ratio(
            headers.get("x-ratelimit-remaining-requests"),
            headers.get("x-ratelimit-limit-requests"),
        )
        token_ratio = _router_ratio(
            headers.get("x-ratelimit-remaining-tokens"),
            headers.get("x-ratelimit-limit-tokens"),
        )

        request_reset = _router_reset_seconds(
            headers.get("x-ratelimit-reset-requests")
        )
        token_reset = _router_reset_seconds(
            headers.get("x-ratelimit-reset-tokens")
        )

        now = time.monotonic()

        if request_ratio is not None:
            _ROUTER_REQUEST_RATIO[name] = request_ratio
        if token_ratio is not None:
            _ROUTER_TOKEN_RATIO[name] = token_ratio
        if request_reset is not None:
            _ROUTER_REQUEST_RESET_AT[name] = now + request_reset
        if token_reset is not None:
            _ROUTER_TOKEN_RESET_AT[name] = now + token_reset

        candidates = {}
        if request_ratio is not None:
            candidates["req_day"] = request_ratio
        if token_ratio is not None:
            candidates["tok_minute"] = token_ratio

        if candidates:
            _ROUTER_BOTTLENECK_V22B[name] = min(
                candidates,
                key=candidates.get,
            )

def _router_float(
    values: dict[str, str],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(str(values.get(key, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _router_base_weight(name: str, values: dict[str, str]) -> float:
    if name == "cerebras_gptoss":
        return _router_float(
            values,
            "ATRI_FREE_WEIGHT_CEREBRAS",
            4.0,
            0.1,
            20.0,
        )
    if name == "groq_gptoss":
        return _router_float(
            values,
            "ATRI_FREE_WEIGHT_GROQ",
            1.0,
            0.1,
            20.0,
        )
    return 0.0


def _router_effective_weight(
    name: str,
    values: dict[str, str],
) -> float:
    now = time.monotonic()

    if _COOLDOWN_UNTIL.get(name, 0.0) > now:
        return 0.0

    weight = _router_base_weight(name, values)

    if name == "cerebras_gptoss":
        current = _current_window_ratios_v22b(name)

        if current:
            bottleneck = min(current, key=current.get)
            _ROUTER_BOTTLENECK_V22B[name] = bottleneck
            weight *= max(0.05, current[bottleneck])

            request_values = [
                value
                for window, value in current.items()
                if window.startswith("req_")
            ]
            token_values = [
                value
                for window, value in current.items()
                if window.startswith("tok_")
            ]

            if request_values:
                _ROUTER_REQUEST_RATIO[name] = min(request_values)
            else:
                _ROUTER_REQUEST_RATIO.pop(name, None)

            if token_values:
                _ROUTER_TOKEN_RATIO[name] = min(token_values)
            else:
                _ROUTER_TOKEN_RATIO.pop(name, None)

        else:
            _ROUTER_REQUEST_RATIO.pop(name, None)
            _ROUTER_TOKEN_RATIO.pop(name, None)
            _ROUTER_BOTTLENECK_V22B.pop(name, None)

    else:
        request_ratio = _ROUTER_REQUEST_RATIO.get(name)
        if (
            request_ratio is not None
            and _ROUTER_REQUEST_RESET_AT.get(name, now + 1.0) <= now
        ):
            request_ratio = None
            _ROUTER_REQUEST_RATIO.pop(name, None)
            _ROUTER_REQUEST_RESET_AT.pop(name, None)

        token_ratio = _ROUTER_TOKEN_RATIO.get(name)
        if (
            token_ratio is not None
            and _ROUTER_TOKEN_RESET_AT.get(name, now + 1.0) <= now
        ):
            token_ratio = None
            _ROUTER_TOKEN_RATIO.pop(name, None)
            _ROUTER_TOKEN_RESET_AT.pop(name, None)

        ratios = [
            ratio
            for ratio in (request_ratio, token_ratio)
            if ratio is not None
        ]

        if ratios:
            weight *= max(0.05, min(ratios))

    latency = _ROUTER_LATENCY_MS.get(name)
    if latency is not None:
        latency_factor = 1000.0 / max(500.0, latency)
        latency_factor = max(0.65, min(1.35, latency_factor))
        weight *= latency_factor

    return max(0.0, weight)

def _smart_order(
    chain: list[str],
    values: dict[str, str],
) -> list[str]:
    if not _truthy(
        values.get("ATRI_FREE_SMART_ROUTER"),
        default=True,
    ):
        return list(chain)

    primary = [
        name
        for name in ("cerebras_gptoss", "groq_gptoss")
        if (
            name in chain
            and name in _PROVIDER_DEFS
            and str(
                values.get(_PROVIDER_DEFS[name]["key_name"], "")
            ).strip()
            and _COOLDOWN_UNTIL.get(name, 0.0) <= time.monotonic()
        )
    ]

    if len(primary) < 2:
        return list(chain)

    weights = {
        name: _router_effective_weight(name, values)
        for name in primary
    }

    total = sum(weights.values())
    if total <= 0:
        return list(chain)

    for name in primary:
        _ROUTER_CURRENT_WEIGHT[name] = (
            _ROUTER_CURRENT_WEIGHT.get(name, 0.0) + weights[name]
        )

    selected = max(
        primary,
        key=lambda name: _ROUTER_CURRENT_WEIGHT[name],
    )
    _ROUTER_CURRENT_WEIGHT[selected] -= total

    rest_primary = [name for name in primary if name != selected]
    rest_primary.sort(
        key=lambda name: weights[name],
        reverse=True,
    )

    fallback = [name for name in chain if name not in primary]
    ordered = [selected, *rest_primary, *fallback]

    LOGGER.info(
        "ATRI_FREE_ROUTER_ORDER primary=%s order=%s cerebras_w=%.3f groq_w=%.3f",
        selected,
        ",".join(ordered),
        weights.get("cerebras_gptoss", 0.0),
        weights.get("groq_gptoss", 0.0),
    )

    return ordered


def _record_router_latency(
    name: str,
    elapsed_ms: int,
    values: dict[str, str],
) -> None:
    if name not in {"cerebras_gptoss", "groq_gptoss"}:
        return

    alpha = _router_float(
        values,
        "ATRI_FREE_LATENCY_EWMA_ALPHA",
        0.25,
        0.05,
        1.0,
    )

    old = _ROUTER_LATENCY_MS.get(name)
    if old is None:
        ewma = float(elapsed_ms)
    else:
        ewma = alpha * float(elapsed_ms) + (1.0 - alpha) * old

    _ROUTER_LATENCY_MS[name] = ewma

    LOGGER.info(
        "ATRI_FREE_ROUTER_METRIC name=%s ewma_ms=%s request_remaining=%s token_remaining=%s bottleneck=%s",
        name,
        int(ewma),
        (
            f"{_ROUTER_REQUEST_RATIO[name]:.4f}"
            if name in _ROUTER_REQUEST_RATIO
            else "NA"
        ),
        (
            f"{_ROUTER_TOKEN_RATIO[name]:.4f}"
            if name in _ROUTER_TOKEN_RATIO
            else "NA"
        ),
        _ROUTER_BOTTLENECK_V22B.get(name, "NA"),
    )


def smart_router_status() -> dict[str, Any]:
    values = _config()
    now = time.monotonic()
    providers: dict[str, Any] = {}

    for name in ("cerebras_gptoss", "groq_gptoss"):
        req_reset = _ROUTER_REQUEST_RESET_AT.get(name)
        tok_reset = _ROUTER_TOKEN_RESET_AT.get(name)

        windows = {}
        if name == "cerebras_gptoss":
            current = _current_window_ratios_v22b(name)
            observed = _ROUTER_WINDOW_OBSERVED_AT_V22B.get(name, {})
            window_sizes = _ROUTER_WINDOW_SECONDS_V22B.get(name, {})

            for window, ratio in current.items():
                windows[window] = {
                    "ratio": ratio,
                    "age_s": max(
                        0.0,
                        now - observed.get(window, now),
                    ),
                    "window_s": window_sizes.get(window),
                }

        providers[name] = {
            "effective_weight": _router_effective_weight(name, values),
            "ewma_ms": _ROUTER_LATENCY_MS.get(name),
            "request_remaining_ratio": _ROUTER_REQUEST_RATIO.get(name),
            "token_remaining_ratio": _ROUTER_TOKEN_RATIO.get(name),
            "bottleneck": _ROUTER_BOTTLENECK_V22B.get(name),
            "windows": windows,
            "request_reset_in_s": (
                max(0.0, req_reset - now)
                if req_reset is not None
                else None
            ),
            "token_reset_in_s": (
                max(0.0, tok_reset - now)
                if tok_reset is not None
                else None
            ),
            "cooldown_in_s": max(
                0.0,
                _COOLDOWN_UNTIL.get(name, 0.0) - now,
            ),
        }

    return {
        "enabled": _truthy(
            values.get("ATRI_FREE_SMART_ROUTER"),
            default=True,
        ),
        "providers": providers,
    }

def free_pool_status() -> dict[str, Any]:
    values = _config()

    enabled = _truthy(
        values.get("ATRI_FREE_POOL_ENABLED"),
        default=True,
    )

    chain_raw = values.get("ATRI_FREE_CHAT_CHAIN", "")
    chain = [
        x.strip()
        for x in chain_raw.split(",")
        if x.strip()
    ] or list(DEFAULT_CHAIN)

    items: list[dict[str, Any]] = []

    for name in chain:
        spec = _PROVIDER_DEFS.get(name)
        if not spec:
            items.append(
                {
                    "name": name,
                    "known": False,
                    "key": False,
                }
            )
            continue

        items.append(
            {
                "name": name,
                "known": True,
                "provider": spec["provider"],
                "model": spec["model"],
                "key": bool(
                    str(values.get(spec["key_name"], "")).strip()
                ),
            }
        )

    return {
        "enabled": enabled,
        "env_path": str(ENV_PATH),
        "chain": items,
    }



# ATRI_FREE_DYNAMIC_TOKEN_BUDGET_V1
def _dynamic_max_tokens(
    values: dict[str, str],
    thinking_level: str,
) -> int:
    global_cap = _get_int(
        values,
        "ATRI_FREE_MAX_TOKENS",
        4096,
        64,
        16384,
    )

    level = str(thinking_level or "medium").casefold()

    defaults = {
        "minimal": 512,
        "low": 1024,
        "medium": 2048,
        "high": 3072,
    }

    keys = {
        "minimal": "ATRI_FREE_MAX_TOKENS_MINIMAL",
        "low": "ATRI_FREE_MAX_TOKENS_LOW",
        "medium": "ATRI_FREE_MAX_TOKENS_MEDIUM",
        "high": "ATRI_FREE_MAX_TOKENS_HIGH",
    }

    if level not in defaults:
        level = "medium"

    level_cap = _get_int(
        values,
        keys[level],
        defaults[level],
        64,
        8192,
    )

    result = min(global_cap, level_cap)

    LOGGER.info(
        "ATRI_FREE_TOKEN_BUDGET thinking=%s max_tokens=%s",
        thinking_level,
        result,
    )

    return result

async def generate_free_chat(
    *,
    system_instruction: str,
    history: list[dict[str, Any]],
    current_parts: list[dict[str, Any]],
    thinking_level: str = "medium",
    task_type: str = "chat",
) -> FreeReply | None:
    values = _config()

    if not _truthy(
        values.get("ATRI_FREE_POOL_ENABLED"),
        default=True,
    ):
        return None

    # ATRI_FREE_NATURAL_STYLE_V1
    system_instruction = naturalize_system_instruction(
        system_instruction,
        current_parts,
    )

    messages = _build_messages(
        system_instruction=system_instruction,
        history=history,
        current_parts=current_parts,
    )

    if messages is None:
        LOGGER.info(
            "ATRI_FREE_POOL_SKIP reason=non_text_or_empty"
        )
        return None

    chain_raw = values.get("ATRI_FREE_CHAT_CHAIN", "")
    chain = [
        x.strip()
        for x in chain_raw.split(",")
        if x.strip()
    ] or list(DEFAULT_CHAIN)

    # ATRI_FREE_PROVIDER_MODE_V1
    # ATRI_ACTIVE_TASK_ROUTER_V243_RUNTIME
    task_type = _atri_normalize_task_type(task_type)
    provider_mode = resolve_provider_mode()

    manual_name = {
        "cerebras": "cerebras_gptoss",
        "groq": "groq_gptoss",
        "openrouter": "openrouter_free",
    }.get(provider_mode)

    if provider_mode == "vertex":
        LOGGER.info("ATRI_FREE_POOL_SKIP reason=manual_vertex")
        return None

    if manual_name:
        chain = [manual_name]
    else:
        task_chain = list(_atri_task_chain(task_type))
        if task_type == "coding_agentic":
            chain = task_chain
        else:
            chain = _smart_order(task_chain, values)

    LOGGER.info(
        "ATRI_TASK_ROUTER_ORDER task=%s provider_mode=%s order=%s",
        task_type,
        provider_mode,
        ",".join(chain),
    )

    max_attempts = _get_int(
        values,
        "ATRI_FREE_MAX_ATTEMPTS",
        3,
        1,
        5,
    )
    if provider_mode == "smart" and task_type == "chat":
        max_attempts = max(max_attempts, 4)
    max_tokens = _dynamic_max_tokens(
        values,
        thinking_level,
    )
    timeout_seconds = _get_int(
        values,
        "ATRI_FREE_REQUEST_TIMEOUT",
        20,
        5,
        60,
    )

    attempted = 0
    now = time.monotonic()

    for name in chain:
        if attempted >= max_attempts:
            break

        spec = _PROVIDER_DEFS.get(name)
        if not spec:
            continue

        # ATRI_FREE_PROVIDER_MODEL_V1
        spec = dict(spec)

        fixed_model = (
            _atri_task_fixed_model(task_type, name)
            if provider_mode == "smart"
            else ""
        )

        if fixed_model:
            spec["model"] = fixed_model
        else:
            spec["model"] = resolve_provider_model(
                spec["provider"],
                spec["model"],
            )

        key = str(values.get(spec["key_name"], "")).strip()
        if not key:
            continue

        if _atri_task_cooldown_until(name, spec) > now:
            continue

        # ATRI_FREE_PROVIDER_THINKING_V1
        provider_thinking = resolve_provider_thinking(
            spec["provider"],
            thinking_level,
        )

        attempted += 1
        started = time.monotonic()

        LOGGER.info(
            "ATRI_FREE_PROVIDER_START name=%s provider=%s model=%s thinking=%s",
            name,
            spec["provider"],
            spec["model"],
            provider_thinking,
        )

        try:
            text = await _call_provider(
                spec=spec,
                api_key=key,
                messages=messages,
                thinking_level=provider_thinking,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )

        except FreeProviderError as exc:
            status = exc.status_code

            # ATRI_MODEL_SELF_HEAL_V231
            if is_terminal_model_error(
                status,
                str(exc),
            ):
                mark_model_unavailable(
                    spec["provider"],
                    spec["model"],
                    str(exc),
                )

                LOGGER.warning(
                    "ATRI_MODEL_MARK_DEAD provider=%s model=%s status=%s",
                    spec["provider"],
                    spec["model"],
                    status,
                )

            if status in {401, 403}:
                cooldown = 300.0
            elif status == 429:
                cooldown = 60.0
            elif status is not None and status >= 500:
                cooldown = 20.0
            else:
                cooldown = 10.0

            _COOLDOWN_UNTIL[
                _atri_task_failure_cooldown_key(
                    name,
                    spec,
                    status,
                )
            ] = (
                time.monotonic() + cooldown
            )

            LOGGER.warning(
                "ATRI_FREE_PROVIDER_FAIL name=%s provider=%s model=%s status=%s elapsed_ms=%s error=%s",
                name,
                spec["provider"],
                spec["model"],
                status if status is not None else "NA",
                int((time.monotonic() - started) * 1000),
                str(exc)[:300],
            )
            continue

        except Exception as exc:
            _COOLDOWN_UNTIL[name] = (
                time.monotonic() + 15.0
            )
            LOGGER.warning(
                "ATRI_FREE_PROVIDER_FAIL name=%s provider=%s model=%s status=NA elapsed_ms=%s error=%s",
                name,
                spec["provider"],
                spec["model"],
                int((time.monotonic() - started) * 1000),
                f"{type(exc).__name__}:{exc}"[:300],
            )
            continue

        elapsed_ms = int(
            (time.monotonic() - started) * 1000
        )
        _record_router_latency(
            name,
            elapsed_ms,
            values,
        )

        LOGGER.info(
            "ATRI_FREE_PROVIDER_DONE name=%s provider=%s model=%s elapsed_ms=%s chars=%s",
            name,
            spec["provider"],
            spec["model"],
            elapsed_ms,
            len(text),
        )

        return FreeReply(
            text=text,
            provider=spec["provider"],
            model=spec["model"],
        )

    if attempted:
        LOGGER.warning(
            "ATRI_FREE_POOL_EXHAUSTED attempts=%s; fallback=vertex",
            attempted,
        )

    return None
