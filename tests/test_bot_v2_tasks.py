from __future__ import annotations

import asyncio

from bot_v2.tasks import BackgroundTaskSupervisor


def test_spawn_once_blocks_three_replays_of_same_claim():
    async def scenario():
        supervisor = BackgroundTaskSupervisor()
        calls = []

        async def work(value):
            calls.append(value)
            await asyncio.sleep(0)

        first = supervisor.spawn_once(
            work("first"),
            name="first",
            claim="chat:1:message:2:route:mirror",
        )
        second = supervisor.spawn_once(
            work("second"),
            name="second",
            claim="chat:1:message:2:route:mirror",
        )
        third = supervisor.spawn_once(
            work("third"),
            name="third",
            claim="chat:1:message:2:route:mirror",
        )

        assert first is not None
        assert second is None
        assert third is None
        await first
        await asyncio.sleep(0)
        assert calls == ["first"]

        # The claim remains after completion, so a late replay is also blocked.
        late = supervisor.spawn_once(
            work("late"),
            name="late",
            claim="chat:1:message:2:route:mirror",
        )
        assert late is None
        assert calls == ["first"]

    asyncio.run(scenario())


def test_distinct_claims_are_independent():
    async def scenario():
        supervisor = BackgroundTaskSupervisor()
        calls = []

        async def work(value):
            calls.append(value)

        tasks = [
            supervisor.spawn_once(
                work(value),
                name=value,
                claim=f"claim:{value}",
            )
            for value in ("mirror", "leech", "ytdl")
        ]
        assert all(task is not None for task in tasks)
        await asyncio.gather(*(task for task in tasks if task is not None))
        assert sorted(calls) == ["leech", "mirror", "ytdl"]

    asyncio.run(scenario())


def test_shutdown_cancels_owned_tasks():
    async def scenario():
        supervisor = BackgroundTaskSupervisor()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = supervisor.spawn(work(), name="long-running")
        await started.wait()
        await supervisor.shutdown(timeout=1.0)

        assert task.cancelled()
        assert cancelled.is_set()

    asyncio.run(scenario())
