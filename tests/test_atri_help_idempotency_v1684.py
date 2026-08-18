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
from pyrogram import StopPropagation


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


def test_v1684_production_shape_identities_are_atomic(tmp_path):
    module = _load_module(
        "atri_update_idempotency_v1684_production_shape_test",
        ROOT / "bot/modules/atri_update_idempotency_v1684.py",
    )
    _configure_claims(module, tmp_path)

    def message(message_id: int):
        update = SimpleNamespace(
            chat=SimpleNamespace(id=100),
            message_thread_id=7,
            id=message_id,
        )
        assert not hasattr(update, "update_id")
        return update

    first, identity = module.claim_telegram_update_once(
        message(4101),
        route="help-message",
    )
    replay, replay_identity = module.claim_telegram_update_once(
        message(4101),
        route="help-message-replay",
    )
    assert first is True
    assert replay is False
    assert identity == replay_identity == "message:100:7:4101"

    message_barrier = threading.Barrier(3)

    def message_contender() -> bool:
        message_barrier.wait(timeout=5)
        return module.claim_telegram_update_once(
            message(4102),
            route="help-message-concurrent",
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(message_contender) for _ in range(2)]
        message_barrier.wait(timeout=5)
        message_results = [
            future.result(timeout=5)
            for future in futures
        ]

    assert sorted(message_results) == [False, True]

    def callback(query_id: str):
        update = SimpleNamespace(
            id=query_id,
            data="aucm:42:refresh",
        )
        assert not hasattr(update, "update_id")
        return update

    first, identity = module.claim_telegram_update_once(
        callback("callback-4101"),
        route="help-callback",
    )
    replay, replay_identity = module.claim_telegram_update_once(
        callback("callback-4101"),
        route="help-callback-replay",
    )
    assert first is True
    assert replay is False
    assert (
        identity
        == replay_identity
        == "callback:callback-4101"
    )

    callback_barrier = threading.Barrier(3)

    def callback_contender() -> bool:
        callback_barrier.wait(timeout=5)
        return module.claim_telegram_update_once(
            callback("callback-4102"),
            route="help-callback-concurrent",
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(callback_contender) for _ in range(2)]
        callback_barrier.wait(timeout=5)
        callback_results = [
            future.result(timeout=5)
            for future in futures
        ]

    assert sorted(callback_results) == [False, True]


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
        message_id: int,
        fail_after_side_effect: bool = False,
    ):
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
        query_id: str,
        message: _EditableMessage,
    ):
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

    def remove_handler(self, handler, group=0):
        self.handlers.remove((group, handler))

    async def restart(self):
        self.restarts += 1


class _PartialFailureClient(_Client):
    def __init__(self):
        super().__init__()
        self.add_calls = 0

    def add_handler(self, handler, group=0):
        self.add_calls += 1
        if self.add_calls == 2:
            raise RuntimeError("injected second-handler failure")
        super().add_handler(handler, group)


def test_v1684_partial_registration_rolls_back_before_retry(tmp_path):
    with _isolated_menu(tmp_path) as (menu, _):
        client = _PartialFailureClient()

        with pytest.raises(
            RuntimeError,
            match="injected second-handler failure",
        ):
            menu.add_atri_unified_menu_handlers(client)

        assert client.handlers == []
        assert menu.add_atri_unified_menu_handlers(client) is True

        callbacks = [
            handler.callback
            for _, handler in client.handlers
        ]
        assert callbacks.count(menu.unified_menu_command) == 1
        assert callbacks.count(menu.unified_menu_callback) == 1


def test_v1684_unified_help_stops_before_legacy_help(tmp_path):
    with _isolated_menu(tmp_path) as (menu, _):
        message = _Message(message_id=99)
        legacy_calls = 0

        def stop_propagation():
            message.stop_calls += 1
            raise StopPropagation

        message.stop_propagation = stop_propagation

        async def legacy_help(_, _message):
            nonlocal legacy_calls
            legacy_calls += 1

        async def dispatch():
            for callback in (
                menu.unified_menu_command,
                legacy_help,
            ):
                try:
                    await callback(None, message)
                except StopPropagation:
                    break

        asyncio.run(dispatch())

        assert len(message.replies) == 1
        assert message.stop_calls == 1
        assert legacy_calls == 0

    handlers_source = (
        ROOT / "bot/core/handlers.py"
    ).read_text(encoding="utf-8")
    assert (
        handlers_source.count(
            "filters=command(BotCommands.HelpCommand"
        )
        == 1
    )


def test_v1684_help_dispatch_callback_registry_and_catalog_contract(tmp_path):
    with _isolated_menu(tmp_path) as (menu, command_ui):

        async def scenario():
            single = _Message(message_id=101)
            await menu.unified_menu_command(None, single)

            replay_first = _Message(message_id=102)
            replay_second = _Message(message_id=102)
            await menu.unified_menu_command(None, replay_first)
            await menu.unified_menu_command(None, replay_second)

            concurrent_first = _Message(message_id=103)
            concurrent_second = _Message(message_id=103)
            await asyncio.gather(
                menu.unified_menu_command(None, concurrent_first),
                menu.unified_menu_command(None, concurrent_second),
            )

            first = _Message(message_id=104)
            second = _Message(message_id=105)
            await asyncio.gather(
                menu.unified_menu_command(None, first),
                menu.unified_menu_command(None, second),
            )

            retry = _Message(
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

            sequential_editable = _EditableMessage()
            sequential_callback_first = _Query(
                query_id="callback-6001",
                message=sequential_editable,
            )
            sequential_callback_second = _Query(
                query_id="callback-6001",
                message=sequential_editable,
            )
            await menu.unified_menu_callback(
                None,
                sequential_callback_first,
            )
            await menu.unified_menu_callback(
                None,
                sequential_callback_second,
            )

            concurrent_editable = _EditableMessage()
            concurrent_callback_first = _Query(
                query_id="callback-6002",
                message=concurrent_editable,
            )
            concurrent_callback_second = _Query(
                query_id="callback-6002",
                message=concurrent_editable,
            )
            await asyncio.gather(
                menu.unified_menu_callback(
                    None,
                    concurrent_callback_first,
                ),
                menu.unified_menu_callback(
                    None,
                    concurrent_callback_second,
                ),
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
                "replay": (replay_first, replay_second),
                "concurrent": (
                    concurrent_first,
                    concurrent_second,
                ),
                "first": first,
                "second": second,
                "retry": retry,
                "sequential_callbacks": (
                    sequential_callback_first,
                    sequential_callback_second,
                ),
                "sequential_editable": sequential_editable,
                "concurrent_callbacks": (
                    concurrent_callback_first,
                    concurrent_callback_second,
                ),
                "concurrent_editable": concurrent_editable,
                "registry": registry,
                "restarted": restarted_process_client,
            }

        result = asyncio.run(scenario())

        assert len(result["single"].replies) == 1
        assert sum(
            len(message.replies)
            for message in result["replay"]
        ) == 1
        assert sum(
            len(message.replies)
            for message in result["concurrent"]
        ) == 1
        assert (
            len(result["first"].replies)
            + len(result["second"].replies)
            == 2
        )
        assert len(result["retry"].replies) == 1
        assert sum(
            query.answers
            for query in result["sequential_callbacks"]
        ) == 1
        assert len(result["sequential_editable"].edits) == 1
        assert sum(
            query.answers
            for query in result["concurrent_callbacks"]
        ) == 1
        assert len(result["concurrent_editable"].edits) == 1

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
    assert "filters=command(BotCommands.HelpCommand" in handlers_source
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
