from __future__ import annotations

from collections.abc import Iterable

from bot import LOGGER
from bot.core.config_manager import Config
from bot.helper.telegram_helper.bot_commands import BotCommands

from .registry import HandlerKey, HandlerRegistry


def _command_name(base: str) -> str:
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
    return f"{base}{suffix}"


def _command_values(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if str(item))
    return ()


def all_declared_bot_commands() -> tuple[str, ...]:
    commands: set[str] = set()
    for name, value in vars(BotCommands).items():
        if name.endswith("Command"):
            commands.update(_command_values(value))
    return tuple(sorted(commands))


def _message_command_owners(
    registry: HandlerRegistry,
    command: str,
) -> tuple[tuple[str | None, HandlerKey], ...]:
    return tuple(
        (route_id, key)
        for route_id, key in registry.command_owners(command)
        if key.handler_type.endswith(":MessageHandler")
    )


def _render_owners(owners: tuple[tuple[str | None, HandlerKey], ...]) -> str:
    return ", ".join(
        f"{route_id or '-'}:{key.group}:{key.callback}"
        for route_id, key in owners
    ) or "none"


def assert_no_duplicate_message_commands(registry: HandlerRegistry) -> None:
    by_command: dict[str, list[tuple[str | None, HandlerKey]]] = {}
    for route_id, key in registry.records:
        if not key.handler_type.endswith(":MessageHandler"):
            continue
        for command in key.commands:
            by_command.setdefault(command, []).append((route_id, key))

    conflicts = {
        command: tuple(owners)
        for command, owners in by_command.items()
        if len(owners) > 1
    }
    if not conflicts:
        return

    details = "; ".join(
        f"/{command}=[{_render_owners(owners)}]"
        for command, owners in sorted(conflicts.items())
    )
    raise RuntimeError(
        "PRIXOK_V2_ROUTE_CONTRACT_FAILED: duplicate message command ownership: "
        + details
    )


def require_single_message_command_owner(
    registry: HandlerRegistry,
    command: str,
    callback_suffix: str | None = None,
) -> None:
    owners = _message_command_owners(registry, command)
    if len(owners) != 1:
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: command "
            f"/{command} expected exactly one message owner, got {len(owners)} "
            f"[{_render_owners(owners)}]"
        )

    if callback_suffix is None:
        return

    _, key = owners[0]
    if not key.callback.endswith(callback_suffix):
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: command "
            f"/{command} owner={key.callback}, expected *{callback_suffix}"
        )


def _require_values(
    registry: HandlerRegistry,
    values,
    callback_suffix: str,
) -> None:
    for command in _command_values(values):
        require_single_message_command_owner(
            registry,
            command,
            callback_suffix,
        )


def validate_route_contract(registry: HandlerRegistry) -> None:
    callbacks = [key.callback for _, key in registry.records]

    if any(callback.endswith("bot.modules.help:bot_help") for callback in callbacks):
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: legacy bot_help registered; "
            "/help must be owned only by the unified command center"
        )

    if any(
        callback.endswith("bot.modules.atri_command_ui:command_center")
        for callback in callbacks
    ):
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: legacy command_center registered; "
            "/menu and /amenu must be owned only by atri_unified_menu"
        )

    assert_no_duplicate_message_commands(registry)

    declared = all_declared_bot_commands()
    for command in declared:
        require_single_message_command_owner(registry, command)

    explicit = (
        (BotCommands.StartCommand, "bot_v2.commands.core:start"),
        (BotCommands.LogCommand, "bot_v2.commands.core:log"),
        (BotCommands.PingCommand, "bot_v2.commands.core:ping"),
        (BotCommands.RestartCommand, "bot_v2.commands.restart:restart_bot"),
        (BotCommands.StatsCommand, "bot_v2.commands.system:bot_stats"),
        (BotCommands.MirrorCommand, "bot_v2.commands.transfers:mirror"),
        (BotCommands.QbMirrorCommand, "bot_v2.commands.transfers:qb_mirror"),
        (BotCommands.JdMirrorCommand, "bot_v2.commands.transfers:jd_mirror"),
        (BotCommands.NzbMirrorCommand, "bot_v2.commands.transfers:nzb_mirror"),
        (BotCommands.LeechCommand, "bot_v2.commands.transfers:leech"),
        (BotCommands.QbLeechCommand, "bot_v2.commands.transfers:qb_leech"),
        (BotCommands.JdLeechCommand, "bot_v2.commands.transfers:jd_leech"),
        (BotCommands.NzbLeechCommand, "bot_v2.commands.transfers:nzb_leech"),
        (BotCommands.YtdlCommand, "bot_v2.commands.transfers:ytdl"),
        (BotCommands.YtdlLeechCommand, "bot_v2.commands.transfers:ytdl_leech"),
        (BotCommands.GallerydlCommand, "bot_v2.commands.transfers:gallery_dl"),
        (
            BotCommands.GallerydlLeechCommand,
            "bot_v2.commands.transfers:gallery_dl_leech",
        ),
        (BotCommands.MediaDirectCommand, "bot_v2.commands.transfers:media_direct"),
    )
    for values, callback_suffix in explicit:
        _require_values(registry, values, callback_suffix)

    for base in ("help", "menu", "amenu"):
        require_single_message_command_owner(
            registry,
            _command_name(base),
            "bot.modules.atri_unified_menu:unified_menu_command",
        )

    LOGGER.info(
        "PRIXOK_V2_ROUTE_CONTRACT_PASS declared_commands=%s unique=1 "
        "native_restart=1 native_transfers=13 help=1 menu=1 amenu=1 "
        "legacy_help=0 legacy_command_center=0 handlers=%s",
        len(declared),
        len(registry.records),
    )
