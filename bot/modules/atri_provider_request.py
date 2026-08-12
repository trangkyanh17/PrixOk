from __future__ import annotations

from typing import Any


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
        effort = str(thinking_level or "medium").casefold()
        if effort not in {"minimal", "low", "medium", "high"}:
            effort = "medium"
        payload["reasoning"] = {
            "effort": effort,
            "exclude": True,
        }

    if provider == "groq" and model == "qwen/qwen3.6-27b":
        qwen_level = str(thinking_level or "medium").casefold()
        if qwen_level in {"minimal", "low"}:
            payload["reasoning_effort"] = "none"
            payload.pop("reasoning_format", None)
        else:
            payload["reasoning_effort"] = "default"
            payload["reasoning_format"] = "hidden"

    return payload
