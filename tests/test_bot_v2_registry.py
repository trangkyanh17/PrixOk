from __future__ import annotations

import pytest

from bot_v2.registry import GuardedClient, HandlerRegistry, RouteConflictError


async def callback_a(*_):
    return None


async def callback_b(*_):
    return None


class MessageHandler:
    def __init__(self, callback):
        self.callback = callback


class CallbackQueryHandler:
    def __init__(self, callback):
        self.callback = callback


class FakeClient:
    def __init__(self):
        self.added = []
        self.identity = "fake-client"

    def add_handler(self, handler, group=0):
        self.added.append((handler, group))
        return handler, group


def test_exact_duplicate_is_idempotent():
    client = FakeClient()
    registry = HandlerRegistry(client)

    assert registry.add(MessageHandler(callback_a), group=0, route_id="ping") is True
    assert registry.add(MessageHandler(callback_a), group=0, route_id="ping") is False

    assert len(client.added) == 1
    assert len(registry.records) == 1


def test_same_callback_can_exist_in_different_handler_type_or_group():
    client = FakeClient()
    registry = HandlerRegistry(client)

    assert registry.add(MessageHandler(callback_a), group=0, route_id="message") is True
    assert registry.add(MessageHandler(callback_a), group=1, route_id="message-g1") is True
    assert registry.add(CallbackQueryHandler(callback_a), group=0, route_id="callback") is True

    assert len(client.added) == 3


def test_route_id_cannot_be_rebound_to_different_callback():
    client = FakeClient()
    registry = HandlerRegistry(client)

    registry.add(MessageHandler(callback_a), route_id="core.ping")

    with pytest.raises(RouteConflictError):
        registry.add(MessageHandler(callback_b), route_id="core.ping")

    assert len(client.added) == 1


def test_guarded_client_forces_extension_registration_through_registry():
    client = FakeClient()
    registry = HandlerRegistry(client)
    guarded = GuardedClient(registry)

    guarded.add_handler(MessageHandler(callback_a), group=5)
    guarded.add_handler(MessageHandler(callback_a), group=5)

    assert len(client.added) == 1
    assert guarded.identity == "fake-client"


def test_inventory_is_stable_and_countable():
    client = FakeClient()
    registry = HandlerRegistry(client)

    registry.add(MessageHandler(callback_a), group=2, route_id="a")
    registry.add(MessageHandler(callback_b), group=3, route_id="b")

    assert registry.count_callback(callback_a) == 1
    assert registry.count_callback(callback_b) == 1
    assert len(registry.inventory_lines()) == 2
    assert registry.inventory_lines()[0].startswith("a\t2\t")
    assert registry.inventory_lines()[1].startswith("b\t3\t")
