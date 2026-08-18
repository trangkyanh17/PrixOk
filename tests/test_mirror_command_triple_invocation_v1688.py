from __future__ import annotations

import asyncio

from bot.modules import gallery_dl as gallery_mod
from bot.modules import mirror_leech as mirror_mod
from bot.modules import ytdlp as ytdlp_mod


class FakeLoop:
    def __init__(self):
        self.created = []

    def create_task(self, coroutine):
        self.created.append(coroutine)
        close = getattr(coroutine, "close", None)
        if close is not None:
            close()
        return coroutine


class FakeJob:
    constructed = []

    def __init__(self, *args, **kwargs):
        type(self).constructed.append((args, kwargs))

    async def new_event(self):
        return "scheduled"


def _run(coro):
    return asyncio.run(coro)


def _exercise(module, klass_name, callback_name, expected_kwargs, monkeypatch):
    FakeJob.constructed = []
    loop = FakeLoop()
    monkeypatch.setattr(module, klass_name, FakeJob)
    monkeypatch.setattr(module, "bot_loop", loop)
    callback = getattr(module, callback_name)
    client = object()
    message = object()

    for _ in range(3):
        _run(callback(client, message))

    assert len(loop.created) == 3, callback_name
    assert len(FakeJob.constructed) == 3, callback_name
    for args, kwargs in FakeJob.constructed:
        assert args == (client, message), (callback_name, args)
        assert kwargs == expected_kwargs, (callback_name, kwargs)


def test_every_mirror_and_leech_callback_schedules_once_per_invocation_three_times(monkeypatch):
    cases = [
        ("mirror", {}),
        ("qb_mirror", {"is_qbit": True}),
        ("jd_mirror", {"is_jd": True}),
        ("nzb_mirror", {"is_nzb": True}),
        ("leech", {"is_leech": True}),
        ("qb_leech", {"is_qbit": True, "is_leech": True}),
        ("jd_leech", {"is_leech": True, "is_jd": True}),
        ("nzb_leech", {"is_leech": True, "is_nzb": True}),
    ]
    for callback, kwargs in cases:
        _exercise(mirror_mod, "Mirror", callback, kwargs, monkeypatch)


def test_ytdlp_callbacks_schedule_once_per_invocation_three_times(monkeypatch):
    for callback, kwargs in [
        ("ytdl", {}),
        ("ytdl_leech", {"is_leech": True}),
    ]:
        _exercise(ytdlp_mod, "YtDlp", callback, kwargs, monkeypatch)


def test_gallery_callbacks_schedule_once_per_invocation_three_times(monkeypatch):
    for callback, kwargs in [
        ("gallery_dl", {}),
        ("gallery_dl_leech", {"is_leech": True}),
    ]:
        _exercise(gallery_mod, "GalleryDL", callback, kwargs, monkeypatch)
