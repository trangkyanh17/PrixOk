from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable


class RouteConflictError(RuntimeError):
    """Raised when one logical route id is rebound to a different handler."""


@dataclass(frozen=True, slots=True)
class HandlerKey:
    group: int
    handler_type: str
    callback: str


def _callable_name(callback: Any) -> str:
    if isinstance(callback, partial):
        base = _callable_name(callback.func)
        return f"partial:{base}"

    module = getattr(callback, "__module__", callback.__class__.__module__)
    qualname = getattr(
        callback,
        "__qualname__",
        getattr(callback, "__name__", callback.__class__.__qualname__),
    )
    return f"{module}:{qualname}"


def make_handler_key(handler: Any, group: int) -> HandlerKey:
    callback = getattr(handler, "callback", None)
    if callback is None:
        raise TypeError(
            f"handler {handler!r} has no callback attribute; "
            "PrixOk v2 only registers explicit callback handlers"
        )

    handler_type = f"{handler.__class__.__module__}:{handler.__class__.__qualname__}"
    return HandlerKey(
        group=int(group),
        handler_type=handler_type,
        callback=_callable_name(callback),
    )


class HandlerRegistry:
    """Single owner for every Telegram handler registered by the v2 runtime.

    Pyrogram/Kurigram permits the same callback to be registered repeatedly.  The
    legacy runtime accumulated registrations from several bootstrap functions,
    which makes duplicate-response bugs hard to reason about.  This registry
    makes registration idempotent and exposes a deterministic inventory.
    """

    def __init__(self, client: Any, *, logger: Any | None = None) -> None:
        self.client = client
        self.logger = logger
        self._keys: set[HandlerKey] = set()
        self._route_ids: dict[str, HandlerKey] = {}
        self._records: list[tuple[str | None, HandlerKey]] = []

    @property
    def records(self) -> tuple[tuple[str | None, HandlerKey], ...]:
        return tuple(self._records)

    def add(
        self,
        handler: Any,
        *,
        group: int = 0,
        route_id: str | None = None,
    ) -> bool:
        key = make_handler_key(handler, group)

        if route_id:
            current = self._route_ids.get(route_id)
            if current is not None and current != key:
                raise RouteConflictError(
                    f"route id {route_id!r} already owns {current}; refused {key}"
                )

        if key in self._keys:
            if self.logger is not None:
                self.logger.warning(
                    "PRIXOK_V2_HANDLER_DUPLICATE_BLOCKED "
                    "group=%s handler=%s callback=%s route=%s",
                    key.group,
                    key.handler_type,
                    key.callback,
                    route_id or "-",
                )
            if route_id:
                self._route_ids.setdefault(route_id, key)
            return False

        self.client.add_handler(handler, group=int(group))
        self._keys.add(key)
        if route_id:
            self._route_ids[route_id] = key
        self._records.append((route_id, key))

        if self.logger is not None:
            self.logger.info(
                "PRIXOK_V2_HANDLER_REGISTERED "
                "group=%s handler=%s callback=%s route=%s",
                key.group,
                key.handler_type,
                key.callback,
                route_id or "-",
            )
        return True

    def count_callback(self, callback: Callable[..., Any]) -> int:
        name = _callable_name(callback)
        return sum(1 for _, key in self._records if key.callback == name)

    def inventory_lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for route_id, key in self._records:
            lines.append(
                "\t".join(
                    (
                        route_id or "-",
                        str(key.group),
                        key.handler_type,
                        key.callback,
                    )
                )
            )
        return tuple(lines)


class GuardedClient:
    """Small Client facade that forces extension registrars through the registry."""

    __slots__ = ("_registry", "_client")

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry
        self._client = registry.client

    def add_handler(self, handler: Any, group: int = 0):
        installed = self._registry.add(handler, group=group)
        # Pyrogram callers in this project do not use the return value, but
        # returning the usual tuple keeps compatibility with code that does.
        return handler, group if installed else group

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
