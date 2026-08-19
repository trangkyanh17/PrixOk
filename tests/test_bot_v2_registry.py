from __future__ import annotations

import pytest

from bot_v2.registry import GuardedClient, HandlerRegistry, RouteConflictError


async def callback_a(*_):
    return None


async def callback_b(*_):
    return None


class MessageHandler:
    def __init__(self, callback, filters=None):
        self.callback = callback
        self.filters = filters


class CallbackQueryHandler:
    def __init__(self, callback, filters=None):
        self.callback = callback
        self.filters = filters


class FakeFilter:
    def __init__(self, command, *, case_sensitive=True):
        self.commands = {command}
        self.case_sensitive = case_sensitive


class FakeAndFilter:
    def __init__(self, base, other):
        self.base = base
        self.other = other


class FakePermissionFilter:
    def __init__(self, name):
        self.name = name


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


def test_semantically_equal_filters_are_idempotent():
    client = FakeClient()
    registry = HandlerRegistry(client)

    first = MessageHandler(callback_a, FakeFilter("ping"))
    second = MessageHandler(callback_a, FakeFilter("ping"))

    assert registry.add(first, group=0) is True
    assert registry.add(second, group=0) is False
    assert len(client.added) == 1


def test_same_callback_with_different_filters_is_not_dropped():
    client = FakeClient()
    registry = HandlerRegistry(client)

    ping = MessageHandler(callback_a, FakeFilter("ping"))
    help_ = MessageHandler(callback_a, FakeFilter("help"))

    assert registry.add(ping, group=0, route_id="ping") is True
    assert registry.add(help_, group=0, route_id="help") is True
    assert len(client.added) == 2
    assert len(registry.records) == 2


def test_command_inventory_survives_compound_permission_filter():
    client = FakeClient()
    registry = HandlerRegistry(client)
    compound = FakeAndFilter(FakeFilter("ping"), FakePermissionFilter("authorized"))

    registry.add(MessageHandler(callback_a, compound), route_id="core.ping")

    owners = registry.command_owners("ping")
    assert len(owners) == 1
    route_id, key = owners[0]
    assert route_id == "core.ping"
    assert key.commands == ("ping",)


def test_same_callback_can_exist_in_different_handler_type_or_group():
    client = FakeClient()
    registry = HandlerRegistry(client)

    assert registry.add(MessageHandler(callback_a), group=0, route_id="message") is True
    assert registry.add(MessageHandler(callback_a), group=1, route_id="message-g1") is True
    assert registry.add(CallbackQueryHandler(callback_a), group=0, route_id="callback") is True

    assert len(client.added) == 3


def test_route_id_cannot_be_rebound_to_different_callback_or_filter():
    client = FakeClient()
    registry = HandlerRegistry(client)

    registry.add(
        MessageHandler(callback_a, FakeFilter("ping")),
        route_id="core.ping",
    )

    with pytest.raises(RouteConflictError):
        registry.add(
            MessageHandler(callback_b, FakeFilter("ping")),
            route_id="core.ping",
        )

    with pytest.raises(RouteConflictError):
        registry.add(
            MessageHandler(callback_a, FakeFilter("help")),
            route_id="core.ping",
        )

    assert len(client.added) == 1


def test_guarded_client_forces_extension_registration_through_registry():
    client = FakeClient()
    registry = HandlerRegistry(client)
    guarded = GuardedClient(registry)

    guarded.add_handler(MessageHandler(callback_a), group=5)
    guarded.add_handler(MessageHandler(callback_a), group=5)

    assert len(client.added) == 1
    assert guarded.identity == "fake-client"


def test_inventory_is_stable_countable_and_contains_commands_and_filter():
    client = FakeClient()
    registry = HandlerRegistry(client)

    registry.add(
        MessageHandler(callback_a, FakeFilter("ping")),
        group=2,
        route_id="a",
    )
    registry.add(
        MessageHandler(callback_b, FakeFilter("help")),
        group=3,
        route_id="b",
    )

    assert registry.count_callback(callback_a) == 1
    assert registry.count_callback(callback_b) == 1
    assert len(registry.inventory_lines()) == 2

    first = registry.inventory_lines()[0].split("\t")
    second = registry.inventory_lines()[1].split("\t")
    assert first[0] == "a"
    assert first[1] == "2"
    assert first[4] == "ping"
    assert second[0] == "b"
    assert second[1] == "3"
    assert second[4] == "help"
    assert len(first) == 6
    assert len(first[-1]) == 20
    assert first[-1] != second[-1]
