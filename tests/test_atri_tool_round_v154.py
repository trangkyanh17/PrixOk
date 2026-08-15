from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeVertexError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


class _FakeResponse:
    status_code = 200

    def __init__(self, function_calls: int) -> None:
        self.function_calls = function_calls

    def json(self):
        if self.function_calls <= 0:
            parts = [{"text": "final answer"}]
        else:
            parts = [
                {
                    "functionCall": {
                        "name": f"tool_{index}",
                        "args": {},
                    }
                }
                for index in range(self.function_calls)
            ]
        return {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": parts,
                    }
                }
            ]
        }


class _FakeClient:
    is_closed = False

    def __init__(self, responses):
        self.responses = list(responses)

    async def post(self, *args, **kwargs):
        del args, kwargs
        return self.responses.pop(0)


def test_tool_round_budget_counts_model_rounds_not_parallel_call_count():
    from bot.modules import atri_system_guard as guard

    client = _FakeClient(
        [
            _FakeResponse(3),
            _FakeResponse(2),
            _FakeResponse(0),
        ]
    )
    proxy = guard._VertexClientProxy(
        client,
        SimpleNamespace(VertexRequestError=_FakeVertexError),
    )
    token = guard._TOOL_ROUND_STATE.set(
        {"mode": "code", "limit": 8, "rounds": 0}
    )
    try:
        asyncio.run(proxy.post("https://vertex.invalid"))
        assert guard._TOOL_ROUND_STATE.get()["rounds"] == 1

        asyncio.run(proxy.post("https://vertex.invalid"))
        assert guard._TOOL_ROUND_STATE.get()["rounds"] == 2

        # A final text/continuation response is not a tool round.
        asyncio.run(proxy.post("https://vertex.invalid"))
        assert guard._TOOL_ROUND_STATE.get()["rounds"] == 2
    finally:
        guard._TOOL_ROUND_STATE.reset(token)


def test_ninth_code_tool_round_is_blocked_before_executor_can_run():
    from bot.modules import atri_system_guard as guard

    client = _FakeClient([_FakeResponse(1) for _ in range(9)])
    proxy = guard._VertexClientProxy(
        client,
        SimpleNamespace(VertexRequestError=_FakeVertexError),
    )
    token = guard._TOOL_ROUND_STATE.set(
        {"mode": "code", "limit": 8, "rounds": 0}
    )
    try:
        for _ in range(8):
            asyncio.run(proxy.post("https://vertex.invalid"))
        assert guard._TOOL_ROUND_STATE.get()["rounds"] == 8

        with pytest.raises(_FakeVertexError) as caught:
            asyncio.run(proxy.post("https://vertex.invalid"))
        assert caught.value.reason == "TOOL_ROUND_LIMIT"
        assert guard._TOOL_ROUND_STATE.get()["rounds"] == 9
    finally:
        guard._TOOL_ROUND_STATE.reset(token)


def test_post_import_guard_is_armed_after_handlers_import_at_boot():
    source = Path("bot/__main__.py").read_text(encoding="utf-8")
    assert "install_atri_system_post_import_guard" in source
    assert source.index("from .core.handlers import add_handlers") < source.index(
        "install_atri_system_post_import_guard()"
    )
    assert source.index("add_handlers()") < source.index(
        "install_atri_system_post_import_guard()"
    )


def test_chat_web_tools_code_declarations_remain_wired_to_atri_core():
    source = Path("bot/modules/atri_ai.py").read_text(encoding="utf-8")

    assert "*GOOGLE_TOOL_DECLARATIONS" in source
    assert "WEATHER_TOOL_DECLARATION" in source
    assert "*CODE_PLUGIN_DECLARATIONS" in source
    assert '"name": "code_web_search"' in source
    assert 'payload["tools"] = [{"googleSearch": {}}]' in source
    assert "generate_free_chat(" in source
    assert "build_long_memory_context(" in source
    assert "_atri_build_attachment_context_v143(" in source
