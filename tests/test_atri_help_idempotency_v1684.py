from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _configure_claims(module, tmp_path: Path, ttl: int = 600) -> None:
    module.UPDATE_CLAIM_DIR = tmp_path / "update-claims"
    module.UPDATE_CLAIM_TTL_SECONDS = ttl
    module.UPDATE_SWEEP_INTERVAL_SECONDS = max(30, ttl)
    module._LAST_SWEEP_AT = 0.0


class _Update:
    def __init__(self, update_id: int):
        self.update_id = update_id


def test_v1684_update_claim_is_atomic_and_update_scoped(tmp_path):
    module = _load_module(
        "atri_update_idempotency_v1684_atomic_test",
        ROOT / "bot/modules/atri_update_idempotency_v1684.py",
    )
    _configure_claims(module, tmp_path)

    first, identity = module.claim_telegram_update_once(
        _Update(4001),
        route="help",
    )
    replay, replay_identity = module.claim_telegram_update_once(
        _Update(4001),
        route="help-replay",
    )
    independent, independent_identity = module.claim_telegram_update_once(
        _Update(4002),
        route="help",
    )

    assert first is True
    assert replay is False
    assert independent is True
    assert identity == replay_identity == "update:4001"
    assert independent_identity == "update:4002"

    barrier = threading.Barrier(3)

    def contender() -> bool:
        barrier.wait(timeout=5)
        return module.claim_telegram_update_once(
            _Update(4003),
            route="help-concurrent",
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        barrier.wait(timeout=5)
        results = [future.result(timeout=5) for future in futures]

    assert sorted(results) == [False, True]


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _Config:
    CMD_SUFFIX = ""
    OWNER_ID = 42
    SUDO_USERS = ""


_ISOLATED_MODULES = (
    "bot",
    "bot.core",
    "bot.core.config_manager",
    "bot.helper",
    "bot.helper.telegram_helper",
    "bot.helper.telegram_helper.bot_commands",
    "bot.modules",
    "bot.modules.atri_command_ui",
    "bot.modules.atri_update_idempotency_v1684",
    "bot.modules.atri_unified_menu",
)


def _package(name: str, path: Path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module
    return module


@contextmanager
def _isolated_menu(tmp_path: Path):
    missing = object()
    saved = {
        name: sys.modules.get(name, missing)
        for name in _ISOLATED_MODULES
    }
    for name in _ISOLATED_MODULES:
        sys.modules.pop(name, None)

    try:
        bot = _package("bot", ROOT / "bot")
        bot.LOGGER = _Logger()
        _package("bot.core", ROOT / "bot/core")
        config_module = types.ModuleType("bot.core.config_manager")
        config_module.Config = _Config
        sys.modules[config_module.__name__] = config_module
        _package("bot.helper", ROOT / "bot/helper")
        _package(
            "bot.helper.telegram_helper",
            ROOT / "bot/helper/telegram_helper",
        )
        _package("bot.modules", ROOT / "bot/modules")

        _load_module(
            "bot.helper.telegram_helper.bot_commands",
            ROOT / "bot/helper/telegram_helper/bot_commands.py",
        )
        command_ui = _load_module(
            "bot.modules.atri_command_ui",
            ROOT / "bot/modules/atri_command_ui.py",
        )
        claims = _load_module(
            "bot.modules.atri_update_idempotency_v1684",
            ROOT / "bot/modules/atri_update_idempotency_v1684.py",
        )
        _configure_claims(claims, tmp_path)
        menu = _load_module(
            "bot.modules.atri_unified_menu",
            ROOT / "bot/modules/atri_unified_menu.py",
        )

        # Keep behavior tests focused on dispatch/idempotency. The initial live
        # source scan above remains the evidence for the 174-command invariant.
        menu._refresh_catalog = lambda: None
        yield menu, command_ui
    finally:
        for name in _ISOLATED_MODULES:
            sys.modules.pop(name, None)
        for name, module in saved.items():
            if module is not missing:
                sys.modules[name] = module


class _User:
    id = 42
    is_bot = False


class _Chat:
    type = "private"

    def __init__(self, chat_id: int = 100):
        self.id = chat_id


class _Message:
    def __init__(
        self,
        *,
        update_id: int,
        message_id: int,
        fail_after_side_effect: bool = False,
    ):
        self.update_id = update_id
        self.id = message_id
        self.message_id = message_id
        self.message_thread_id = 0
        self.chat = _Chat()
        self.from_user = _User()
        self.text = "/help"
        self.replies = []
        self.stop_calls = 0
        self.fail_after_side_effect = fail_after_side_effect

    async def reply_text(self, text, **kwargs):
        await asyncio.sleep(0)
        self.replies.append((text, kwargs))
        if self.fail_after_side_effect:
            raise RuntimeError("ambiguous send failure")
        return object()

    def stop_propagation(self):
        self.stop_calls += 1


class _EditableMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        await asyncio.sleep(0)
        self.edits.append((text, kwargs))


class _Query:
    def __init__(
        self,
        *,
        update_id: int,
        query_id: str,
        message: _EditableMessage,
    ):
        self.update_id = update_id
        self.id = query_id
        self.data = "aucm:42:refresh"
        self.from_user = _User()
        self.message = message
        self.answers = 0

    async def answer(self, *_args, **_kwargs):
        self.answers += 1


class _Client:
    def __init__(self):
        self.handlers = []
        self.restarts = 0

    def add_handler(self, handler, group=0):
        self.handlers.append((group, handler))

    async def restart(self):
        self.restarts += 1


def test_v1684_help_dispatch_callback_registry_and_catalog_contract(tmp_path):
    with _isolated_menu(tmp_path) as (menu, command_ui):

        async def scenario():
            single = _Message(update_id=5001, message_id=101)
            await menu.unified_menu_command(None, single)

            replay = _Message(update_id=5002, message_id=102)
            await menu.unified_menu_command(None, replay)
            await menu.unified_menu_command(None, replay)

            concurrent = _Message(update_id=5003, message_id=103)
            await asyncio.gather(
                menu.unified_menu_command(None, concurrent),
                menu.unified_menu_command(None, concurrent),
            )

            first = _Message(update_id=5004, message_id=104)
            second = _Message(update_id=5005, message_id=104)
            await asyncio.gather(
                menu.unified_menu_command(None, first),
                menu.unified_menu_command(None, second),
            )

            retry = _Message(
                update_id=5006,
                message_id=106,
                fail_after_side_effect=True,
            )
            with pytest.raises(
                RuntimeError,
                match="ambiguous send failure",
            ):
                await menu.unified_menu_command(None, retry)
            retry.fail_after_side_effect = False
            await menu.unified_menu_command(None, retry)

            editable = _EditableMessage()
            callback = _Query(
                update_id=6001,
                query_id="callback-6001",
                message=editable,
            )
            await asyncio.gather(
                menu.unified_menu_callback(None, callback),
                menu.unified_menu_callback(None, callback),
            )

            registry = _Client()
            assert menu.add_atri_unified_menu_handlers(registry) is True
            assert menu.add_atri_unified_menu_handlers(registry) is False
            await registry.restart()
            assert menu.add_atri_unified_menu_handlers(registry) is False

            restarted_process_client = _Client()
            assert (
                menu.add_atri_unified_menu_handlers(
                    restarted_process_client
                )
                is True
            )

            return {
                "single": single,
                "replay": replay,
                "concurrent": concurrent,
                "first": first,
                "second": second,
                "retry": retry,
                "callback": callback,
                "editable": editable,
                "registry": registry,
                "restarted": restarted_process_client,
            }

        result = asyncio.run(scenario())

        assert len(result["single"].replies) == 1
        assert len(result["replay"].replies) == 1
        assert len(result["concurrent"].replies) == 1
        assert (
            len(result["first"].replies)
            + len(result["second"].replies)
            == 2
        )
        assert len(result["retry"].replies) == 1
        assert result["callback"].answers == 1
        assert len(result["editable"].edits) == 1

        for key in ("registry", "restarted"):
            handlers = result[key].handlers
            assert len(handlers) == 2
            callbacks = [handler.callback for _, handler in handlers]
            assert callbacks.count(menu.unified_menu_command) == 1
            assert callbacks.count(menu.unified_menu_callback) == 1

        assert result["registry"].restarts == 1
        assert len(command_ui.CATALOG) == 174
        assert sum(
            len(commands)
            for commands in command_ui.CATEGORIES.values()
        ) == 174

        keyboard = menu._root_keyboard(42)
        labels = [
            button.text
            for row in keyboard.inline_keyboard
            for button in row
        ]
        assert labels.count("🔄 Quét lại") == 1

    handlers_source = (
        ROOT / "bot/core/handlers.py"
    ).read_text(encoding="utf-8")
    menu_source = (
        ROOT / "bot/modules/atri_unified_menu.py"
    ).read_text(encoding="utf-8")
    assert "filters=command(BotCommands.HelpCommand" not in handlers_source
    assert '_cmd("help"): "main"' in menu_source


def test_v1684_generic_ai_filter_rejects_help():
    source_path = ROOT / "bot/modules/atri_ai.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    wanted = {
        "_command_name",
        "_command_argument",
        "_matches_command",
        "_is_private",
        "atri_accept_message",
    }
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    namespace = {
        "Config": SimpleNamespace(CMD_SUFFIX="", OWNER_ID=42),
    }
    exec(
        compile(
            ast.Module(body=selected, type_ignores=[]),
            str(source_path),
            "exec",
        ),
        namespace,
    )

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42, is_bot=False),
        text="/help",
        caption="",
        chat=SimpleNamespace(type="private"),
    )
    assert (
        asyncio.run(
            namespace["atri_accept_message"](
                SimpleNamespace(me=None),
                message,
            )
        )
        is False
    )


def test_v1684_worker_singleton_guard_remains_authoritative():
    watchdog = (
        ROOT / "rewrite/supervisor/watchdog.go"
    ).read_text(encoding="utf-8")
    watchdog_test = (
        ROOT / "rewrite/supervisor/watchdog_test.go"
    ).read_text(encoding="utf-8")

    assert "case botLockHeld:" in watchdog
    held = watchdog[watchdog.index("case botLockHeld:"):]
    assert held.index("BOT_SESSION_MISSING_WORKER_ACTIVE") < held.index(
        "recoverVerifiedOrphan"
    )
    assert "TestWatchdogDoesNotDuplicateActiveWorker" in watchdog_test
    assert (
        "worker lock was held but watchdog started a duplicate tmux session"
        in watchdog_test
    )
