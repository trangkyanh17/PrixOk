#!/usr/bin/env python3
"""Isolated, read-only V153 GitHub REST smoke probe.

Loads the deployed guard module by file path instead of importing the bot
package, so the probe cannot start Telegram or another production worker.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def load_guard(path: Path) -> ModuleType:
    if not path.is_file():
        raise RuntimeError(f"guard module missing: {path}")
    spec = importlib.util.spec_from_file_location("atri_v153_probe_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load guard module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def probe(module: ModuleType, owner: str, repo: str, ref: str) -> dict[str, object]:
    call = getattr(module, "github_rest_readonly_call", None)
    if not callable(call):
        raise RuntimeError("guard module has no github_rest_readonly_call")

    result = await call(
        "list_commits",
        {
            "owner": owner,
            "repo": repo,
            "ref": ref,
            "per_page": 1,
        },
    )
    if not isinstance(result, dict):
        raise RuntimeError("GitHub fallback returned a non-object result")
    if result.get("ok") is not True or result.get("data_ok") is not True:
        raise RuntimeError(
            "GitHub fallback data probe failed: "
            + str(result.get("error") or result.get("reason") or "unknown")[:500]
        )
    if result.get("source") != "github_rest_readonly":
        raise RuntimeError(f"unexpected GitHub probe source: {result.get('source')}")

    data = result.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise RuntimeError("GitHub fallback returned no commit data")
    first = data[0]
    sha = str(first.get("sha") or "").casefold()
    if not SHA_RE.fullmatch(sha):
        raise RuntimeError(f"invalid latest commit SHA: {sha!r}")

    message = str(first.get("message") or "").splitlines()[0][:240]
    return {
        "ok": True,
        "data_ok": True,
        "source": "github_rest_readonly",
        "owner": owner,
        "repo": repo,
        "ref": ref,
        "latest_sha": sha,
        "latest_message": message,
        "rate_remaining": str(result.get("rate_remaining") or ""),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument(
        "--guard",
        default="/app/bot/modules/atri_ai_runtime_guard.py",
    )
    result.add_argument("--owner", default="trangkyanh17")
    result.add_argument("--repo", default="PrixOk")
    result.add_argument("--ref", default="main")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        module = load_guard(Path(args.guard))
        result = asyncio.run(probe(module, args.owner, args.repo, args.ref))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
