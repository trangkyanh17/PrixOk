from __future__ import annotations

import asyncio
from pathlib import Path


def _run(awaitable):
    return asyncio.run(awaitable)


def test_startup_installs_v153_guard():
    source = Path("bot/__init__.py").read_text(encoding="utf-8")
    assert "ATRI_AI_RUNTIME_GUARD_V153_BOOT" in source
    assert "install_atri_ai_runtime_guard" in source


def test_missing_token_keeps_github_read_capability(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    from bot.modules.atri_ai_runtime_guard import install_atri_ai_runtime_guard
    from bot.modules.atri_tools import code_plugins

    install_atri_ai_runtime_guard()
    ready, reason = code_plugins._availability("github")

    assert ready is True
    assert "REST read-only fallback" in reason


def test_github_discovery_uses_readonly_catalog_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    from bot.modules.atri_ai_runtime_guard import install_atri_ai_runtime_guard
    from bot.modules.atri_tools import code_plugins

    install_atri_ai_runtime_guard()
    result = _run(
        code_plugins.code_plugin_search(
            query="latest commit on GitHub repository",
            plugin="github",
            limit=20,
        )
    )

    assert result["ok"] is True
    names = {tool["name"] for tool in result["tools"]}
    assert "get_repository" in names
    assert "list_commits" in names
    assert "get_commit" in names
    assert "get_file_contents" in names
    assert not any(
        marker in name.casefold()
        for name in names
        for marker in ("create", "update", "delete", "merge", "push", "write")
    )


def test_direct_github_fastpath_is_preloaded_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    from bot.modules.atri_ai_runtime_guard import install_atri_ai_runtime_guard
    from bot.modules.atri_tools import code_plugins

    install_atri_ai_runtime_guard()
    result = _run(
        code_plugins.build_direct_plugin_fastpath_context(
            "Tìm trên GitHub repo trangkyanh17/PrixOk commit mới nhất",
            limit=10,
        )
    )

    assert result["ok"] is True
    assert result["plugin"] == "github"
    assert result["tool_count"] >= 3
    assert "list_commits" in result["context"]
    assert "DO NOT call code_plugin_search" in result["context"]


def test_list_commits_public_fallback_normalizes_payload(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    from bot.modules import atri_ai_runtime_guard as guard
    from bot.modules.atri_tools import code_plugins

    guard.install_atri_ai_runtime_guard()

    async def fake_get(path, *, params=None):
        assert path == "/repos/trangkyanh17/PrixOk/commits"
        assert params == {"per_page": 3, "sha": "main"}
        return {
            "ok": True,
            "data_ok": True,
            "terminal": False,
            "source": "github_rest_readonly",
            "status_code": 200,
            "rate_remaining": "57",
            "data": [
                {
                    "sha": "abc123",
                    "html_url": "https://github.com/trangkyanh17/PrixOk/commit/abc123",
                    "commit": {
                        "message": "fix: test commit\n\nbody",
                        "author": {"name": "Prix", "date": "2026-08-15T03:00:00Z"},
                        "committer": {"date": "2026-08-15T03:01:00Z"},
                    },
                }
            ],
        }

    monkeypatch.setattr(guard, "_github_rest_get", fake_get)

    result = _run(
        code_plugins.code_plugin_call(
            "github",
            "list_commits",
            {"owner": "trangkyanh17", "repo": "PrixOk", "ref": "main", "per_page": 3},
        )
    )

    assert result["ok"] is True
    assert result["data_ok"] is True
    assert result["source"] == "github_rest_readonly"
    assert result["data"][0]["sha"] == "abc123"
    assert result["data"][0]["message"].startswith("fix: test commit")


def test_github_terminal_failure_is_ok_to_stop_forced_tool_loop(monkeypatch):
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    from bot.modules import atri_ai_runtime_guard as guard
    from bot.modules.atri_tools import code_plugins

    guard.install_atri_ai_runtime_guard()

    async def fake_get(path, *, params=None):
        return guard._terminal_result(
            "GitHub resource not found",
            status_code=404,
            reason="not_found",
        )

    monkeypatch.setattr(guard, "_github_rest_get", fake_get)

    result = _run(
        code_plugins.code_plugin_call(
            "github",
            "list_commits",
            {"owner": "missing", "repo": "private", "ref": "main"},
        )
    )

    # atri_ai marks an explicit GitHub forced call complete when ok=True.
    # data_ok=False + terminal=True keeps that orchestration bit truthful
    # while preventing the old repeat-until-max_tool_rounds failure.
    assert result["ok"] is True
    assert result["data_ok"] is False
    assert result["terminal"] is True
    assert result["status_code"] == 404
    assert result["reason"] == "not_found"


def test_public_catalog_is_copy_safe():
    from bot.modules.atri_ai_runtime_guard import github_rest_tool_catalog

    first = github_rest_tool_catalog()
    second = github_rest_tool_catalog()
    first[0]["name"] = "mutated"

    assert second[0]["name"] == "get_repository"
