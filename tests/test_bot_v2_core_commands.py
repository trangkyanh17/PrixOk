from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bot_v2.commands import core


class FakeReply:
    async def edit_text(self, *_args, **_kwargs):
        return self


class FakeMessage:
    def __init__(self, user_id=123):
        self.from_user = SimpleNamespace(id=user_id)
        self.replies = []
        self.documents = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return FakeReply()

    async def reply_document(self, document, **kwargs):
        self.documents.append((document, kwargs))
        return FakeReply()


async def _run_three(callback, client, message):
    for _ in range(3):
        await callback(client, message)


def test_start_three_invocations_have_three_responses(monkeypatch):
    async def authorized(_client, _message):
        return True

    monkeypatch.setattr(core.CustomFilters, "authorized", authorized)
    message = FakeMessage()

    asyncio.run(_run_three(core.start, object(), message))

    assert len(message.replies) == 3
    assert all("Type /" in text for text, _ in message.replies)
    assert all(kwargs.get("reply_markup") is not None for _, kwargs in message.replies)


def test_log_three_invocations_send_three_documents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "log.txt").write_text("prixok-v2-test\n", encoding="utf-8")
    message = FakeMessage()

    asyncio.run(_run_three(core.log, None, message))

    assert len(message.documents) == 3
    assert all(document == "log.txt" for document, _ in message.documents)
    assert all(kwargs.get("caption") == "PrixOk log" for _, kwargs in message.documents)


def test_log_missing_file_is_one_visible_response(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    message = FakeMessage()

    asyncio.run(core.log(None, message))

    assert len(message.documents) == 0
    assert len(message.replies) == 1
    assert "chưa tồn tại" in message.replies[0][0]


def test_native_core_commands_do_not_use_new_task_wrapper():
    source = core.__file__
    text = open(source, encoding="utf-8").read()
    assert "@new_task" not in text
    assert "from bot.helper.ext_utils.bot_utils import new_task" not in text
