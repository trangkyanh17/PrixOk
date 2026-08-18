import asyncio
import importlib.util
import subprocess
from pathlib import Path


def _start_bot_block() -> str:
    source = Path("bot/core/telegram_manager.py").read_text(encoding="utf-8")
    start = source.index("async def start_bot")
    end = source.index("async def start_user", start)
    return source[start:end]


def _load_telegram_startup_module():
    path = Path("bot/core/telegram_startup.py")
    spec = importlib.util.spec_from_file_location("atri_telegram_startup_v1674", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bot_token_session_survives_process_restarts():
    bot_block = _start_bot_block()

    assert "bot_token=Config.BOT_TOKEN" in bot_block
    assert "workdir=\"/app\"" in bot_block
    assert "in_memory=True" not in bot_block
    assert "in_memory=False" in bot_block
    assert "await start_bot_client(cls.bot, LOGGER)" in bot_block


def test_bot_start_floodwait_waits_and_retries_same_client():
    module = _load_telegram_startup_module()

    class FakeFloodWait(Exception):
        def __init__(self, value: int):
            super().__init__(value)
            self.value = value

    class FakeClient:
        def __init__(self):
            self.calls = 0

        async def start(self):
            self.calls += 1
            if self.calls == 1:
                raise FakeFloodWait(7)
            return self

    class FakeLogger:
        def __init__(self):
            self.events = []

        def warning(self, message, *args):
            self.events.append((message, args))

    module.FloodWait = FakeFloodWait
    client = FakeClient()
    logger = FakeLogger()
    sleeps = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    result = asyncio.run(
        module.start_bot_client(client, logger, sleeper=fake_sleep)
    )

    assert result is client
    assert client.calls == 2
    assert sleeps == [8]
    assert logger.events == [
        (
            "TELEGRAM_BOT_START_FLOOD_WAIT seconds=%s action=wait-in-process",
            (7,),
        )
    ]


def test_bot_start_non_flood_error_remains_fail_fast():
    module = _load_telegram_startup_module()

    class FatalClient:
        async def start(self):
            raise RuntimeError("invalid token or transport failure")

    class FakeLogger:
        def warning(self, *_args):
            raise AssertionError("non-FloodWait must not be swallowed")

    async def run():
        await module.start_bot_client(FatalClient(), FakeLogger())

    try:
        asyncio.run(run())
    except RuntimeError as exc:
        assert str(exc) == "invalid token or transport failure"
    else:
        raise AssertionError("non-FloodWait startup failure was swallowed")


def test_v150_launcher_respawns_supervisor_without_respawning_bot_itself():
    path = Path("rewrite/termux-v150-production-watchdog.sh")
    source = path.read_text(encoding="utf-8")

    assert "while true; do" in source
    assert "SUPERVISOR_EXIT" in source
    assert "SUPERVISOR_RESTART_BACKOFF" in source
    assert "trap 'request_stop' TERM INT HUP" in source
    assert "ATRI_V150_WRAPPER_ALREADY_RUNNING" in source
    assert '"$BIN" 8>&- &' in source
    assert 'exec env \\\n' not in source
    assert "tmux new-session" not in source

    completed = subprocess.run(
        ["bash", str(path), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "v150 production watchdog self-test: PASS" in completed.stdout
