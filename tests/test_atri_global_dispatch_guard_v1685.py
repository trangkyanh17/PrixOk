from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from pyrogram import StopPropagation


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str):
    path = ROOT / "bot/modules/atri_update_idempotency_v1684.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _configure_claims(module, tmp_path: Path) -> None:
    module.UPDATE_CLAIM_DIR = tmp_path / "claims"
    module.UPDATE_CLAIM_TTL_SECONDS = 600
    module.UPDATE_SWEEP_INTERVAL_SECONDS = 120
    module._LAST_SWEEP_AT = 0.0


class _Message:
    def __init__(self, message_id: int):
        self.id = message_id
        self.message_id = message_id
        self.message_thread_id = 0
        self.chat = SimpleNamespace(id=5825099053)
        self.stop_calls = 0

    def stop_propagation(self):
        self.stop_calls += 1
        raise StopPropagation


class _Query:
    def __init__(self, query_id: str):
        self.id = query_id
        self.data = "aucm:42:refresh"
        self.stop_calls = 0

    def stop_propagation(self):
        self.stop_calls += 1
        raise StopPropagation


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _Client:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.append((group, handler))

    def remove_handler(self, handler, group=0):
        self.handlers.remove((group, handler))


def test_v1685_global_and_route_claim_namespaces_are_independent(tmp_path):
    module = _load_module("atri_global_dispatch_namespace_test")
    _configure_claims(module, tmp_path)

    message = _Message(5001)

    accepted, identity = module.claim_telegram_update_once(
        message,
        route="global-dispatch-message",
    )
    replay, replay_identity = module.claim_telegram_update_once(
        _Message(5001),
        route="global-dispatch-message",
    )

    downstream, downstream_identity = module.claim_telegram_update_once(
        _Message(5001),
        route="unified-menu-command",
    )
    downstream_replay, _ = module.claim_telegram_update_once(
        _Message(5001),
        route="unified-menu-command",
    )

    assert accepted is True
    assert replay is False
    assert downstream is True
    assert downstream_replay is False
    assert (
        identity
        == replay_identity
        == downstream_identity
        == "message:5825099053:0:5001"
    )


def test_v1685_seven_concurrent_message_replays_accept_exactly_one(tmp_path):
    module = _load_module("atri_global_dispatch_concurrency_test")
    _configure_claims(module, tmp_path)

    barrier = threading.Barrier(8)

    def contender():
        barrier.wait(timeout=5)
        return module.claim_telegram_update_once(
            _Message(5002),
            route="global-dispatch-message",
        )[0]

    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(contender) for _ in range(7)]
        barrier.wait(timeout=5)
        results = [future.result(timeout=5) for future in futures]

    assert results.count(True) == 1
    assert results.count(False) == 6


def test_v1685_ping_like_dispatch_replay_reaches_downstream_once(
    tmp_path,
    monkeypatch,
):
    module = _load_module("atri_global_dispatch_ping_test")
    _configure_claims(module, tmp_path)

    fake_bot = SimpleNamespace(LOGGER=_Logger())
    monkeypatch.setitem(sys.modules, "bot", fake_bot)

    calls = 0

    async def ping_like(_, _message):
        nonlocal calls
        calls += 1

    async def dispatch_one(message):
        try:
            await module._global_message_guard(None, message)
        except StopPropagation:
            return
        await ping_like(None, message)

    async def scenario():
        await asyncio.gather(
            *(dispatch_one(_Message(5003)) for _ in range(7))
        )

    asyncio.run(scenario())
    assert calls == 1


def test_v1685_help_like_dispatch_replay_reaches_downstream_once(
    tmp_path,
    monkeypatch,
):
    module = _load_module("atri_global_dispatch_help_test")
    _configure_claims(module, tmp_path)

    fake_bot = SimpleNamespace(LOGGER=_Logger())
    monkeypatch.setitem(sys.modules, "bot", fake_bot)

    replies = 0

    async def help_like(_, _message):
        nonlocal replies
        replies += 1

    async def dispatch_one(message):
        try:
            await module._global_message_guard(None, message)
        except StopPropagation:
            return
        await help_like(None, message)

    async def scenario():
        for _ in range(7):
            await dispatch_one(_Message(5004))

    asyncio.run(scenario())
    assert replies == 1


def test_v1685_callback_replay_is_exactly_once(tmp_path, monkeypatch):
    module = _load_module("atri_global_dispatch_callback_test")
    _configure_claims(module, tmp_path)

    fake_bot = SimpleNamespace(LOGGER=_Logger())
    monkeypatch.setitem(sys.modules, "bot", fake_bot)

    edits = 0

    async def callback_like(_, _query):
        nonlocal edits
        edits += 1

    async def dispatch_one(query):
        try:
            await module._global_callback_guard(None, query)
        except StopPropagation:
            return
        await callback_like(None, query)

    async def scenario():
        await asyncio.gather(
            *(dispatch_one(_Query("query-5005")) for _ in range(7))
        )
        await dispatch_one(_Query("query-5006"))

    asyncio.run(scenario())
    assert edits == 2


def test_v1685_global_guard_registers_once_at_earliest_group(
    tmp_path,
    monkeypatch,
):
    module = _load_module("atri_global_dispatch_registry_test")
    _configure_claims(module, tmp_path)

    fake_bot = SimpleNamespace(LOGGER=_Logger())
    monkeypatch.setitem(sys.modules, "bot", fake_bot)

    client = _Client()
    assert module.install_atri_global_dispatch_guard_v1685(client) is True
    assert module.install_atri_global_dispatch_guard_v1685(client) is False

    assert len(client.handlers) == 2
    assert {group for group, _ in client.handlers} == {-10000}


def test_v1685_production_import_hook_precedes_normal_handler_registration():
    source = (
        ROOT / "bot/modules/atri_update_idempotency_v1684.py"
    ).read_text(encoding="utf-8")
    handlers = (ROOT / "bot/core/handlers.py").read_text(encoding="utf-8")

    assert "ATRI_GLOBAL_DISPATCH_GUARD_V1685" in source
    assert "_GLOBAL_HANDLER_GROUP = -10000" in source
    assert "_install_global_dispatch_guard_if_ready()" in source

    assert "from ..modules.atri_unified_menu import" in handlers

    main = (ROOT / "bot/__main__.py").read_text(encoding="utf-8")
    assert main.index("from .core.handlers import add_handlers") < main.index(
        "add_handlers()"
    )


def test_v1685_existing_v1684_help_contract_is_preserved():
    source = (
        ROOT / "bot/modules/atri_update_idempotency_v1684.py"
    ).read_text(encoding="utf-8")

    assert "claim_telegram_update_once" in source
    assert "global-dispatch-message" in source
    assert "global-dispatch-callback" in source
    assert "/app/atri_data/atri_telegram_update_claims" in source
