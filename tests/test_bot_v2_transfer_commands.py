from __future__ import annotations

import asyncio
from dataclasses import dataclass

from bot_v2.commands import transfers
from bot_v2.tasks import BackgroundTaskSupervisor


@dataclass
class FakeChat:
    id: int = -100123


@dataclass
class FakeMessage:
    id: int = 777
    chat: FakeChat = FakeChat()


class FakeOperation:
    def __init__(self, events, kind: str, kwargs):
        self.events = events
        self.kind = kind
        self.kwargs = dict(kwargs)

    async def new_event(self):
        self.events.append((self.kind, self.kwargs))
        await asyncio.sleep(0)


def test_all_transfer_entrypoints_are_replay_safe_across_three_invocations(monkeypatch):
    async def scenario():
        events = []
        supervisor = BackgroundTaskSupervisor()
        monkeypatch.setattr(transfers, "SUPERVISOR", supervisor)

        monkeypatch.setattr(
            transfers,
            "Mirror",
            lambda _client, _message, **kwargs: FakeOperation(
                events,
                "mirror",
                kwargs,
            ),
        )
        monkeypatch.setattr(
            transfers,
            "YtDlp",
            lambda _client, _message, **kwargs: FakeOperation(
                events,
                "ytdl",
                kwargs,
            ),
        )
        monkeypatch.setattr(
            transfers,
            "GalleryDL",
            lambda _client, _message, **kwargs: FakeOperation(
                events,
                "gallery",
                kwargs,
            ),
        )
        monkeypatch.setattr(
            transfers,
            "MediaDirectYtDlp",
            lambda _client, _message, **kwargs: FakeOperation(
                events,
                "media-direct",
                kwargs,
            ),
        )

        message = FakeMessage()
        callbacks = (
            transfers.mirror,
            transfers.qb_mirror,
            transfers.jd_mirror,
            transfers.nzb_mirror,
            transfers.leech,
            transfers.qb_leech,
            transfers.jd_leech,
            transfers.nzb_leech,
            transfers.ytdl,
            transfers.ytdl_leech,
            transfers.gallery_dl,
            transfers.gallery_dl_leech,
            transfers.media_direct,
        )

        for callback in callbacks:
            for _ in range(3):
                await callback(object(), message)

        # Let the 13 accepted tasks complete and done callbacks drain.
        await asyncio.sleep(0)
        pending = supervisor.tasks
        if pending:
            await asyncio.gather(*pending)
        await asyncio.sleep(0)

        # Three dispatcher replays of each command must still create one
        # business operation for that route/message pair.
        assert len(events) == len(callbacks) == 13

        assert ("mirror", {}) in events
        assert ("mirror", {"is_qbit": True}) in events
        assert ("mirror", {"is_jd": True}) in events
        assert ("mirror", {"is_nzb": True}) in events
        assert ("mirror", {"is_leech": True}) in events
        assert ("mirror", {"is_qbit": True, "is_leech": True}) in events
        assert ("mirror", {"is_jd": True, "is_leech": True}) in events
        assert ("mirror", {"is_nzb": True, "is_leech": True}) in events
        assert ("ytdl", {}) in events
        assert ("ytdl", {"is_leech": True}) in events
        assert ("gallery", {}) in events
        assert ("gallery", {"is_leech": True}) in events
        assert ("media-direct", {}) in events

    asyncio.run(scenario())
