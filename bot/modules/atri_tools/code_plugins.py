from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


UVX = "/app/mltbenv/bin/uvx"
PROJECT_ROOT = os.getenv("ATRI_CODE_PROJECT_ROOT", "/app")
TIMEOUT = max(30, int(os.getenv("ATRI_CODE_PLUGIN_TIMEOUT", "180")))
ALLOW_WRITE = os.getenv("ATRI_CODE_PLUGINS_ALLOW_WRITE", "0") == "1"
TOOL_LIST_CACHE_TTL = max(
    30.0,
    float(os.getenv("ATRI_CODE_PLUGIN_TOOL_CACHE_TTL", "600")),
)
CONTEXT7_LIBRARY_CACHE_TTL = max(
    300.0,
    float(os.getenv("ATRI_CONTEXT7_LIBRARY_CACHE_TTL", "21600")),
)

_tool_list_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_tool_list_locks: dict[str, asyncio.Lock] = {}
_context7_library_cache: dict[str, tuple[float, str]] = {}

PLUGIN_NAMES = (
    "serena",
    "context7",
    "github",
    "semgrep",
    "sentry",
    "chrome-devtools",
)

PLUGIN_HINTS = {
    "serena": (
        "source", "codebase", "symbol", "reference", "refactor",
        "file", "class", "function",
    ),
    "context7": (
        "docs", "documentation", "library", "framework", "api",
        "version", "example",
    ),
    "github": (
        "github", "repository", "repo", "issue", "pull request",
        "commit", "actions", "release",
    ),
    "semgrep": (
        "security", "vulnerability", "scan", "sast", "secret",
        "unsafe", "cve",
    ),
    "sentry": (
        "sentry", "production", "exception", "trace", "event",
        "runtime error", "crash",
    ),
    "chrome-devtools": (
        "browser", "chrome", "dom", "console", "network",
        "lighthouse", "performance", "web page",
    ),
}


def _github_token() -> str:
    return (
        os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )


def _specs() -> dict[str, dict[str, Any]]:
    context7_headers = {}
    context7_key = os.getenv("CONTEXT7_API_KEY", "").strip()
    if context7_key:
        context7_headers["CONTEXT7_API_KEY"] = context7_key

    github_headers = {
        "X-MCP-Readonly": "true",
        "X-MCP-Toolsets": (
            "repos,issues,pull_requests,actions,code_security"
        ),
    }
    token = _github_token()
    if token:
        github_headers["Authorization"] = f"Bearer {token}"

    return {
        "serena": {
            "transport": "stdio",
            "command": UVX,
            "args": [
                "--from",
                "git+https://github.com/oraios/serena",
                "serena",
                "start-mcp-server",
                "--context",
                "agent",
                "--project",
                PROJECT_ROOT,
            ],
            "description": "Semantic source/codebase understanding.",
        },
        "context7": {
            "transport": "http",
            "url": "https://mcp.context7.com/mcp",
            "headers": context7_headers,
            "description": "Current library/API documentation.",
        },
        "github": {
            "transport": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": github_headers,
            "requires": "github_token",
            "description": "GitHub repositories, issues, PRs and Actions.",
        },
        "semgrep": {
            "transport": "stdio",
            "command": UVX,
            "args": ["-p", "3.13", "semgrep", "mcp"],
            "description": "Static/security analysis with Semgrep.",
        },
        "sentry": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@sentry/mcp-server@latest"],
            "requires": "sentry_token",
            "description": "Production errors, events and traces.",
        },
        "chrome-devtools": {
            "transport": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "chrome-devtools-mcp@latest",
                "--headless",
                "--isolated",
                "--executable-path=/usr/bin/google-chrome",
                "--redact-network-headers",
                "--chrome-arg=--no-sandbox",
                "--chrome-arg=--disable-dev-shm-usage",
            ],
            "requires": "browser",
            "description": "Browser DOM/network/console/performance debugging.",
        },
    }


def _browser() -> str:
    for name in (
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(name)
        if path:
            return path
    return ""


def _availability(name: str) -> tuple[bool, str]:
    spec = _specs().get(name)
    if not spec:
        return False, "unknown plugin"

    requirement = spec.get("requires")

    if requirement == "github_token" and not _github_token():
        return False, "missing GITHUB_PERSONAL_ACCESS_TOKEN/GITHUB_TOKEN"

    if requirement == "sentry_token" and not os.getenv(
        "SENTRY_ACCESS_TOKEN", ""
    ).strip():
        return False, "missing SENTRY_ACCESS_TOKEN"

    if requirement == "browser" and not _browser():
        return False, "Chrome/Chromium is not installed"

    if spec["transport"] == "stdio":
        command = spec["command"]
        if os.path.isabs(command):
            exists = os.path.isfile(command)
        else:
            exists = bool(shutil.which(command))
        if not exists:
            return False, f"command not found: {command}"

    return True, "ready"


@asynccontextmanager
async def _session(plugin: str):
    spec = _specs()[plugin]

    if spec["transport"] == "stdio":
        env = dict(os.environ)

        params = StdioServerParameters(
            command=spec["command"],
            args=spec.get("args", []),
            env=env,
        )

        async with stdio_client(params) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    headers = spec.get("headers") or {}

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(TIMEOUT),
        follow_redirects=True,
    ) as client:
        async with streamable_http_client(
            spec["url"],
            http_client=client,
        ) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


# Semgrep MCP is expensive to cold-start. Keep one stdio session owned by
# one background task so list_tools -> call_tool can reuse the same process.
_semgrep_worker_queue = None
_semgrep_worker_task = None
_semgrep_worker_guard = None

SEMGREP_MCP_IDLE_SECONDS = max(
    60,
    int(os.getenv("ATRI_SEMGREP_MCP_IDLE_SECONDS", "3600")),
)


async def _ensure_semgrep_worker() -> None:
    global _semgrep_worker_queue
    global _semgrep_worker_task
    global _semgrep_worker_guard

    if _semgrep_worker_guard is None:
        _semgrep_worker_guard = asyncio.Lock()

    async with _semgrep_worker_guard:
        if (
            _semgrep_worker_task is not None
            and not _semgrep_worker_task.done()
        ):
            return

        _semgrep_worker_queue = asyncio.Queue()
        _semgrep_worker_task = asyncio.create_task(
            _semgrep_worker(),
            name="atri-semgrep-mcp-worker",
        )


async def _semgrep_worker() -> None:
    from bot import LOGGER

    while True:
        try:
            async with _session("semgrep") as session:
                LOGGER.info("SEMGREP_MCP_WARM_READY")

                while True:
                    try:
                        item = await asyncio.wait_for(
                            _semgrep_worker_queue.get(),
                            timeout=SEMGREP_MCP_IDLE_SECONDS,
                        )
                    except TimeoutError:
                        LOGGER.info(
                            "SEMGREP_MCP_WARM_IDLE_CLOSE seconds=%s",
                            SEMGREP_MCP_IDLE_SECONDS,
                        )
                        return

                    operation, payload, future = item

                    if future.done():
                        continue

                    try:
                        if operation == "list_tools":
                            result = await session.list_tools()

                        elif operation == "call_tool":
                            result = await session.call_tool(
                                payload["tool"],
                                arguments=payload.get("arguments") or {},
                            )

                        else:
                            raise RuntimeError(
                                f"Unknown Semgrep worker operation: "
                                f"{operation}"
                            )

                    except asyncio.CancelledError:
                        raise

                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)

                        # Reconnect the MCP process before serving the next
                        # queued operation.
                        LOGGER.warning(
                            "SEMGREP_MCP_WARM_RECONNECT reason=%s: %s",
                            type(exc).__name__,
                            exc,
                        )
                        break

                    else:
                        if not future.done():
                            future.set_result(result)

        except asyncio.CancelledError:
            raise

        except Exception:
            LOGGER.warning(
                "SEMGREP_MCP_WARM_START_FAILED",
                exc_info=True,
            )
            await asyncio.sleep(1)


async def _semgrep_request(
    operation: str,
    *,
    tool: str = "",
    arguments: dict[str, Any] | None = None,
) -> Any:
    await _ensure_semgrep_worker()

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    await _semgrep_worker_queue.put(
        (
            operation,
            {
                "tool": tool,
                "arguments": arguments or {},
            },
            future,
        )
    )

    return await future


def _resolve_local_json_schema_refs(schema: Any) -> Any:
    """Resolve local #/$defs/... JSON-Schema references for Vertex safety.

    Some MCP servers, notably Semgrep, expose input schemas containing local
    $ref values. Vertex rejects those refs when the discovery result is later
    returned inside functionResponse.response.
    """
    if not isinstance(schema, dict):
        return schema

    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        defs = {}

    def resolve(value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [resolve(item, stack) for item in value]

        if not isinstance(value, dict):
            return value

        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref[len("#/$defs/"):]
            target = defs.get(name)

            if isinstance(target, dict) and name not in stack:
                resolved = resolve(target, stack + (name,))

                extras = {
                    key: resolve(item, stack)
                    for key, item in value.items()
                    if key != "$ref"
                }

                if isinstance(resolved, dict):
                    merged = dict(resolved)
                    merged.update(extras)
                    return merged

        result = {}

        for key, item in value.items():
            if key == "$defs":
                continue

            # Never propagate unresolved local JSON-Schema refs to Vertex.
            if key == "$ref" and isinstance(item, str) and item.startswith("#/$defs/"):
                result["description"] = (
                    result.get("description")
                    or f"Schema reference: {item[len('#/$defs/'):]}"
                )
                continue

            result[key] = resolve(item, stack)

        return result

    return resolve(schema)



def _sanitize_mcp_schema_for_vertex(schema: Any) -> Any:
    """Normalize MCP JSON Schema before exposing it through tool discovery."""

    def to_plain(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump(mode="json", by_alias=True)
            except Exception:
                pass

        if hasattr(value, "dict"):
            try:
                return value.dict()
            except Exception:
                pass

        return value

    schema = to_plain(schema)

    if not isinstance(schema, dict):
        return {}

    root = schema

    def resolve_pointer(ref: str) -> Any:
        if not ref.startswith("#/"):
            return None

        current: Any = root

        try:
            for token in ref[2:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")

                current = to_plain(current)

                if not isinstance(current, dict):
                    return None

                current = current[token]

            return current

        except (KeyError, TypeError):
            return None

    def clean(value: Any, seen: frozenset[int] = frozenset()) -> Any:
        value = to_plain(value)

        if value is None or isinstance(value, (bool, int, float)):
            return value

        if isinstance(value, str):
            # Vertex interprets strings such as "#/$defs/CodeFile"
            # specially inside functionResponse.response.
            return value.replace("#/$defs/", "schema:")

        if isinstance(value, (list, tuple, set)):
            return [clean(item, seen) for item in value]

        if not isinstance(value, dict):
            return str(value)

        ref = value.get("$ref")

        if isinstance(ref, str):
            target = resolve_pointer(ref)

            if isinstance(target, dict) and id(target) not in seen:
                merged = dict(target)

                for key, item in value.items():
                    if key != "$ref":
                        merged[key] = item

                return clean(
                    merged,
                    seen | {id(target)},
                )

        result: dict[str, Any] = {}

        for key, item in value.items():
            key = str(key)

            # $defs/$ref/$schema/... are discovery implementation
            # details and must never reach Vertex functionResponse.
            if key.startswith("$"):
                continue

            result[key] = clean(item, seen)

        return result

    cleaned = clean(schema)

    return cleaned if isinstance(cleaned, dict) else {}


def _tool_dict(plugin: str, tool: Any) -> dict[str, Any]:
    raw_schema = getattr(tool, "input_schema", None)

    # Compatibility fallback for MCP SDK variants exposing the wire alias.
    if raw_schema is None:
        raw_schema = getattr(tool, "inputSchema", None)

    schema = _sanitize_mcp_schema_for_vertex(raw_schema)
    if schema is None:
        schema = getattr(tool, "input_schema", None)

    return {
        "plugin": plugin,
        "name": str(getattr(tool, "name", "")),
        "description": str(getattr(tool, "description", "") or ""),
        "input_schema": schema or {},
    }


async def _list_tools(plugin: str) -> list[dict[str, Any]]:
    ready, reason = _availability(plugin)
    if not ready:
        raise RuntimeError(reason)

    now = time.monotonic()
    cached = _tool_list_cache.get(plugin)
    if cached and now - cached[0] < TOOL_LIST_CACHE_TTL:
        return cached[1]

    lock = _tool_list_locks.setdefault(plugin, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        cached = _tool_list_cache.get(plugin)
        if cached and now - cached[0] < TOOL_LIST_CACHE_TTL:
            return cached[1]

        async with asyncio.timeout(TIMEOUT):
            if plugin == "semgrep":
                response = await _semgrep_request("list_tools")
            else:
                async with _session(plugin) as session:
                    response = await session.list_tools()

            tools = [_tool_dict(plugin, x) for x in response.tools]

        _tool_list_cache[plugin] = (time.monotonic(), tools)
        return tools


# ATRI_PERSISTENT_MCP_POOL_V1
#
# Keep expensive MCP transports alive between discovery/calls.
# Semgrep keeps its already-verified dedicated worker.
_PERSISTENT_MCP_PLUGINS = frozenset(
    {
        "context7",
        "github",
        "chrome-devtools",
        "sentry",
        "serena",
    }
)

# Tool discovery is effectively static during one bot deployment.
TOOL_LIST_CACHE_TTL = max(
    TOOL_LIST_CACHE_TTL,
    int(
        os.getenv(
            "ATRI_CODE_PLUGIN_WARM_CACHE_TTL",
            "3600",
        )
    ),
)

_persistent_mcp_queues: dict[str, asyncio.Queue] = {}
_persistent_mcp_tasks: dict[str, asyncio.Task] = {}
_persistent_mcp_guards: dict[str, asyncio.Lock] = {}


async def _persistent_mcp_worker(
    plugin: str,
    queue: asyncio.Queue,
) -> None:
    from bot import LOGGER

    while True:
        try:
            async with _session(plugin) as session:
                LOGGER.info(
                    "MCP_WARM_READY plugin=%s transport=%s",
                    plugin,
                    _specs()[plugin].get("transport"),
                )

                while True:
                    operation, payload, future = await queue.get()

                    if future.done():
                        continue

                    try:
                        if operation == "list_tools":
                            result = await session.list_tools()

                        elif operation == "call_tool":
                            result = await session.call_tool(
                                payload["tool"],
                                arguments=payload.get("arguments") or {},
                            )

                        else:
                            raise RuntimeError(
                                f"Unknown persistent MCP operation: "
                                f"{operation}"
                            )

                    except asyncio.CancelledError:
                        raise

                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)

                        LOGGER.warning(
                            "MCP_WARM_RECONNECT plugin=%s reason=%s: %s",
                            plugin,
                            type(exc).__name__,
                            exc,
                        )

                        # Re-open the transport while keeping pending queue.
                        await asyncio.sleep(0.25)
                        break

                    else:
                        if not future.done():
                            future.set_result(result)

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            LOGGER.warning(
                "MCP_WARM_START_FAILED plugin=%s reason=%s: %s",
                plugin,
                type(exc).__name__,
                exc,
                exc_info=True,
            )

            # Fail pending work. A later request may create a fresh worker.
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                _, _, future = item
                if not future.done():
                    future.set_exception(exc)

            return


async def _persistent_mcp_request(
    plugin: str,
    operation: str,
    *,
    tool: str = "",
    arguments: dict[str, Any] | None = None,
) -> Any:
    if plugin not in _PERSISTENT_MCP_PLUGINS:
        raise RuntimeError(
            f"Plugin does not use persistent MCP pool: {plugin}"
        )

    guard = _persistent_mcp_guards.get(plugin)

    if guard is None:
        guard = asyncio.Lock()
        _persistent_mcp_guards[plugin] = guard

    loop = asyncio.get_running_loop()

    async with guard:
        task = _persistent_mcp_tasks.get(plugin)

        if task is None or task.done():
            queue: asyncio.Queue = asyncio.Queue()

            _persistent_mcp_queues[plugin] = queue

            task = asyncio.create_task(
                _persistent_mcp_worker(
                    plugin,
                    queue,
                ),
                name=f"atri-mcp-warm-{plugin}",
            )

            _persistent_mcp_tasks[plugin] = task

        queue = _persistent_mcp_queues[plugin]
        future = loop.create_future()

        queue.put_nowait(
            (
                operation,
                {
                    "tool": tool,
                    "arguments": arguments or {},
                },
                future,
            )
        )

    return await future


class _PersistentMcpSessionProxy:
    def __init__(self, plugin: str):
        self.plugin = plugin

    async def call_tool(
        self,
        tool: str,
        *,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        if self.plugin == "semgrep":
            return await _semgrep_request(
                "call_tool",
                tool=tool,
                arguments=arguments or {},
            )

        return await _persistent_mcp_request(
            self.plugin,
            "call_tool",
            tool=tool,
            arguments=arguments or {},
        )


@asynccontextmanager
async def _optimized_batch_session(plugin: str):
    if (
        plugin == "semgrep"
        or plugin in _PERSISTENT_MCP_PLUGINS
    ):
        yield _PersistentMcpSessionProxy(plugin)
        return

    async with _session(plugin) as session:
        yield session


# Override tool discovery after the original implementation so all consumers
# automatically reuse warm transports.
async def _list_tools(plugin: str) -> list[dict[str, Any]]:
    ready, reason = _availability(plugin)

    if not ready:
        raise RuntimeError(reason)

    now = time.monotonic()
    cached = _tool_list_cache.get(plugin)

    if cached and now - cached[0] < TOOL_LIST_CACHE_TTL:
        return cached[1]

    lock = _tool_list_locks.setdefault(
        plugin,
        asyncio.Lock(),
    )

    async with lock:
        now = time.monotonic()
        cached = _tool_list_cache.get(plugin)

        if cached and now - cached[0] < TOOL_LIST_CACHE_TTL:
            return cached[1]

        async with asyncio.timeout(TIMEOUT):
            if plugin == "semgrep":
                response = await _semgrep_request(
                    "list_tools"
                )

            elif plugin in _PERSISTENT_MCP_PLUGINS:
                response = await _persistent_mcp_request(
                    plugin,
                    "list_tools",
                )

            else:
                async with _session(plugin) as session:
                    response = await session.list_tools()

            tools = [
                _tool_dict(plugin, item)
                for item in response.tools
            ]

        _tool_list_cache[plugin] = (
            time.monotonic(),
            tools,
        )

        return tools


async def prewarm_remaining_code_plugins() -> None:
    """Warm all non-Semgrep coding MCPs after bot startup."""

    from bot import LOGGER

    # Let core Telegram/startup work settle first.
    await asyncio.sleep(1.0)

    names = [
        "serena",
        "sentry",
        "github",
        "chrome-devtools",
        "context7",
    ]

    # Avoid 5 package/server cold-starts hammering the host simultaneously.
    semaphore = asyncio.Semaphore(2)

    async def warm(plugin: str) -> None:
        ready, reason = _availability(plugin)

        if not ready:
            LOGGER.info(
                "MCP_BOOT_PREWARM_SKIP plugin=%s reason=%s",
                plugin,
                reason,
            )
            return

        started = time.monotonic()

        try:
            async with semaphore:
                tools = await _list_tools(plugin)

        except asyncio.CancelledError:
            raise

        except Exception:
            LOGGER.warning(
                "MCP_BOOT_PREWARM_FAILED plugin=%s",
                plugin,
                exc_info=True,
            )
            return

        LOGGER.info(
            "MCP_BOOT_PREWARM_READY "
            "plugin=%s tools=%s elapsed_ms=%s",
            plugin,
            len(tools),
            int(
                (time.monotonic() - started)
                * 1000
            ),
        )

    await asyncio.gather(
        *(warm(name) for name in names)
    )

    LOGGER.info("MCP_BOOT_PREWARM_ALL_DONE")


async def prewarm_semgrep_mcp() -> None:
    """Warm Semgrep MCP + discovery cache without blocking the caller."""

    from bot import LOGGER

    try:
        started = time.monotonic()

        tools = await _list_tools("semgrep")

        LOGGER.info(
            "SEMGREP_MCP_PREWARM_READY tools=%s elapsed_ms=%s",
            len(tools),
            int((time.monotonic() - started) * 1000),
        )

    except asyncio.CancelledError:
        raise

    except Exception:
        # Prewarm is opportunistic only. Normal discovery/call path remains
        # authoritative and can retry/reconnect on demand.
        LOGGER.warning(
            "SEMGREP_MCP_PREWARM_FAILED",
            exc_info=True,
        )


def _selected_plugins(query: str) -> list[str]:
    text = query.casefold()

    explicit = [
        name for name in PLUGIN_NAMES
        if name in text
    ]
    if explicit:
        return explicit[:1]

    selected = [
        name
        for name, hints in PLUGIN_HINTS.items()
        if any(hint in text for hint in hints)
    ]

    if selected:
        return selected[:3]

    return ["serena", "context7", "semgrep"]


async def code_plugin_search(
    query: str,
    plugin: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    query = str(query or "").strip()
    plugin = str(plugin or "").strip().casefold()
    limit = max(1, min(int(limit or 12), 30))

    names = [plugin] if plugin else _selected_plugins(query)
    found: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    fallback_plugins: list[str] = []

    async def _load_plugin_tools(
        name: str,
    ) -> tuple[str, list[dict[str, Any]], str]:
        if name not in PLUGIN_NAMES:
            return name, [], "unknown plugin"

        try:
            tools = await _list_tools(name)
        except Exception as exc:
            return name, [], f"{type(exc).__name__}: {exc}"

        allowed = [
            tool
            for tool in tools
            if _plugin_tool_allowed(name, tool["name"])
        ]
        return name, allowed, ""

    # Plugin discovery sessions are independent: run them concurrently.
    loaded = await asyncio.gather(
        *(_load_plugin_tools(name) for name in names)
    )

    terms = [
        term
        for term in query.casefold().split()
        if len(term) >= 2
    ]

    for name, tools, error in loaded:
        if error:
            errors[name] = error
            continue

        scored: list[tuple[int, dict[str, Any]]] = []

        for tool in tools:
            haystack = (
                tool["name"] + " " + tool["description"]
            ).casefold()

            score = sum(
                1 for term in terms
                if term in haystack
            )

            if score:
                scored.append((score, tool))

        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            found.extend(tool for _, tool in scored)
            continue

        # A topic query such as "httpx AsyncClient ReadTimeout"
        # describes what the user wants to research, not the MCP tool name.
        # Once routing has narrowed to one plugin, returning that plugin's
        # available read-only tools is more useful than a false "no match".
        if len(names) == 1:
            found.extend(tools)
            fallback_plugins.append(name)

    result = {
        "ok": bool(found),
        "query": query,
        "tools": found[:limit],
        "errors": errors,
        "fallback_plugins": fallback_plugins,
    }

    # Final safety boundary: nothing returned by MCP discovery may expose
    # JSON-Schema $ref/$defs metadata to Vertex functionResponse.
    return _sanitize_mcp_schema_for_vertex(result)


SENSITIVE_PATH_MARKERS = (
    "vertex-service-account.json",
    "rclone.conf",
    "config.py",
    ".env",
    "credentials",
    "client_secret",
    "service-account",
    "service_account",
    "private_key",
    "token.json",
)


def _contains_sensitive_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_sensitive_path(k) or _contains_sensitive_path(v)
            for k, v in value.items()
        )

    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_path(x) for x in value)

    text = str(value or "").casefold()
    return any(marker in text for marker in SENSITIVE_PATH_MARKERS)


PLUGIN_BLOCKED_TOOLS = {
    "sentry": {
        "update_issue",
        "analyze_issue_with_seer",
        "search_sentry_tools",
        "execute_sentry_tool",
    },
}


def _plugin_tool_allowed(plugin: str, tool: str) -> bool:
    return tool not in PLUGIN_BLOCKED_TOOLS.get(plugin, set())


_WRITE_MARKERS = (
    "delete",
    "remove",
    "write",
    "create",
    "update",
    "edit",
    "replace",
    "insert",
    "execute_shell",
    "push",
    "merge",
    "upload",
)


def _write_allowed(tool: str) -> bool:
    if ALLOW_WRITE:
        return True

    value = tool.casefold()
    return not any(marker in value for marker in _WRITE_MARKERS)


def _normalize_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)

    blocks = []

    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)

        if text is not None:
            blocks.append(text)
        elif hasattr(block, "model_dump"):
            blocks.append(
                block.model_dump(mode="json", by_alias=True)
            )
        else:
            blocks.append(str(block))

    is_error = getattr(result, "isError", None)
    if is_error is None:
        is_error = getattr(result, "is_error", False)

    return {
        "ok": not bool(is_error),
        "content": blocks,
        "structured": structured,
    }


# ATRI_DIRECT_PLUGIN_FASTPATH_V1

_DIRECT_PLUGIN_ALIASES = (
    ("chrome devtools", "chrome-devtools"),
    ("chrome-devtools", "chrome-devtools"),
    ("context 7", "context7"),
    ("context7", "context7"),
    ("github mcp", "github"),
    ("github", "github"),
    ("semgrep", "semgrep"),
    ("serena", "serena"),
    ("sentry", "sentry"),
)


def _explicit_direct_plugin(query: str) -> str:
    text = str(query or "").casefold()

    normalized = (
        text
        .replace("_", " ")
        .replace("/", " ")
    )

    for alias, plugin in _DIRECT_PLUGIN_ALIASES:
        if alias in normalized:
            return plugin

    return ""


def _compact_fastpath_args(
    schema: dict[str, Any] | None,
) -> str:
    if not isinstance(schema, dict):
        return "none"

    properties = schema.get("properties") or {}

    if not isinstance(properties, dict):
        return "none"

    required = {
        str(item)
        for item in (
            schema.get("required") or []
        )
    }

    items = []

    for name, metadata in properties.items():
        name = str(name)

        if isinstance(metadata, dict):
            value_type = str(
                metadata.get("type")
                or "any"
            )

            enum = metadata.get("enum")

            if isinstance(enum, list) and enum:
                choices = ",".join(
                    str(x)[:40]
                    for x in enum[:8]
                )
                value_type += f"[{choices}]"
        else:
            value_type = "any"

        suffix = (
            ":required"
            if name in required
            else ":optional"
        )

        items.append(
            f"{name}<{value_type}>{suffix}"
        )

    return ", ".join(items) or "none"


async def build_direct_plugin_fastpath_context(
    query: str,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """Build a compact cached MCP catalog for explicit plugin requests."""

    plugin = _explicit_direct_plugin(query)

    if not plugin:
        return {
            "ok": False,
            "plugin": "",
            "context": "",
            "tool_count": 0,
        }

    ready, reason = _availability(plugin)

    if not ready:
        return {
            "ok": False,
            "plugin": plugin,
            "context": "",
            "tool_count": 0,
            "error": reason,
        }

    limit = max(
        1,
        min(int(limit or 10), 16),
    )

    # Reuse existing discovery logic. Tool list is already cached/warm.
    result = await code_plugin_search(
        query=query,
        plugin=plugin,
        limit=max(limit, 12),
    )

    raw_tools = result.get("tools") or []

    safe_tools = []

    for tool in raw_tools:
        if not isinstance(tool, dict):
            continue

        name = str(tool.get("name") or "")

        if not name:
            continue

        # Do not advertise write-capable tools when write mode is disabled.
        if not _write_allowed(name):
            continue

        if not _plugin_tool_allowed(
            plugin,
            name,
        ):
            continue

        safe_tools.append(tool)

        if len(safe_tools) >= limit:
            break

    if not safe_tools:
        return {
            "ok": False,
            "plugin": plugin,
            "context": "",
            "tool_count": 0,
            "error": "No safe direct-call tools found",
        }

    lines = [
        "DIRECT MCP TOOL CATALOG",
        f"Plugin explicitly requested: {plugin}",
        (
            "The catalog has already been resolved by the backend. "
            "DO NOT call code_plugin_search for this plugin. "
            "Call code_plugin_call directly with plugin="
            f"{plugin} and the appropriate tool."
        ),
        "Available safe tools:",
    ]

    for tool in safe_tools:
        name = str(tool.get("name") or "")
        description = " ".join(
            str(
                tool.get("description")
                or ""
            ).split()
        )[:220]

        args = _compact_fastpath_args(
            tool.get("input_schema")
        )

        lines.append(
            f"- {name} | args: {args}"
            + (
                f" | {description}"
                if description
                else ""
            )
        )

    return {
        "ok": True,
        "plugin": plugin,
        "tool_count": len(safe_tools),
        "context": "\n".join(lines),
    }


async def code_plugin_call(
    plugin: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # PERSISTENT_MCP_CALL_V1
    plugin = str(plugin or "").strip().casefold()
    tool = str(tool or "").strip()

    from bot import LOGGER

    LOGGER.info(
        "CODE_PLUGIN_CALL plugin=%s tool=%s",
        plugin,
        tool,
    )

    if plugin not in PLUGIN_NAMES:
        return {
            "ok": False,
            "error": f"Unknown plugin: {plugin}",
        }

    ready, reason = _availability(plugin)

    if not ready:
        return {
            "ok": False,
            "error": reason,
        }

    if not _plugin_tool_allowed(plugin, tool):
        return {
            "ok": False,
            "error": (
                f"Tool blocked by read-only policy: "
                f"{plugin}/{tool}"
            ),
        }

    if _contains_sensitive_path(arguments or {}):
        return {
            "ok": False,
            "error": (
                "Access to sensitive credential/config "
                "paths is blocked."
            ),
        }

    if not _write_allowed(tool):
        return {
            "ok": False,
            "error": (
                "Write-capable MCP tool blocked. "
                "Set ATRI_CODE_PLUGINS_ALLOW_WRITE=1 "
                "to enable."
            ),
        }

    try:
        async with asyncio.timeout(TIMEOUT):
            if plugin == "semgrep":
                result = await _semgrep_request(
                    "call_tool",
                    tool=tool,
                    arguments=arguments or {},
                )

            elif plugin in _PERSISTENT_MCP_PLUGINS:
                result = await _persistent_mcp_request(
                    plugin,
                    "call_tool",
                    tool=tool,
                    arguments=arguments or {},
                )

            else:
                async with _session(plugin) as session:
                    result = await session.call_tool(
                        tool,
                        arguments=arguments or {},
                    )

        return _normalize_result(result)

    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def code_plugin_status(
    plugin: str = "",
    probe: bool = False,
) -> dict[str, Any]:
    names = [plugin.casefold()] if plugin else list(PLUGIN_NAMES)
    result = {}

    for name in names:
        if name not in PLUGIN_NAMES:
            result[name] = {"ready": False, "reason": "unknown plugin"}
            continue

        ready, reason = _availability(name)
        item: dict[str, Any] = {
            "ready": ready,
            "reason": reason,
            "description": _specs()[name]["description"],
        }

        if ready and probe:
            try:
                tools = await _list_tools(name)
                item["tool_count"] = len(tools)
                item["probe"] = "ok"
            except Exception as exc:
                item["probe"] = "failed"
                item["error"] = f"{type(exc).__name__}: {exc}"

        result[name] = item

    return {
        "ok": True,
        "write_enabled": ALLOW_WRITE,
        "plugins": result,
    }



async def code_plugin_batch(
    plugin: str,
    steps: list[dict[str, Any]],
    stop_on_error: bool = True,
) -> dict[str, Any]:
    plugin = str(plugin or "").strip().casefold()

    if plugin not in PLUGIN_NAMES:
        return {"ok": False, "error": f"Unknown plugin: {plugin}"}

    ready, reason = _availability(plugin)
    if not ready:
        return {"ok": False, "error": reason}

    if not isinstance(steps, list) or not steps:
        return {"ok": False, "error": "steps must be a non-empty list"}

    if len(steps) > 10:
        return {"ok": False, "error": "Maximum 10 batch steps"}

    from bot import LOGGER
    LOGGER.info(
        "CODE_PLUGIN_BATCH plugin=%s steps=%s",
        plugin,
        len(steps),
    )

    results: list[dict[str, Any]] = []

    try:
        async with asyncio.timeout(TIMEOUT):
            # IMPORTANT: one MCP session for every step.
            async with _optimized_batch_session(plugin) as session:
                for index, step in enumerate(steps, start=1):
                    if not isinstance(step, dict):
                        item = {
                            "ok": False,
                            "step": index,
                            "error": "step must be an object",
                        }
                        results.append(item)
                        if stop_on_error:
                            break
                        continue

                    tool = str(step.get("tool") or "").strip()
                    arguments = step.get("arguments") or {}

                    if not isinstance(arguments, dict):
                        item = {
                            "ok": False,
                            "step": index,
                            "tool": tool,
                            "error": "arguments must be an object",
                        }
                        results.append(item)
                        if stop_on_error:
                            break
                        continue

                    if not _plugin_tool_allowed(plugin, tool):
                        item = {
                            "ok": False,
                            "step": index,
                            "tool": tool,
                            "error": (
                                "Tool blocked by read-only policy: "
                                f"{plugin}/{tool}"
                            ),
                        }
                        results.append(item)
                        if stop_on_error:
                            break
                        continue

                    if _contains_sensitive_path(arguments):
                        item = {
                            "ok": False,
                            "step": index,
                            "tool": tool,
                            "error": (
                                "Access to sensitive credential/config "
                                "paths is blocked."
                            ),
                        }
                        results.append(item)
                        if stop_on_error:
                            break
                        continue

                    if not _write_allowed(tool):
                        item = {
                            "ok": False,
                            "step": index,
                            "tool": tool,
                            "error": (
                                "Write-capable MCP tool blocked. "
                                "Set ATRI_CODE_PLUGINS_ALLOW_WRITE=1 "
                                "to enable."
                            ),
                        }
                        results.append(item)
                        if stop_on_error:
                            break
                        continue

                    LOGGER.info(
                        "CODE_PLUGIN_CALL plugin=%s tool=%s batch_step=%s",
                        plugin,
                        tool,
                        index,
                    )

                    result = await session.call_tool(
                        tool,
                        arguments=arguments,
                    )

                    normalized = _normalize_result(result)
                    normalized["step"] = index
                    normalized["tool"] = tool
                    results.append(normalized)

                    if stop_on_error and not normalized.get("ok"):
                        break

    except Exception as exc:
        return {
            "ok": False,
            "plugin": plugin,
            "results": results,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "ok": bool(results) and all(
            x.get("ok", False) for x in results
        ),
        "plugin": plugin,
        "results": results,
    }


# ATRI_CONTEXT7_FASTPATH_V2
_CONTEXT7_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)"
)


def _extract_context7_library_id(value: Any) -> str:
    candidates: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                key_fold = str(key).casefold()
                if (
                    key_fold in {"libraryid", "library_id", "id"}
                    and isinstance(child, str)
                    and child.startswith("/")
                ):
                    candidates.insert(0, child.strip())
                walk(child)
            return

        if isinstance(item, (list, tuple, set)):
            for child in item:
                walk(child)
            return

        if isinstance(item, str):
            candidates.extend(_CONTEXT7_ID_RE.findall(item))

    walk(value)
    return candidates[0] if candidates else ""


async def code_context7_docs(
    library: str,
    query: str,
) -> dict[str, Any]:
    library = str(library or "").strip()
    query = str(query or "").strip()

    if not library:
        return {"ok": False, "error": "library is required"}
    if not query:
        return {"ok": False, "error": "query is required"}

    ready, reason = _availability("context7")
    if not ready:
        return {"ok": False, "plugin": "context7", "error": reason}

    cache_key = library.casefold()
    now = time.monotonic()
    cached = _context7_library_cache.get(cache_key)
    library_id = ""
    cache_hit = False

    if cached and now - cached[0] < CONTEXT7_LIBRARY_CACHE_TTL:
        library_id = cached[1]
        cache_hit = True

    try:
        async with asyncio.timeout(TIMEOUT):
            # Resolve and query docs inside the same MCP session.
            async with _optimized_batch_session("context7") as session:
                if not library_id:
                    from bot import LOGGER
                    LOGGER.info(
                        "CODE_CONTEXT7_FASTPATH resolve library=%s",
                        library[:120],
                    )
                    resolved_raw = await session.call_tool(
                        "resolve-library-id",
                        arguments={
                            "libraryName": library,
                            "query": query,
                        },
                    )
                    resolved = _normalize_result(resolved_raw)
                    library_id = _extract_context7_library_id(resolved)
                    if not library_id:
                        return {
                            "ok": False,
                            "plugin": "context7",
                            "error": "Context7 did not return a library ID",
                        }
                    _context7_library_cache[cache_key] = (
                        time.monotonic(),
                        library_id,
                    )

                from bot import LOGGER
                LOGGER.info(
                    "CODE_CONTEXT7_FASTPATH query library_id=%s cache_hit=%s",
                    library_id,
                    cache_hit,
                )
                docs_raw = await session.call_tool(
                    "query-docs",
                    arguments={
                        "libraryId": library_id,
                        "query": query,
                    },
                )
                docs = _normalize_result(docs_raw)

        return {
            "ok": bool(docs.get("ok")),
            "plugin": "context7",
            "library": library,
            "library_id": library_id,
            "cache_hit": cache_hit,
            "content": docs.get("content"),
            "structured": docs.get("structured"),
        }

    except Exception as exc:
        return {
            "ok": False,
            "plugin": "context7",
            "library": library,
            "library_id": library_id,
            "error": f"{type(exc).__name__}: {exc}",
        }


CODE_CONTEXT7_DOCS_DECLARATION = {
    "name": "code_context7_docs",
    "description": (
        "Tra cứu docs/API hiện hành của một library/package bằng Context7. "
        "Tự resolve library ID rồi query docs trong cùng một MCP session. "
        "Ưu tiên dùng trực tiếp khi đã biết tên library; có thể chạy song song "
        "với code_web_search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "library": {
                "type": "string",
                "description": "Tên library/package chính thức, ví dụ httpx.",
            },
            "query": {
                "type": "string",
                "description": "Chủ đề cụ thể cần tra trong tài liệu.",
            },
        },
        "required": ["library", "query"],
    },
}


CODE_PLUGIN_BATCH_DECLARATION = {
    "name": "code_plugin_batch",
    "description": (
        "Gọi nhiều MCP tools tuần tự trong cùng một session. "
        "Bắt buộc ưu tiên tool này cho Chrome DevTools khi cần "
        "mở trang rồi đọc console/network hoặc thực hiện nhiều "
        "thao tác phụ thuộc cùng browser state. "
        "steps_json phải là JSON array các object gồm tool và arguments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plugin": {
                "type": "string",
                "enum": list(PLUGIN_NAMES),
            },
            "steps_json": {
                "type": "string",
                "description": (
                    'Ví dụ: [{"tool":"new_page",'
                    '"arguments":{"url":"https://example.com"}},'
                    '{"tool":"list_network_requests","arguments":{}}]'
                ),
            },
            "stop_on_error": {
                "type": "boolean",
            },
        },
        "required": ["plugin", "steps_json"],
    },
}


CODE_PLUGIN_SEARCH_DECLARATION = {
    "name": "code_plugin_search",
    "description": (
        "Tìm MCP tool phù hợp cho công việc lập trình. "
        "Dùng trước code_plugin_call khi chưa biết chính xác tên tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "plugin": {
                "type": "string",
                "enum": list(PLUGIN_NAMES),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
            },
        },
        "required": ["query"],
    },
}


CODE_PLUGIN_CALL_DECLARATION = {
    "name": "code_plugin_call",
    "description": (
        "Gọi một MCP coding tool đã tìm thấy. "
        "arguments_json phải là JSON object hợp lệ."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plugin": {
                "type": "string",
                "enum": list(PLUGIN_NAMES),
            },
            "tool": {"type": "string"},
            "arguments_json": {"type": "string"},
        },
        "required": ["plugin", "tool"],
    },
}


CODE_PLUGIN_STATUS_DECLARATION = {
    "name": "code_plugin_status",
    "description": "Kiểm tra trạng thái các coding MCP plugin.",
    "parameters": {
        "type": "object",
        "properties": {
            "plugin": {
                "type": "string",
                "enum": list(PLUGIN_NAMES),
            },
            "probe": {"type": "boolean"},
        },
    },
}


CODE_PLUGIN_DECLARATIONS = [
    CODE_CONTEXT7_DOCS_DECLARATION,
    CODE_PLUGIN_SEARCH_DECLARATION,
    CODE_PLUGIN_CALL_DECLARATION,
    CODE_PLUGIN_BATCH_DECLARATION,
    CODE_PLUGIN_STATUS_DECLARATION,
]


async def execute_code_plugin_tool(
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name == "code_context7_docs":
        return await code_context7_docs(
            library=arguments.get("library", ""),
            query=arguments.get("query", ""),
        )

    if name == "code_plugin_batch":
        native_steps = arguments.get("steps")

        if isinstance(native_steps, list):
            steps = native_steps
        else:
            raw = arguments.get("steps_json", "") or "[]"
            try:
                steps = json.loads(raw)
            except (TypeError, ValueError) as exc:
                return {
                    "ok": False,
                    "error": f"steps_json invalid: {exc}",
                }

        if not isinstance(steps, list):
            return {
                "ok": False,
                "error": "steps/steps_json must decode to an array",
            }

        return await code_plugin_batch(
            plugin=arguments.get("plugin", ""),
            steps=steps,
            stop_on_error=bool(
                arguments.get("stop_on_error", True)
            ),
        )

    if name == "code_plugin_search":
        return await code_plugin_search(
            query=arguments.get("query", ""),
            plugin=arguments.get("plugin", ""),
            limit=arguments.get("limit", 12),
        )

    if name == "code_plugin_status":
        return await code_plugin_status(
            plugin=arguments.get("plugin", ""),
            probe=bool(arguments.get("probe", False)),
        )

    if name == "code_plugin_call":
        native_arguments = arguments.get("arguments")

        if isinstance(native_arguments, dict):
            parsed = native_arguments
        else:
            raw = arguments.get("arguments_json", "") or "{}"
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError) as exc:
                return {
                    "ok": False,
                    "error": f"arguments_json invalid: {exc}",
                }

        if not isinstance(parsed, dict):
            return {
                "ok": False,
                "error": "arguments/arguments_json must decode to object",
            }

        return await code_plugin_call(
            plugin=arguments.get("plugin", ""),
            tool=arguments.get("tool", ""),
            arguments=parsed,
        )

    return {"ok": False, "error": f"Unknown code plugin tool: {name}"}
