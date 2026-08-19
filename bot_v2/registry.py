from __future__ import annotations

import hashlib
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
    commands: tuple[str, ...]
    filter_fingerprint: str


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


def _object_attrs(value: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    try:
        attrs.update(vars(value))
    except TypeError:
        pass

    for cls in value.__class__.__mro__:
        slots = getattr(cls, "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for slot in slots:
            if slot in {"__dict__", "__weakref__"} or slot in attrs:
                continue
            try:
                attrs[slot] = getattr(value, slot)
            except Exception:
                continue
    return attrs


def _stable_value(value: Any, seen: set[int]) -> str:
    """Build a deterministic structural representation for handler filters."""

    if value is None:
        return "none"
    if isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    if isinstance(value, partial):
        return (
            "partial("
            + _callable_name(value.func)
            + ",args="
            + _stable_value(value.args, seen)
            + ",keywords="
            + _stable_value(value.keywords or {}, seen)
            + ")"
        )
    if callable(value):
        return "callable:" + _callable_name(value)

    marker = id(value)
    if marker in seen:
        return f"cycle:{value.__class__.__module__}:{value.__class__.__qualname__}"

    if isinstance(value, dict):
        seen.add(marker)
        try:
            items = sorted(
                (
                    _stable_value(key, seen),
                    _stable_value(item, seen),
                )
                for key, item in value.items()
            )
            return "dict{" + ",".join(f"{key}:{item}" for key, item in items) + "}"
        finally:
            seen.discard(marker)

    if isinstance(value, (set, frozenset)):
        seen.add(marker)
        try:
            items = sorted(_stable_value(item, seen) for item in value)
            return value.__class__.__name__ + "{" + ",".join(items) + "}"
        finally:
            seen.discard(marker)

    if isinstance(value, (list, tuple)):
        seen.add(marker)
        try:
            items = [_stable_value(item, seen) for item in value]
            return value.__class__.__name__ + "[" + ",".join(items) + "]"
        finally:
            seen.discard(marker)

    attrs = _object_attrs(value)
    class_name = f"{value.__class__.__module__}:{value.__class__.__qualname__}"
    if not attrs:
        return class_name

    seen.add(marker)
    try:
        parts = [
            f"{name}={_stable_value(attrs[name], seen)}"
            for name in sorted(attrs)
            if not name.startswith("__")
        ]
        return class_name + "(" + ",".join(parts) + ")"
    finally:
        seen.discard(marker)


def _filter_fingerprint(handler: Any) -> str:
    value = getattr(handler, "filters", None)
    structural = _stable_value(value, set())
    return hashlib.sha256(structural.encode("utf-8")).hexdigest()[:20]


def _extract_commands(value: Any, seen: set[int] | None = None) -> set[str]:
    """Collect command names embedded in Kurigram/Pyrogram filter graphs."""

    if seen is None:
        seen = set()
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return set()

    marker = id(value)
    if marker in seen:
        return set()
    seen.add(marker)

    try:
        if isinstance(value, dict):
            found: set[str] = set()
            for key, item in value.items():
                found.update(_extract_commands(key, seen))
                found.update(_extract_commands(item, seen))
            return found

        if isinstance(value, (list, tuple, set, frozenset)):
            found: set[str] = set()
            for item in value:
                found.update(_extract_commands(item, seen))
            return found

        attrs = _object_attrs(value)
        found: set[str] = set()
        raw_commands = attrs.get("commands")
        if isinstance(raw_commands, str):
            found.add(raw_commands)
        elif isinstance(raw_commands, (list, tuple, set, frozenset)):
            found.update(
                str(item)
                for item in raw_commands
                if isinstance(item, str) and item
            )

        for name, item in attrs.items():
            if name == "commands":
                continue
            found.update(_extract_commands(item, seen))
        return found
    finally:
        seen.discard(marker)


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
        commands=tuple(sorted(_extract_commands(getattr(handler, "filters", None)))),
        filter_fingerprint=_filter_fingerprint(handler),
    )


class HandlerRegistry:
    """Single owner for every Telegram handler registered by the v2 runtime.

    Registration identity includes group, handler type, callback and filter
    semantics. Exact duplicates are suppressed without deleting a legitimate
    second route that reuses one callback under a different filter. Command
    names are also inventoried so startup contracts can detect dual ownership.
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
                    "group=%s handler=%s callback=%s commands=%s filter=%s route=%s",
                    key.group,
                    key.handler_type,
                    key.callback,
                    ",".join(key.commands) or "-",
                    key.filter_fingerprint,
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
                "group=%s handler=%s callback=%s commands=%s filter=%s route=%s",
                key.group,
                key.handler_type,
                key.callback,
                ",".join(key.commands) or "-",
                key.filter_fingerprint,
                route_id or "-",
            )
        return True

    def count_callback(self, callback: Callable[..., Any]) -> int:
        name = _callable_name(callback)
        return sum(1 for _, key in self._records if key.callback == name)

    def command_owners(self, command: str) -> tuple[tuple[str | None, HandlerKey], ...]:
        target = str(command)
        return tuple(
            (route_id, key)
            for route_id, key in self._records
            if target in key.commands
        )

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
                        ",".join(key.commands) or "-",
                        key.filter_fingerprint,
                    )
                )
            )
        return tuple(lines)


class GuardedClient:
    """Client facade that forces extension registrars through the registry."""

    __slots__ = ("_registry", "_client")

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry
        self._client = registry.client

    def add_handler(self, handler: Any, group: int = 0):
        self._registry.add(handler, group=group)
        return handler, group

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
