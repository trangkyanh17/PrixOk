from __future__ import annotations

import pytest

from bot_v2.contracts import assert_no_duplicate_message_commands
from bot_v2.registry import HandlerRegistry


async def callback_a(*_):
    return None


async def callback_b(*_):
    return None


class FakeCommandFilter:
    def __init__(self, *commands):
        self.commands = set(commands)


class MessageHandler:
    def __init__(self, callback, filters=None):
        self.callback = callback
        self.filters = filters


class EditedMessageHandler:
    def __init__(self, callback, filters=None):
        self.callback = callback
        self.filters = filters


class FakeClient:
    def __init__(self):
        self.added = []

    def add_handler(self, handler, group=0):
        self.added.append((handler, group))
        return handler, group


def test_two_message_handlers_cannot_own_same_command():
    registry = HandlerRegistry(FakeClient())
    registry.add(
        MessageHandler(callback_a, FakeCommandFilter("ping")),
        group=0,
        route_id="first",
    )
    registry.add(
        MessageHandler(callback_b, FakeCommandFilter("ping")),
        group=10,
        route_id="second",
    )

    with pytest.raises(RuntimeError, match="duplicate message command ownership"):
        assert_no_duplicate_message_commands(registry)


def test_alias_overlap_is_also_rejected():
    registry = HandlerRegistry(FakeClient())
    registry.add(
        MessageHandler(callback_a, FakeCommandFilter("mirror", "m")),
        route_id="mirror",
    )
    registry.add(
        MessageHandler(callback_b, FakeCommandFilter("m")),
        route_id="other",
    )

    with pytest.raises(RuntimeError, match=r"/m="):
        assert_no_duplicate_message_commands(registry)


def test_edited_message_support_does_not_count_as_duplicate_message_owner():
    registry = HandlerRegistry(FakeClient())
    registry.add(
        MessageHandler(callback_a, FakeCommandFilter("shell")),
        route_id="shell.new",
    )
    registry.add(
        EditedMessageHandler(callback_a, FakeCommandFilter("shell")),
        route_id="shell.edited",
    )

    assert_no_duplicate_message_commands(registry)
