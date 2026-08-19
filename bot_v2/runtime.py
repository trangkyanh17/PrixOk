from __future__ import annotations

import fcntl
import os
from asyncio import gather
from pathlib import Path
from typing import Any

from bot import LOGGER, bot_loop
from bot.core.config_manager import Config
from bot.core.telegram_manager import TgClient
from bot.modules.atri_network_egress_guard import install_atri_early_network_guard

from .registry import GuardedClient, HandlerRegistry


DEFAULT_LOCK_PATH = Path("/app/.atri-prixok-bot-v133.lock")
DEFAULT_INVENTORY_PATH = Path("/app/atri_data/prixok_v2_handler_inventory.tsv")


class RuntimeLock:
    """Hold the same singleton lock as production v1 for the whole process.

    v2 must never run beside the legacy Telegram worker. Reusing the existing
    lock path gives a hard mutual-exclusion boundary during migration.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            LOGGER.error(
                "PRIXOK_V2_DUPLICATE_WORKER_BLOCKED pid=%s lock=%s",
                os.getpid(),
                self.path,
            )
            raise SystemExit(73)

        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        self.handle = handle
        LOGGER.info(
            "PRIXOK_V2_SINGLETON_ACQUIRED pid=%s lock=%s",
            os.getpid(),
            self.path,
        )

    def close(self) -> None:
        if self.handle is None:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


async def _start_application_services() -> None:
    from bot.core.jdownloader_booter import jdownloader
    from bot.core.startup import (
        load_configurations,
        load_settings,
        save_settings,
        update_aria2_options,
        update_nzb_options,
        update_qb_options,
        update_variables,
    )
    from bot.core.torrent_manager import TorrentManager
    from bot.helper.ext_utils.files_utils import clean_all
    from bot.helper.ext_utils.telegraph_helper import telegraph
    from bot.helper.mirror_leech_utils.rclone_utils.serve import rclone_serve_booter
    from bot.modules import get_packages_version, initiate_search_tools, restart_notification

    await load_settings()
    await gather(TgClient.start_bot(), TgClient.start_user())
    await gather(load_configurations(), update_variables())

    await TorrentManager.initiate()
    await gather(
        update_qb_options(),
        update_aria2_options(),
        update_nzb_options(),
    )

    await gather(
        save_settings(),
        jdownloader.boot(),
        clean_all(),
        initiate_search_tools(),
        get_packages_version(),
        restart_notification(),
        telegraph.create_account(),
        rclone_serve_booter(),
    )


def _register_extension_handlers(registry: HandlerRegistry) -> None:
    from bot.modules.atri_media_auto import add_atri_media_auto_handlers
    from bot.modules.atri_provider_control import add_atri_provider_control_handlers
    from bot.modules.atri_rose import add_atri_rose_handlers
    from bot.modules.atri_rose_natural import add_atri_rose_natural_handlers
    from bot.modules.atri_skills import add_atri_skills_handlers
    from bot.modules.atri_thinking_control import add_atri_thinking_handlers

    guarded = GuardedClient(registry)
    registrars = (
        ("atri.media_auto", add_atri_media_auto_handlers),
        ("atri.skills", add_atri_skills_handlers),
        ("atri.thinking", add_atri_thinking_handlers),
        ("atri.provider", add_atri_provider_control_handlers),
        ("atri.rose_natural", add_atri_rose_natural_handlers),
        ("atri.rose", add_atri_rose_handlers),
    )

    for name, registrar in registrars:
        before = len(registry.records)
        registrar(guarded)
        added = len(registry.records) - before
        LOGGER.info("PRIXOK_V2_EXTENSION_REGISTERED name=%s added=%s", name, added)


def _write_inventory(registry: HandlerRegistry) -> None:
    target = Path(
        os.environ.get(
            "PRIXOK_V2_HANDLER_INVENTORY",
            str(DEFAULT_INVENTORY_PATH),
        )
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    body = (
        "route_id\tgroup\thandler_type\tcallback\tcommands\t"
        "filter_fingerprint\n"
    )
    lines = registry.inventory_lines()
    if lines:
        body += "\n".join(lines) + "\n"
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    os.chmod(target, 0o600)
    LOGGER.info(
        "PRIXOK_V2_HANDLER_INVENTORY_WRITTEN path=%s handlers=%s",
        target,
        len(registry.records),
    )


def _command_name(base: str) -> str:
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
    return f"{base}{suffix}"


def _require_single_command_owner(
    registry: HandlerRegistry,
    command: str,
    callback_suffix: str,
) -> None:
    owners = registry.command_owners(command)
    if len(owners) != 1:
        rendered = ", ".join(
            f"{route_id or '-'}:{key.group}:{key.callback}"
            for route_id, key in owners
        ) or "none"
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: command "
            f"/{command} expected exactly one owner, got {len(owners)} [{rendered}]"
        )

    _, key = owners[0]
    if not key.callback.endswith(callback_suffix):
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: command "
            f"/{command} owner={key.callback}, expected *{callback_suffix}"
        )


def _validate_route_contract(registry: HandlerRegistry) -> None:
    callbacks = [key.callback for _, key in registry.records]

    legacy_help = [
        callback
        for callback in callbacks
        if callback.endswith("bot.modules.help:bot_help")
    ]
    if legacy_help:
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: legacy bot_help registered; "
            "/help must be owned only by the unified command center"
        )

    command_center_callbacks = [
        callback
        for callback in callbacks
        if callback.endswith("bot.modules.atri_command_ui:command_center")
    ]
    if command_center_callbacks:
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: legacy command_center registered; "
            "/menu and /amenu must be owned only by atri_unified_menu"
        )

    _require_single_command_owner(
        registry,
        _command_name("ping"),
        "bot_v2.commands.core:ping",
    )
    for base in ("help", "menu", "amenu"):
        _require_single_command_owner(
            registry,
            _command_name(base),
            "bot.modules.atri_unified_menu:unified_menu_command",
        )

    LOGGER.info(
        "PRIXOK_V2_ROUTE_CONTRACT_PASS ping=1 help=1 menu=1 amenu=1 "
        "legacy_help=0 legacy_command_center=0 handlers=%s",
        len(registry.records),
    )


async def bootstrap() -> HandlerRegistry:
    """Boot business services and install the v2 Telegram route graph once."""

    install_atri_early_network_guard()
    await _start_application_services()

    from bot.helper.ext_utils.bot_utils import create_help_buttons
    from bot.helper.listeners.aria2_listener import add_aria2_callbacks
    from bot.modules.atri_capability_bootstrap import (
        add_capability_runtime_handlers,
        install_capability_runtime,
    )
    from bot.modules.atri_free_tools import start_free_tools
    from bot.modules.atri_message_idempotency_v1672 import (
        install_atri_message_idempotency_v1672,
    )
    from bot.modules.atri_network_egress_guard import install_atri_network_egress_guard
    from bot.modules.atri_response_engine import install_atri_natural_response_engine
    from bot.modules.atri_response_output_guard_v1673 import (
        install_atri_response_output_guard_v1673,
    )
    from bot.modules.atri_system_guard import install_atri_system_post_import_guard
    from bot.modules.atri_v150_shadow import add_v150_shadow_handlers

    add_aria2_callbacks()
    create_help_buttons()
    install_atri_network_egress_guard()
    install_capability_runtime()
    install_atri_natural_response_engine()
    install_atri_message_idempotency_v1672()
    install_atri_response_output_guard_v1673()

    if TgClient.bot is None:
        raise RuntimeError("PRIXOK_V2_BOOT_FAILED: Telegram bot client is not started")

    registry = HandlerRegistry(TgClient.bot, logger=LOGGER)
    guarded = GuardedClient(registry)

    _register_extension_handlers(registry)

    # Import after all runtime patch layers so these modules capture patched
    # Atri callback aliases rather than stale pre-patch references.
    from .atri_routes import (
        register_atri_command_ui_routes,
        register_atri_unified_menu_routes,
    )
    from .routes import register_core_routes

    register_atri_unified_menu_routes(registry)
    register_atri_command_ui_routes(registry)
    register_core_routes(registry)
    bot_loop.create_task(start_free_tools(guarded), name="prixok-v2-free-tools")
    add_capability_runtime_handlers(guarded)
    install_atri_system_post_import_guard()
    add_v150_shadow_handlers(guarded)

    from bot.modules.atri_runtime_hardening_v1671 import (
        install_atri_runtime_hardening_v1671,
    )

    install_atri_runtime_hardening_v1671()

    _validate_route_contract(registry)
    _write_inventory(registry)

    from bot.modules.atri_tools.code_plugins import (
        prewarm_remaining_code_plugins,
        prewarm_semgrep_mcp,
    )

    bot_loop.create_task(
        prewarm_semgrep_mcp(),
        name="prixok-v2-semgrep-prewarm",
    )
    bot_loop.create_task(
        prewarm_remaining_code_plugins(),
        name="prixok-v2-mcp-prewarm",
    )

    LOGGER.info(
        "PRIXOK_PYTHON_V2_ONLINE pid=%s handlers=%s client=%s dispatcher=%s",
        os.getpid(),
        len(registry.records),
        id(TgClient.bot),
        id(getattr(TgClient.bot, "dispatcher", None)),
    )
    return registry


def run() -> None:
    lock_path = Path(os.environ.get("PRIXOK_RUNTIME_LOCK", str(DEFAULT_LOCK_PATH)))
    lock = RuntimeLock(lock_path)
    lock.acquire()

    try:
        Config.load()
        bot_loop.run_until_complete(bootstrap())
        bot_loop.run_forever()
    except KeyboardInterrupt:
        LOGGER.info("PRIXOK_V2_SHUTDOWN keyboard_interrupt=1")
    finally:
        try:
            if not bot_loop.is_closed():
                bot_loop.run_until_complete(TgClient.stop())
        except Exception:
            LOGGER.exception("PRIXOK_V2_CLIENT_STOP_FAILED")
        lock.close()
