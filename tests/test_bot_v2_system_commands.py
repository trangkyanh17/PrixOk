from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bot_v2.commands import system


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self


async def _refresh_three_times():
    return [await system.refresh_package_versions() for _ in range(3)]


def test_package_version_refresh_is_idempotent_across_three_runs(monkeypatch):
    original_specs = dict(system.TOOL_SPECS)

    async def fake_tool_version(argv, pattern):
        assert isinstance(argv, tuple)
        assert isinstance(pattern, str)
        return f"ok:{argv[0]}"

    async def fake_commit_version():
        return "commit-ok"

    monkeypatch.setattr(system, "_tool_version", fake_tool_version)
    monkeypatch.setattr(system, "_commit_version", fake_commit_version)

    results = asyncio.run(_refresh_three_times())

    assert system.TOOL_SPECS == original_specs
    assert len(results) == 3
    assert results[0] == results[1] == results[2]
    assert results[-1]["commit"] == "commit-ok"
    assert results[-1]["python"] == "ok:python3"


async def _stats_three_times(message):
    for _ in range(3):
        await system.bot_stats(None, message)


def test_stats_three_invocations_produce_three_responses(monkeypatch):
    fake_data = {
        "total": 1000,
        "used": 400,
        "free": 600,
        "disk_percent": 40.0,
        "swap": SimpleNamespace(total=200, percent=10.0),
        "memory": SimpleNamespace(percent=20.0, total=100, available=80, used=20),
        "net": SimpleNamespace(bytes_sent=10, bytes_recv=20),
        "per_cpu": [1.0, 2.0],
        "overall_cpu": 3.0,
        "physical_cores": 2,
        "total_cores": 4,
        "boot_time": 0.0,
    }

    async def fake_to_thread(func, *args, **kwargs):
        assert func is system._collect_stats
        return fake_data

    monkeypatch.setattr(system.asyncio, "to_thread", fake_to_thread)
    system.PACKAGE_VERSIONS.update(
        {
            "commit": "test-commit",
            "python": "3.13",
            "aria2": "1",
            "qBittorrent": "1",
            "SABnzbd+": "1",
            "rclone": "1",
            "yt-dlp": "1",
            "ffmpeg": "1",
            "7z": "1",
        }
    )

    message = FakeMessage()
    asyncio.run(_stats_three_times(message))

    assert len(message.replies) == 3
    assert all("test-commit" in text for text, _ in message.replies)
    assert all("<b>CPU:</b> 3.0%" in text for text, _ in message.replies)
