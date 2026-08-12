from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType

import httpx
import pytest


os.environ.setdefault(
    "ATRI_PROVIDER_CAPABILITIES_STATE_PATH",
    str(
        Path(tempfile.gettempdir())
        / f"prixok-provider-capabilities-{os.getpid()}.json"
    ),
)

MODULE_DIR = (
    Path(__file__).resolve().parent.parent / "bot" / "modules"
)
PACKAGE_NAME = "atri_provider_capabilities_testpkg"
package = ModuleType(PACKAGE_NAME)
package.__path__ = [str(MODULE_DIR)]
sys.modules[PACKAGE_NAME] = package


def _load_module(name: str):
    qualified = f"{PACKAGE_NAME}.{name}"
    spec = importlib.util.spec_from_file_location(
        qualified,
        MODULE_DIR / f"{name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_load_module("atri_provider_config")
_load_module("atri_provider_request")
capabilities = _load_module("atri_provider_capabilities")

_check_provider_key = capabilities._check_provider_key
_classify_key_check = capabilities._classify_key_check
_probe_openai_model = capabilities._probe_openai_model
audit_alert_events = capabilities.audit_alert_events
audit_alert_text = capabilities.audit_alert_text
build_audit_alert_snapshot = capabilities.build_audit_alert_snapshot


def test_key_check_classifies_credentials_separately_from_plan_errors():
    assert _classify_key_check(200) == ("ok", "key_valid")
    assert _classify_key_check(401) == ("invalid", "key_invalid")
    assert _classify_key_check(403) == ("denied", "auth_or_plan")
    assert _classify_key_check(429) == ("unknown", "rate_limited")


@pytest.mark.asyncio
async def test_missing_key_skips_network_request():
    async def handler(_request):
        raise AssertionError("network must not be called")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await _check_provider_key(
            client,
            url="https://provider.test/models",
            key="",
            semaphore=asyncio.Semaphore(1),
        )

    assert result == {
        "status": "missing",
        "reason": "key_missing",
        "http_status": None,
    }


@pytest.mark.asyncio
async def test_probe_uses_same_reasoning_payload_as_runtime():
    captured = {}

    async def handler(request):
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await _probe_openai_model(
            client,
            url="https://provider.test/chat/completions",
            provider="groq",
            key="test-key",
            model="qwen/qwen3.6-27b",
            semaphore=asyncio.Semaphore(1),
        )

    assert result["status"] == "ok"
    assert '"reasoning_effort":"default"' in captured["body"]
    assert '"reasoning_format":"hidden"' in captured["body"]


def _alert_report(
    *,
    key_status="ok",
    key_reason="key_valid",
    first_status="ok",
    second_status="ok",
):
    return {
        "cerebras": {
            "key": {
                "status": key_status,
                "reason": key_reason,
                "http_status": 200 if key_status == "ok" else 401,
            },
            "models": {
                "gpt-oss-120b": {
                    "status": first_status,
                    "reason": (
                        "live_probe"
                        if first_status == "ok"
                        else "model_not_listed"
                    ),
                    "http_status": 200 if first_status == "ok" else 404,
                },
                "zai-glm-4.7": {
                    "status": second_status,
                    "reason": (
                        "live_probe"
                        if second_status == "ok"
                        else "model_not_listed"
                    ),
                    "http_status": 200 if second_status == "ok" else 404,
                },
            },
        }
    }


def test_alerts_only_on_state_transitions():
    healthy = _alert_report()
    previous = build_audit_alert_snapshot(healthy)

    assert audit_alert_events(healthy, previous) == []

    failed = _alert_report(
        key_status="invalid",
        key_reason="key_invalid",
        first_status="unknown",
        second_status="unknown",
    )
    events = audit_alert_events(failed, previous)

    assert [event["kind"] for event in events] == ["key_failed"]
    assert "Cerebras key lỗi" in audit_alert_text(events)


def test_alerts_for_dead_models_provider_outage_and_recovery():
    healthy = _alert_report()
    healthy_snapshot = build_audit_alert_snapshot(healthy)
    dead = _alert_report(first_status="dead", second_status="dead")

    failed_events = audit_alert_events(dead, healthy_snapshot)
    assert [event["kind"] for event in failed_events] == [
        "model_dead",
        "model_dead",
        "provider_all_dead",
    ]

    dead_snapshot = build_audit_alert_snapshot(dead)
    recovered_events = audit_alert_events(healthy, dead_snapshot)
    assert [event["kind"] for event in recovered_events] == [
        "model_recovered",
        "model_recovered",
        "provider_recovered",
    ]
    text = audit_alert_text(recovered_events)
    assert "OSS120B đã phục hồi" in text
    assert "Cerebras: đã hoạt động lại" in text
