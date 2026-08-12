from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "bot"
    / "modules"
    / "atri_provider_request.py"
)
spec = importlib.util.spec_from_file_location(
    "atri_provider_request_test",
    MODULE_PATH,
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

build_chat_payload = module.build_chat_payload
build_provider_headers = module.build_provider_headers


def _payload(provider: str, model: str, level: str = "medium"):
    return build_chat_payload(
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": "Reply OK."}],
        thinking_level=level,
        max_tokens=16,
        temperature=0,
    )


def test_groq_qwen_payload_matches_runtime_reasoning_adapter():
    payload = _payload("groq", "qwen/qwen3.6-27b")

    assert payload["reasoning_effort"] == "default"
    assert payload["reasoning_format"] == "hidden"


def test_openrouter_dynamic_router_omits_reasoning_controls():
    payload = _payload("openrouter", "openrouter/free")

    assert "reasoning" not in payload


def test_openrouter_fixed_model_and_headers_match_runtime():
    payload = _payload(
        "openrouter",
        "google/gemma-4-26b-a4b-it:free",
        "high",
    )
    headers = build_provider_headers("openrouter", "secret")

    assert payload["reasoning"] == {
        "effort": "high",
        "exclude": True,
    }
    assert headers["Authorization"] == "Bearer secret"
    assert headers["X-Title"] == "Atri AI"
