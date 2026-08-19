from __future__ import annotations

from pyrogram import filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from bot_v2.registry import HandlerRegistry, make_handler_key


async def callback(*_):
    return None


class FakeClient:
    def __init__(self):
        self.added = []

    def add_handler(self, handler, group=0):
        self.added.append((handler, group))
        return handler, group


def test_real_kurigram_command_filter_exposes_command_inventory():
    handler = MessageHandler(
        callback,
        filters=filters.command(["ping", "p"], case_sensitive=True)
        & filters.private,
    )

    key = make_handler_key(handler, 0)
    assert key.commands == ("p", "ping")
    assert len(key.filter_fingerprint) == 20


def test_equivalent_real_filters_dedupe_but_different_commands_do_not():
    registry = HandlerRegistry(FakeClient())

    ping_a = MessageHandler(
        callback,
        filters=filters.command("ping", case_sensitive=True)
        & filters.private,
    )
    ping_b = MessageHandler(
        callback,
        filters=filters.command("ping", case_sensitive=True)
        & filters.private,
    )
    help_ = MessageHandler(
        callback,
        filters=filters.command("help", case_sensitive=True)
        & filters.private,
    )

    assert registry.add(ping_a, group=0, route_id="ping") is True
    assert registry.add(ping_b, group=0) is False
    assert registry.add(help_, group=0, route_id="help") is True

    assert len(registry.records) == 2
    assert len(registry.command_owners("ping")) == 1
    assert len(registry.command_owners("help")) == 1


def test_real_regex_filters_have_stable_and_distinct_fingerprints():
    first_a = CallbackQueryHandler(callback, filters=filters.regex(r"^first:"))
    first_b = CallbackQueryHandler(callback, filters=filters.regex(r"^first:"))
    second = CallbackQueryHandler(callback, filters=filters.regex(r"^second:"))

    key_a = make_handler_key(first_a, -10)
    key_b = make_handler_key(first_b, -10)
    key_second = make_handler_key(second, -10)

    assert key_a.filter_fingerprint == key_b.filter_fingerprint
    assert key_a.filter_fingerprint != key_second.filter_fingerprint

    registry = HandlerRegistry(FakeClient())
    assert registry.add(first_a, group=-10) is True
    assert registry.add(first_b, group=-10) is False
    assert registry.add(second, group=-10) is True
    assert len(registry.records) == 2
