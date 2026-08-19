from __future__ import annotations

from bot import LOGGER
from bot.core.config_manager import Config

from .registry import HandlerKey, HandlerRegistry


def _command_name(base: str) -> str:
    suffix = str(getattr(Config, "CMD_SUFFIX", "") or "")
    return f"{base}{suffix}"


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
    """Reject two message handlers claiming the same slash command.

    EditedMessageHandler ownership is intentionally separate; `/shell`, for
    example, may support both a new message and an edited message without two
    handlers processing the same update object.
    """

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
    callback_suffix: str,
) -> None:
    owners = _message_command_owners(registry, command)
    if len(owners) != 1:
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: command "
            f"/{command} expected exactly one message owner, got {len(owners)} "
            f"[{_render_owners(owners)}]"
        )

    _, key = owners[0]
    if not key.callback.endswith(callback_suffix):
        raise RuntimeError(
            "PRIXOK_V2_ROUTE_CONTRACT_FAILED: command "
            f"/{command} owner={key.callback}, expected *{callback_suffix}"
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

    expected = {
        "start": "bot_v2.commands.core:start",
        "log": "bot_v2.commands.core:log",
        "ping": "bot_v2.commands.core:ping",
        "stats": "bot_v2.commands.system:bot_stats",
        "help": "bot.modules.atri_unified_menu:unified_menu_command",
        "menu": "bot.modules.atri_unified_menu:unified_menu_command",
        "amenu": "bot.modules.atri_unified_menu:unified_menu_command",
    }
    for base, callback_suffix in expected.items():
        require_single_message_command_owner(
            registry,
            _command_name(base),
            callback_suffix,
        )

    LOGGER.info(
        "PRIXOK_V2_ROUTE_CONTRACT_PASS explicit_message_commands_unique=1 "
        "start=1 log=1 ping=1 stats=1 help=1 menu=1 amenu=1 "
        "legacy_help=0 legacy_command_center=0 handlers=%s",
        len(registry.records),
    )
