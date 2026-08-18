from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import bot
from bot.core import handlers
from bot.modules import atri_capability_bootstrap


class FakeClient:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        callback = getattr(handler, "callback", None)
        self.handlers.append(
            (
                type(handler).__name__,
                getattr(callback, "__module__", ""),
                getattr(callback, "__name__", repr(callback)),
                int(group),
            )
        )


class FakeLoop:
    def __init__(self):
        self.created = 0

    def create_task(self, coroutine, *args, **kwargs):
        self.created += 1
        close = getattr(coroutine, "close", None)
        if close is not None:
            close()
        return SimpleNamespace(cancel=lambda: None)


def _disable_nested_registrars(monkeypatch):
    for name in (
        "add_atri_media_auto_handlers",
        "add_atri_skills_handlers",
        "add_atri_unified_menu_handlers",
        "add_atri_command_ui_handlers",
        "add_atri_thinking_handlers",
        "add_atri_provider_control_handlers",
        "add_atri_rose_natural_handlers",
        "add_atri_rose_handlers",
    ):
        monkeypatch.setattr(handlers, name, lambda _client: None)

    async def noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(handlers, "start_free_tools", noop_async)
    monkeypatch.setattr(handlers, "sync_bot_command_menu", noop_async)


def _callback_counts(client: FakeClient) -> Counter:
    return Counter(name for _cls, _module, name, _group in client.handlers)


def test_core_registration_is_idempotent_under_10_replays(monkeypatch):
    client = FakeClient()
    loop = FakeLoop()
    _disable_nested_registrars(monkeypatch)
    monkeypatch.setattr(handlers.TgClient, "bot", client)
    monkeypatch.setattr(bot, "bot_loop", loop)

    handlers.add_handlers()
    baseline = list(client.handlers)
    assert baseline

    for _ in range(9):
        handlers.add_handlers()

    assert client.handlers == baseline, (
        "core add_handlers() multiplied Telegram routes when replayed; "
        f"baseline={len(baseline)} final={len(client.handlers)}"
    )
    counts = _callback_counts(client)
    for callback in ("ping", "bot_help", "mirror", "leech", "atri_message"):
        assert counts[callback] == 1, (callback, counts[callback])


def test_core_registration_is_per_client_not_process_global(monkeypatch):
    loop = FakeLoop()
    _disable_nested_registrars(monkeypatch)
    monkeypatch.setattr(bot, "bot_loop", loop)

    first = FakeClient()
    monkeypatch.setattr(handlers.TgClient, "bot", first)
    handlers.add_handlers()

    second = FakeClient()
    monkeypatch.setattr(handlers.TgClient, "bot", second)
    handlers.add_handlers()

    assert first.handlers
    assert second.handlers
    assert len(first.handlers) == len(second.handlers)


def test_capability_registration_is_idempotent_under_10_replays(monkeypatch):
    client = FakeClient()

    # Exercise the real capability registrar. It only builds Pyrogram handlers;
    # no Telegram/network startup is involved.
    atri_capability_bootstrap.add_capability_runtime_handlers(client)
    baseline = list(client.handlers)
    assert baseline

    for _ in range(9):
        atri_capability_bootstrap.add_capability_runtime_handlers(client)

    assert client.handlers == baseline, (
        "capability handler registration multiplied routes when replayed; "
        f"baseline={len(baseline)} final={len(client.handlers)}"
    )
