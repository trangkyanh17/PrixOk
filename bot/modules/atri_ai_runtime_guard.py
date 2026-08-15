from __future__ import annotations

import asyncio
import base64
import copy
import logging
import os
from typing import Any
from urllib.parse import quote

import httpx


# ATRI_AI_RUNTIME_GUARD_V153
#
# GitHub MCP normally uses api.githubcopilot.com and needs a GitHub token.
# Production may intentionally run without that token.  Explicit GitHub
# requests must not then force code_plugin_search forever until Vertex hits
# its tool-round limit.  This module installs a narrow runtime guard:
#
# 1. Keep the authenticated GitHub MCP path when it works.
# 2. Fall back to GET-only GitHub REST tools when MCP auth/transport is absent.
# 3. Return terminal, model-readable results for REST failures so an explicit
#    GitHub request cannot spin through the same failed tool repeatedly.
#
# The fallback never performs writes.

_LOGGER = logging.getLogger("bot")
_GITHUB_API = "https://api.github.com"
_HTTP_TIMEOUT_SECONDS = max(
    5.0,
    min(60.0, float(os.getenv("ATRI_GITHUB_REST_TIMEOUT", "20"))),
)
_MAX_FILE_TEXT = max(
    4096,
    min(65536, int(os.getenv("ATRI_GITHUB_REST_FILE_TEXT_MAX", "24000"))),
)


def _github_token() -> str:
    return (
        os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "plugin": "github",
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


_OWNER = {
    "type": "string",
    "description": "GitHub owner/organization login, for example trangkyanh17.",
}
_REPO = {
    "type": "string",
    "description": "Repository name without the owner prefix, for example PrixOk.",
}
_PER_PAGE = {
    "type": "integer",
    "minimum": 1,
    "maximum": 20,
    "description": "Maximum number of results, default 10 and hard-capped at 20.",
}

_GITHUB_REST_TOOLS: tuple[dict[str, Any], ...] = (
    _schema(
        "get_repository",
        "Read public/read-accessible GitHub repository metadata.",
        {"owner": _OWNER, "repo": _REPO},
        ["owner", "repo"],
    ),
    _schema(
        "list_commits",
        "List the newest commits from a GitHub repository/ref.",
        {
            "owner": _OWNER,
            "repo": _REPO,
            "ref": {
                "type": "string",
                "description": "Optional branch/tag/SHA such as main.",
            },
            "per_page": _PER_PAGE,
        },
        ["owner", "repo"],
    ),
    _schema(
        "get_commit",
        "Read one GitHub commit by SHA, branch, or tag ref.",
        {
            "owner": _OWNER,
            "repo": _REPO,
            "ref": {
                "type": "string",
                "description": "Commit SHA, branch, or tag.",
            },
        },
        ["owner", "repo", "ref"],
    ),
    _schema(
        "list_branches",
        "List branches and their head commit SHAs.",
        {"owner": _OWNER, "repo": _REPO, "per_page": _PER_PAGE},
        ["owner", "repo"],
    ),
    _schema(
        "get_file_contents",
        "Read a file or directory listing from a GitHub repository.",
        {
            "owner": _OWNER,
            "repo": _REPO,
            "path": {
                "type": "string",
                "description": "Repository-relative file or directory path.",
            },
            "ref": {
                "type": "string",
                "description": "Optional branch/tag/SHA; defaults to repository default branch.",
            },
        },
        ["owner", "repo", "path"],
    ),
    _schema(
        "list_pull_requests",
        "List GitHub pull requests for a repository.",
        {
            "owner": _OWNER,
            "repo": _REPO,
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Pull request state, default open.",
            },
            "per_page": _PER_PAGE,
        },
        ["owner", "repo"],
    ),
    _schema(
        "list_issues",
        "List GitHub issues (pull requests are filtered out).",
        {
            "owner": _OWNER,
            "repo": _REPO,
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Issue state, default open.",
            },
            "per_page": _PER_PAGE,
        },
        ["owner", "repo"],
    ),
    _schema(
        "list_releases",
        "List GitHub releases for a repository.",
        {"owner": _OWNER, "repo": _REPO, "per_page": _PER_PAGE},
        ["owner", "repo"],
    ),
)
_GITHUB_REST_TOOL_NAMES = frozenset(tool["name"] for tool in _GITHUB_REST_TOOLS)


def github_rest_tool_catalog() -> list[dict[str, Any]]:
    """Return a fresh, mutation-safe copy of the read-only fallback catalog."""
    return copy.deepcopy(list(_GITHUB_REST_TOOLS))


def _clean_component(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > 200:
        raise ValueError(f"{name} is too long")
    return text


def _per_page(arguments: dict[str, Any]) -> int:
    try:
        value = int(arguments.get("per_page") or 10)
    except (TypeError, ValueError):
        value = 10
    return max(1, min(value, 20))


def _terminal_result(
    error: str,
    *,
    status_code: int | None = None,
    rate_remaining: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Return an invocation-success/data-failure result.

    `ok=True` is deliberate: atri_ai uses that bit to mark the forced GitHub
    function call as completed. `data_ok=False` tells the model that no GitHub
    data was obtained. This prevents a terminal auth/network/404 result from
    being re-forced for every remaining Vertex tool round.
    """
    result: dict[str, Any] = {
        "ok": True,
        "data_ok": False,
        "terminal": True,
        "source": "github_rest_readonly",
        "error": str(error)[:1000],
    }
    if status_code is not None:
        result["status_code"] = int(status_code)
    if rate_remaining:
        result["rate_remaining"] = rate_remaining
    if reason:
        result["reason"] = reason
    return result


async def _github_rest_get(
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "PrixOk-Atri-GitHub-Readonly/1.0",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = _GITHUB_API + path
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_TIMEOUT_SECONDS),
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers, params=params or {})
    except httpx.HTTPError as exc:
        return _terminal_result(
            f"GitHub REST network error: {type(exc).__name__}: {exc}",
            reason="network_error",
        )

    remaining = str(response.headers.get("x-ratelimit-remaining") or "")

    if response.status_code == 404:
        return _terminal_result(
            "GitHub resource not found. The repository/ref may not exist, or a private repository needs a token with read access.",
            status_code=404,
            rate_remaining=remaining,
            reason="not_found",
        )

    if response.status_code in {401, 403}:
        reason = "rate_limited" if remaining == "0" else "forbidden"
        message = (
            "GitHub REST rate limit reached; configure GITHUB_TOKEN for a higher quota."
            if reason == "rate_limited"
            else "GitHub REST denied access; configure a read-capable GITHUB_TOKEN for private resources."
        )
        return _terminal_result(
            message,
            status_code=response.status_code,
            rate_remaining=remaining,
            reason=reason,
        )

    if response.status_code >= 400:
        body = response.text.strip().replace("\n", " ")[:700]
        return _terminal_result(
            f"GitHub REST HTTP {response.status_code}: {body}",
            status_code=response.status_code,
            rate_remaining=remaining,
            reason="http_error",
        )

    try:
        payload = response.json()
    except ValueError:
        return _terminal_result(
            "GitHub REST returned invalid JSON.",
            status_code=response.status_code,
            rate_remaining=remaining,
            reason="invalid_json",
        )

    return {
        "ok": True,
        "data_ok": True,
        "terminal": False,
        "source": "github_rest_readonly",
        "status_code": response.status_code,
        "rate_remaining": remaining,
        "data": payload,
    }


def _repo_path(owner: str, repo: str) -> str:
    return f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"


def _commit_summary(item: dict[str, Any]) -> dict[str, Any]:
    commit = item.get("commit") or {}
    author = commit.get("author") or {}
    committer = commit.get("committer") or {}
    message = str(commit.get("message") or "")
    return {
        "sha": str(item.get("sha") or ""),
        "message": message[:2000],
        "author_name": str(author.get("name") or ""),
        "author_date": str(author.get("date") or ""),
        "committer_date": str(committer.get("date") or ""),
        "html_url": str(item.get("html_url") or ""),
    }


def _repo_summary(item: dict[str, Any]) -> dict[str, Any]:
    owner = item.get("owner") or {}
    return {
        "full_name": str(item.get("full_name") or ""),
        "owner": str(owner.get("login") or ""),
        "private": bool(item.get("private", False)),
        "default_branch": str(item.get("default_branch") or ""),
        "description": str(item.get("description") or "")[:2000],
        "updated_at": str(item.get("updated_at") or ""),
        "pushed_at": str(item.get("pushed_at") or ""),
        "html_url": str(item.get("html_url") or ""),
    }


def _success(tool: str, data: Any, **extra: Any) -> dict[str, Any]:
    result = {
        "ok": True,
        "data_ok": True,
        "terminal": False,
        "source": "github_rest_readonly",
        "tool": tool,
        "data": data,
    }
    result.update(extra)
    return result


async def github_rest_readonly_call(
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool = str(tool or "").strip()
    arguments = arguments if isinstance(arguments, dict) else {}

    if tool not in _GITHUB_REST_TOOL_NAMES:
        return _terminal_result(
            f"Unsupported GitHub read-only fallback tool: {tool}",
            reason="unsupported_tool",
        )

    try:
        owner = _clean_component(arguments.get("owner"), "owner")
        repo = _clean_component(arguments.get("repo"), "repo")
    except ValueError as exc:
        return _terminal_result(str(exc), reason="invalid_arguments")

    base = _repo_path(owner, repo)

    if tool == "get_repository":
        raw = await _github_rest_get(base)
        if not raw.get("data_ok"):
            return raw
        data = raw.get("data")
        if not isinstance(data, dict):
            return _terminal_result("GitHub repository response was not an object.", reason="invalid_payload")
        return _success(tool, _repo_summary(data), rate_remaining=raw.get("rate_remaining", ""))

    if tool == "list_commits":
        params: dict[str, Any] = {"per_page": _per_page(arguments)}
        ref = str(arguments.get("ref") or "").strip()
        if ref:
            params["sha"] = ref[:200]
        raw = await _github_rest_get(base + "/commits", params=params)
        if not raw.get("data_ok"):
            return raw
        data = raw.get("data")
        if not isinstance(data, list):
            return _terminal_result("GitHub commits response was not a list.", reason="invalid_payload")
        return _success(
            tool,
            [_commit_summary(item) for item in data if isinstance(item, dict)],
            ref=ref,
            rate_remaining=raw.get("rate_remaining", ""),
        )

    if tool == "get_commit":
        try:
            ref = _clean_component(arguments.get("ref"), "ref")
        except ValueError as exc:
            return _terminal_result(str(exc), reason="invalid_arguments")
        raw = await _github_rest_get(base + "/commits/" + quote(ref, safe=""))
        if not raw.get("data_ok"):
            return raw
        data = raw.get("data")
        if not isinstance(data, dict):
            return _terminal_result("GitHub commit response was not an object.", reason="invalid_payload")
        summary = _commit_summary(data)
        summary["stats"] = data.get("stats") or {}
        files = data.get("files") or []
        summary["files"] = [
            {
                "filename": str(item.get("filename") or ""),
                "status": str(item.get("status") or ""),
                "additions": item.get("additions"),
                "deletions": item.get("deletions"),
            }
            for item in files[:20]
            if isinstance(item, dict)
        ]
        return _success(tool, summary, rate_remaining=raw.get("rate_remaining", ""))

    if tool == "list_branches":
        raw = await _github_rest_get(base + "/branches", params={"per_page": _per_page(arguments)})
        if not raw.get("data_ok"):
            return raw
        data = raw.get("data")
        if not isinstance(data, list):
            return _terminal_result("GitHub branches response was not a list.", reason="invalid_payload")
        branches = []
        for item in data:
            if not isinstance(item, dict):
                continue
            commit = item.get("commit") or {}
            branches.append({
                "name": str(item.get("name") or ""),
                "sha": str(commit.get("sha") or ""),
                "protected": bool(item.get("protected", False)),
            })
        return _success(tool, branches, rate_remaining=raw.get("rate_remaining", ""))

    if tool == "get_file_contents":
        try:
            path = _clean_component(arguments.get("path"), "path")
        except ValueError as exc:
            return _terminal_result(str(exc), reason="invalid_arguments")
        params = {}
        ref = str(arguments.get("ref") or "").strip()
        if ref:
            params["ref"] = ref[:200]
        encoded_path = quote(path.lstrip("/"), safe="/")
        raw = await _github_rest_get(base + "/contents/" + encoded_path, params=params)
        if not raw.get("data_ok"):
            return raw
        data = raw.get("data")
        if isinstance(data, list):
            listing = [
                {
                    "name": str(item.get("name") or ""),
                    "path": str(item.get("path") or ""),
                    "type": str(item.get("type") or ""),
                    "sha": str(item.get("sha") or ""),
                    "size": item.get("size"),
                    "html_url": str(item.get("html_url") or ""),
                }
                for item in data[:100]
                if isinstance(item, dict)
            ]
            return _success(tool, listing, ref=ref, rate_remaining=raw.get("rate_remaining", ""))
        if not isinstance(data, dict):
            return _terminal_result("GitHub contents response had an unsupported shape.", reason="invalid_payload")
        result = {
            "name": str(data.get("name") or ""),
            "path": str(data.get("path") or ""),
            "type": str(data.get("type") or ""),
            "sha": str(data.get("sha") or ""),
            "size": data.get("size"),
            "html_url": str(data.get("html_url") or ""),
        }
        encoded = str(data.get("content") or "")
        if data.get("encoding") == "base64" and encoded:
            try:
                decoded = base64.b64decode(encoded, validate=False)
                result["text"] = decoded.decode("utf-8", errors="replace")[:_MAX_FILE_TEXT]
                result["text_truncated"] = len(decoded) > _MAX_FILE_TEXT
            except Exception:
                result["text_error"] = "Unable to decode GitHub file content."
        return _success(tool, result, ref=ref, rate_remaining=raw.get("rate_remaining", ""))

    if tool == "list_pull_requests":
        state = str(arguments.get("state") or "open").casefold()
        if state not in {"open", "closed", "all"}:
            state = "open"
        raw = await _github_rest_get(
            base + "/pulls",
            params={"state": state, "per_page": _per_page(arguments)},
        )
        if not raw.get("data_ok"):
            return raw
        data = raw.get("data")
        if not isinstance(data, list):
            return _terminal_result("GitHub pull request response was not a list.", reason="invalid_payload")
        pulls = [
            {
                "number": item.get("number"),
                "title": str(item.get("title") or ""),
                "state": str(item.get("state") or ""),
                "draft": bool(item.get("draft", False)),
                "updated_at": str(item.get("updated_at") or ""),
                "html_url": str(item.get("html_url") or ""),
            }
            for item in data
            if isinstance(item, dict)
        ]
        return _success(tool, pulls, rate_remaining=raw.get("rate_remaining", ""))

    if tool == "list_issues":
        state = str(arguments.get("state") or "open").casefold()
        if state not in {"open", "closed", "all"}:
            state = "open"
        raw = await _github_rest_get(
            base + "/issues",
            params={"state": state, "per_page": _per_page(arguments)},
        )
        if not raw.get("data_ok"):
            return raw
        data = raw.get("data")
        if not isinstance(data, list):
            return _terminal_result("GitHub issues response was not a list.", reason="invalid_payload")
        issues = [
            {
                "number": item.get("number"),
                "title": str(item.get("title") or ""),
                "state": str(item.get("state") or ""),
                "updated_at": str(item.get("updated_at") or ""),
                "html_url": str(item.get("html_url") or ""),
            }
            for item in data
            if isinstance(item, dict) and "pull_request" not in item
        ]
        return _success(tool, issues, rate_remaining=raw.get("rate_remaining", ""))

    raw = await _github_rest_get(base + "/releases", params={"per_page": _per_page(arguments)})
    if not raw.get("data_ok"):
        return raw
    data = raw.get("data")
    if not isinstance(data, list):
        return _terminal_result("GitHub releases response was not a list.", reason="invalid_payload")
    releases = [
        {
            "tag_name": str(item.get("tag_name") or ""),
            "name": str(item.get("name") or ""),
            "draft": bool(item.get("draft", False)),
            "prerelease": bool(item.get("prerelease", False)),
            "published_at": str(item.get("published_at") or ""),
            "html_url": str(item.get("html_url") or ""),
        }
        for item in data
        if isinstance(item, dict)
    ]
    return _success(tool, releases, rate_remaining=raw.get("rate_remaining", ""))


def _fallback_search_result(
    query: str,
    *,
    limit: int,
    reason: str,
) -> dict[str, Any]:
    tools = github_rest_tool_catalog()[: max(1, min(int(limit or 12), 30))]
    return {
        "ok": True,
        "data_ok": True,
        "terminal": False,
        "query": str(query or ""),
        "tools": tools,
        "errors": {},
        "fallback_plugins": ["github_rest_readonly"],
        "fallback_reason": str(reason or "")[:500],
    }


async def _github_rest_batch(
    steps: list[dict[str, Any]],
    *,
    stop_on_error: bool,
) -> dict[str, Any]:
    if not isinstance(steps, list) or not steps:
        return _terminal_result("steps must be a non-empty list", reason="invalid_arguments")
    if len(steps) > 10:
        return _terminal_result("Maximum 10 batch steps", reason="invalid_arguments")

    results = []
    all_data_ok = True
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            item = _terminal_result("step must be an object", reason="invalid_arguments")
        else:
            item = await github_rest_readonly_call(
                str(step.get("tool") or ""),
                step.get("arguments") if isinstance(step.get("arguments"), dict) else {},
            )
        item = dict(item)
        item["step"] = index
        results.append(item)
        if not item.get("data_ok"):
            all_data_ok = False
            if stop_on_error:
                break

    return {
        "ok": True,
        "data_ok": all_data_ok,
        "terminal": False,
        "source": "github_rest_readonly",
        "plugin": "github",
        "results": results,
    }


def install_atri_ai_runtime_guard() -> bool:
    """Install the GitHub read-only fallback into the existing code-plugin hub."""
    from bot.modules.atri_tools import code_plugins as plugins

    if getattr(plugins, "_ATRI_AI_RUNTIME_GUARD_V153", False):
        return False

    original_availability = plugins._availability
    original_list_tools = plugins._list_tools
    original_call = plugins.code_plugin_call
    original_batch = plugins.code_plugin_batch

    def guarded_availability(name: str) -> tuple[bool, str]:
        plugin = str(name or "").strip().casefold()
        if plugin == "github" and not _github_token():
            return True, "github REST read-only fallback"
        return original_availability(name)

    async def guarded_list_tools(plugin: str) -> list[dict[str, Any]]:
        name = str(plugin or "").strip().casefold()
        if name == "github" and not _github_token():
            return github_rest_tool_catalog()
        try:
            return await original_list_tools(plugin)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if name != "github":
                raise
            _LOGGER.warning(
                "ATRI_GITHUB_MCP_DISCOVERY_FALLBACK reason=%s: %s",
                type(exc).__name__,
                exc,
            )
            return github_rest_tool_catalog()

    async def guarded_call(
        plugin: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = str(plugin or "").strip().casefold()
        tool_name = str(tool or "").strip()
        if name == "github" and not _github_token():
            return await github_rest_readonly_call(tool_name, arguments)

        try:
            result = await original_call(plugin, tool, arguments)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        if name != "github" or result.get("ok") is True:
            return result

        if tool_name in _GITHUB_REST_TOOL_NAMES:
            _LOGGER.warning(
                "ATRI_GITHUB_MCP_CALL_FALLBACK tool=%s reason=%s",
                tool_name,
                str(result.get("error") or "mcp_call_failed")[:300],
            )
            return await github_rest_readonly_call(tool_name, arguments)

        return _terminal_result(
            "GitHub MCP call failed and this MCP-specific tool has no read-only REST equivalent: "
            + str(result.get("error") or tool_name),
            reason="mcp_call_failed",
        )

    async def guarded_batch(
        plugin: str,
        steps: list[dict[str, Any]],
        stop_on_error: bool = True,
    ) -> dict[str, Any]:
        name = str(plugin or "").strip().casefold()
        if name == "github" and not _github_token():
            return await _github_rest_batch(steps, stop_on_error=bool(stop_on_error))
        return await original_batch(plugin, steps, stop_on_error)

    # The existing code_plugin_search/build_direct_plugin_fastpath_context and
    # execute_code_plugin_tool functions resolve these module globals at call
    # time, so patching the boundaries is sufficient; no duplicate AI/tool
    # executor is introduced.
    plugins._availability = guarded_availability
    plugins._list_tools = guarded_list_tools
    plugins.code_plugin_call = guarded_call
    plugins.code_plugin_batch = guarded_batch
    plugins._ATRI_AI_RUNTIME_GUARD_V153 = True
    plugins._ATRI_AI_RUNTIME_GUARD_V153_ORIGINALS = {
        "availability": original_availability,
        "list_tools": original_list_tools,
        "call": original_call,
        "batch": original_batch,
    }

    _LOGGER.info(
        "ATRI_AI_RUNTIME_GUARD_V153_INSTALLED github_mode=%s tools=%s",
        "mcp" if _github_token() else "rest-readonly",
        len(_GITHUB_REST_TOOLS),
    )
    return True
