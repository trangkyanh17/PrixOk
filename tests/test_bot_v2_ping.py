from __future__ import annotations

import asyncio
from pathlib import Path

from bot_v2.commands.core import ping


class FakeReply:
    def __init__(self, owner):
        self.owner = owner

    async def edit_text(self, text, **kwargs):
        self.owner.edits.append((text, kwargs))
        return self


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return FakeReply(self)


async def _invoke_three_times(message: FakeMessage) -> None:
    for _ in range(3):
        await ping(None, message)


def test_ping_three_invocations_produce_exactly_three_reply_edit_pipelines():
    message = FakeMessage()
    asyncio.run(_invoke_three_times(message))

    assert len(message.replies) == 3
    assert len(message.edits) == 3
    assert [text for text, _ in message.replies] == ["Starting Ping"] * 3
    assert all(text.endswith(" ms") for text, _ in message.edits)


def test_native_ping_has_no_legacy_new_task_wrapper():
    text = Path("bot_v2/commands/core.py").read_text(encoding="utf-8")
    assert "@new_task" not in text
    assert "from bot.helper.ext_utils.bot_utils import new_task" not in text
