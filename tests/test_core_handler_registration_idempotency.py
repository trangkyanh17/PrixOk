from collections import Counter
from types import SimpleNamespace

import pytest

import bot
from bot.core import handlers


class _FakeClient:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        callback = getattr(handler, "callback", None)
        self.handlers.append(
            (
                type(handler).__name__,
                getattr(callback, "__name__", repr(callback)),
                group,
            )
        )


class _FakeLoop:
    def __init__(self):
        self.created = 0

    def create_task(self, coroutine, *args, **kwargs):
        self.created += 1
        close = getattr(coroutine, "close", None)
        if close is not None:
            close()
        return SimpleNamespace(cancel=lambda: None)


def _disable_extension_registrars(monkeypatch):
    names = (
        "add_atri_media_auto_handlers",
        "add_atri_skills_handlers",
        "add_atri_unified_menu_handlers",
        "add_atri_command_ui_handlers",
        "add_atri_thinking_handlers",
        "add_atri_provider_control_handlers",
        "add_atri_rose_natural_handlers",
        "add_atri_rose_handlers",
    )
    for name in names:
        monkeypatch.setattr(handlers, name, lambda _client: None)

    async def _noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(handlers, "start_free_tools", _noop_async)
    monkeypatch.setattr(handlers, "sync_bot_command_menu", _noop_async)


@pytest.mark.parametrize("repeat", [2, 3, 10])
def test_core_handler_registration_is_once_per_client(monkeypatch, repeat):
    client = _FakeClient()
    loop = _FakeLoop()
    _disable_extension_registrars(monkeypatch)
    monkeypatch.setattr(handlers.TgClient, "bot", client)
    monkeypatch.setattr(bot, "bot_loop", loop)

    first = handlers.add_handlers()
    first_snapshot = list(client.handlers)
    assert first is True
    assert first_snapshot, "core handler registration unexpectedly produced no handlers"

    for _ in range(repeat - 1):
        assert handlers.add_handlers() is False

    assert client.handlers == first_snapshot

    callbacks = Counter(callback for _, callback, _ in client.handlers)
    assert callbacks["ping"] == 1
    assert callbacks["bot_help"] == 1
    assert callbacks["mirror"] == 1
    assert callbacks["leech"] == 1
    assert callbacks["atri_message"] == 1


def test_core_handler_registration_is_per_client_not_global(monkeypatch):
    loop = _FakeLoop()
    _disable_extension_registrars(monkeypatch)
    monkeypatch.setattr(bot, "bot_loop", loop)

    client_a = _FakeClient()
    monkeypatch.setattr(handlers.TgClient, "bot", client_a)
    assert handlers.add_handlers() is True

    client_b = _FakeClient()
    monkeypatch.setattr(handlers.TgClient, "bot", client_b)
    assert handlers.add_handlers() is True

    assert client_a.handlers
    assert client_b.handlers
    assert len(client_a.handlers) == len(client_b.handlers)
