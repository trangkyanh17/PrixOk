from __future__ import annotations

from typing import Any

# ATRI_GPTOSS_RESPONSE_POLICY_V1624


# ATRI_OPENROUTER_NATIVE_REASONING_V159_PILOT
_OPENROUTER_PROVIDER_DEFAULT_REASONING = {
    # ATRI_PROVIDER_REASONING_ADAPTER_V162
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "liquid/lfm-2.5-1.2b-thinking:free",
}
_OPENROUTER_HIGH_ONLY_REASONING = {
    "deepseek/deepseek-v4-flash:free",
}

def _thinking_effort(level: str) -> str:
    normalized = str(level or "medium").casefold()
    if normalized in {"minimal", "low"}:
        return "low"
    if normalized == "high":
        return "high"
    return "medium"


def build_provider_headers(
    provider: str,
    api_key: str,
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if str(provider).casefold() == "openrouter":
        headers["X-Title"] = "Atri AI"

    return headers


def build_chat_payload(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    thinking_level: str,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    provider = str(provider).casefold()
    model = str(model)

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    if provider == "cerebras":
        level = str(thinking_level or "medium").casefold()
        if model == "zai-glm-4.7" and level == "minimal":
            payload["reasoning_effort"] = "none"
        else:
            payload["reasoning_effort"] = _thinking_effort(level)

    elif provider == "groq":
        payload["reasoning_effort"] = _thinking_effort(
            thinking_level
        )

    elif provider == "openrouter" and model not in {
        "openrouter/free",
        "openrouter/auto",
    }:
        if model in _OPENROUTER_PROVIDER_DEFAULT_REASONING:
            pass
        elif model in _OPENROUTER_HIGH_ONLY_REASONING:
            payload["reasoning"] = {
                "effort": "high",
                "exclude": True,
            }
        else:
            effort = str(thinking_level or "medium").casefold()
            if effort not in {"minimal", "low", "medium", "high"}:
                effort = "medium"
            payload["reasoning"] = {
                "effort": effort,
                "exclude": True,
            }

    if provider == "groq" and model in {
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    }:
        payload["include_reasoning"] = False

    if provider == "cerebras" and model == "gpt-oss-120b":
        payload["reasoning_format"] = "hidden"

    if provider == "groq" and model == "qwen/qwen3.6-27b":
        qwen_level = str(thinking_level or "medium").casefold()
        if qwen_level in {"minimal", "low"}:
            payload["reasoning_effort"] = "none"
            payload.pop("reasoning_format", None)
        else:
            payload["reasoning_effort"] = "default"
            payload["reasoning_format"] = "hidden"

    return payload
