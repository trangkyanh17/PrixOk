#!/usr/bin/env python3
"""Guarded V152 parity hooks for the customized live /app tree.

Only bot/modules/atri_ai.py and bot/modules/atri_v152_parity.py are touched.
No git command is executed and rollback refuses to overwrite later live edits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import shutil
import sys
from pathlib import Path

MANIFEST_NAME = "source-manifest.json"
DEFAULT_ENABLE_FILE = "/root/.local/state/atri-v152-parity/enabled"

PARITY_IMPORT_ANCHOR = "from bot.modules.atri_provider_control import ("
PARITY_IMPORT_BLOCK = """from bot.modules.atri_v152_parity import (
    publish_route_decision as _v152_publish_route_decision,
    publish_tool_observation as _v152_publish_tool_observation,
    publish_vertex_plan as _v152_publish_vertex_plan,
    tool_profile_for_mode as _v152_tool_profile_for_mode,
)
"""

VERTEX_PLAN_OLD = """    model = get_runtime_model()
    if mode == \"code\":
        model = \"gemini-3.6-flash\"
    # ATRI_THINKING_CONTROL_V2
    thinking_level = resolve_thinking(mode)
    # ATRI_PROVIDER_VERTEX_CONTROL_V1
    model = resolve_provider_model(\"vertex\", model)
    thinking_level = resolve_provider_thinking(
        \"vertex\",
        thinking_level,
    )
"""

VERTEX_PLAN_NEW = """    runtime_model_v152 = get_runtime_model()
    base_model_v152 = (
        \"gemini-3.6-flash\"
        if mode == \"code\"
        else runtime_model_v152
    )
    model = base_model_v152
    # ATRI_THINKING_CONTROL_V2
    base_thinking_v152 = resolve_thinking(mode)
    thinking_level = base_thinking_v152
    # ATRI_PROVIDER_VERTEX_CONTROL_V1
    model = resolve_provider_model(\"vertex\", model)
    thinking_level = resolve_provider_thinking(
        \"vertex\",
        thinking_level,
    )

    # ATRI_V152_DECISION_PARITY_PLAN
    from bot.modules.atri_provider_control import (
        provider_control_state as _v152_provider_control_state,
    )
    from bot.modules.atri_thinking_control import (
        get_thinking_control_state as _v152_get_thinking_control_state,
    )

    _v152_thinking_state = _v152_get_thinking_control_state()
    _v152_provider_state = _v152_provider_control_state()
    _v152_vertex_state = dict(
        (_v152_provider_state.get(\"providers\") or {}).get(\"vertex\") or {}
    )
    _v152_publish_vertex_plan(
        mode=mode,
        runtime_model=runtime_model_v152,
        base_model=base_model_v152,
        resolved_model=model,
        thinking_auto=bool(_v152_thinking_state.get(\"auto\", True)),
        thinking_levels=dict(_v152_thinking_state.get(\"levels\") or {}),
        base_thinking=base_thinking_v152,
        provider_model=str(_v152_vertex_state.get(\"model\") or \"auto\"),
        provider_thinking=str(_v152_vertex_state.get(\"thinking\") or \"auto\"),
        resolved_thinking=thinking_level,
        tool_profile=_v152_tool_profile_for_mode(mode),
    )
"""

ROUTE_OLD = """        force_github_mcp = (
            route_mode == \"code\"
            and is_explicit_github_lookup(
                route_text
            )
        )

        if force_github_mcp:
"""

ROUTE_NEW = """        force_github_mcp = (
            route_mode == \"code\"
            and is_explicit_github_lookup(
                route_text
            )
        )

        # ATRI_V152_DECISION_PARITY_ROUTE
        _v152_publish_route_decision(
            route_text=route_text,
            attachment_route=attachment_route_v143,
            actual_mode=route_mode,
            force_github_mcp=force_github_mcp,
        )

        if force_github_mcp:
"""

TOOL_OLD = """            name = str(function_call.get(\"name\") or \"\").strip()
            arguments = function_call.get(\"args\") or {}
"""

TOOL_NEW = """            name = str(function_call.get(\"name\") or \"\").strip()
            arguments = function_call.get(\"args\") or {}

            # ATRI_V152_DECISION_PARITY_TOOL_BOUNDARY
            _v152_publish_tool_observation(
                mode=mode,
                tool_profile=_v152_tool_profile_for_mode(mode),
                tool_name=name,
            )
"""


def enable_file() -> Path:
    return Path(os.getenv("ATRI_V152_DEBIAN_ENABLE_FILE", DEFAULT_ENABLE_FILE))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.v152.tmp.{os.getpid()}")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def atomic_text_replace(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.v152.tmp.{os.getpid()}")
    shutil.copy2(path, temporary)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def paths(source_root: Path, live_root: Path) -> tuple[Path, Path, Path]:
    source_module = source_root / "bot" / "modules" / "atri_v152_parity.py"
    live_ai = live_root / "bot" / "modules" / "atri_ai.py"
    live_module = live_root / "bot" / "modules" / "atri_v152_parity.py"
    return source_module, live_ai, live_module


def marker_state(text: str) -> tuple[int, int, int, int]:
    return (
        text.count("from bot.modules.atri_v152_parity import ("),
        text.count("# ATRI_V152_DECISION_PARITY_ROUTE"),
        text.count("# ATRI_V152_DECISION_PARITY_PLAN"),
        text.count("# ATRI_V152_DECISION_PARITY_TOOL_BOUNDARY"),
    )


def verify(source_root: Path, live_root: Path) -> dict[str, object]:
    source_module, live_ai, live_module = paths(source_root, live_root)
    if not source_module.is_file():
        raise RuntimeError(f"source parity module missing: {source_module}")
    if not live_ai.is_file():
        raise RuntimeError(f"live atri_ai missing: {live_ai}")
    if not live_module.is_file():
        raise RuntimeError(f"live parity module missing: {live_module}")

    state = marker_state(live_ai.read_text(encoding="utf-8"))
    if state != (1, 1, 1, 1):
        raise RuntimeError(f"live V152 hook markers invalid: {state}")
    if sha256(source_module) != sha256(live_module):
        raise RuntimeError("live parity module differs from isolated main clone")

    py_compile.compile(str(live_ai), doraise=True)
    py_compile.compile(str(live_module), doraise=True)
    return {
        "applied": True,
        "ai_sha256": sha256(live_ai),
        "module_sha256": sha256(live_module),
    }


def restore_pre_apply(
    live_ai: Path,
    live_module: Path,
    ai_backup: Path,
    module_backup: Path,
    module_existed: bool,
) -> None:
    atomic_copy(ai_backup, live_ai)
    if module_existed:
        atomic_copy(module_backup, live_module)
    else:
        live_module.unlink(missing_ok=True)


def apply(source_root: Path, live_root: Path, backup_dir: Path) -> dict[str, object]:
    source_module, live_ai, live_module = paths(source_root, live_root)
    if not source_module.is_file():
        raise RuntimeError(f"source parity module missing: {source_module}")
    if not live_ai.is_file():
        raise RuntimeError(f"live atri_ai missing: {live_ai}")

    backup_dir.mkdir(parents=True, exist_ok=False)
    ai_backup = backup_dir / "atri_ai.py.before"
    module_backup = backup_dir / "atri_v152_parity.py.before"
    shutil.copy2(live_ai, ai_backup)
    module_existed = live_module.exists()
    if module_existed:
        shutil.copy2(live_module, module_backup)

    state_file = enable_file()
    state_file_existed = state_file.exists()

    text = live_ai.read_text(encoding="utf-8")
    state = marker_state(text)
    if state not in {(0, 0, 0, 0), (1, 1, 1, 1)}:
        raise RuntimeError(f"partial V152 parity hooks already present: {state}")

    already_hooked = state == (1, 1, 1, 1)
    if not already_hooked:
        if text.count(PARITY_IMPORT_ANCHOR) != 1:
            raise RuntimeError("expected exactly one provider import anchor")
        if text.count(VERTEX_PLAN_OLD) != 1:
            raise RuntimeError("expected exactly one Vertex plan anchor")
        if text.count(ROUTE_OLD) != 1:
            raise RuntimeError("expected exactly one route anchor")
        if text.count(TOOL_OLD) != 1:
            raise RuntimeError("expected exactly one tool boundary anchor")

        text = text.replace(
            PARITY_IMPORT_ANCHOR,
            PARITY_IMPORT_BLOCK + "\n" + PARITY_IMPORT_ANCHOR,
            1,
        )
        text = text.replace(VERTEX_PLAN_OLD, VERTEX_PLAN_NEW, 1)
        text = text.replace(ROUTE_OLD, ROUTE_NEW, 1)
        text = text.replace(TOOL_OLD, TOOL_NEW, 1)

    mutation_started = False
    enable_created = False
    try:
        if not already_hooked:
            mutation_started = True
            atomic_text_replace(live_ai, text)
        mutation_started = True
        atomic_copy(source_module, live_module)
        result = verify(source_root, live_root)
        if not state_file_existed:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.touch()
            enable_created = True
    except Exception:
        if mutation_started:
            restore_pre_apply(
                live_ai,
                live_module,
                ai_backup,
                module_backup,
                module_existed,
            )
        if enable_created:
            state_file.unlink(missing_ok=True)
        raise

    manifest = {
        "module_existed": module_existed,
        "ai_before_sha256": sha256(ai_backup),
        "ai_after_sha256": result["ai_sha256"],
        "module_after_sha256": result["module_sha256"],
        "enable_file": str(state_file),
        "enable_file_existed": state_file_existed,
    }
    if module_existed:
        manifest["module_before_sha256"] = sha256(module_backup)
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **result,
        "backup": str(backup_dir),
        "already_hooked": already_hooked,
        "enable_file": str(state_file),
    }


def rollback(live_root: Path, backup_dir: Path) -> dict[str, object]:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"backup manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    live_ai = live_root / "bot" / "modules" / "atri_ai.py"
    live_module = live_root / "bot" / "modules" / "atri_v152_parity.py"
    ai_backup = backup_dir / "atri_ai.py.before"
    module_backup = backup_dir / "atri_v152_parity.py.before"
    if not ai_backup.is_file():
        raise RuntimeError("atri_ai backup missing")

    current_ai_hash = sha256(live_ai) if live_ai.is_file() else "missing"
    expected_after = str(manifest.get("ai_after_sha256", ""))
    if expected_after and current_ai_hash != expected_after:
        raise RuntimeError(
            "live bot/modules/atri_ai.py changed after V152 apply; refusing destructive rollback"
        )

    atomic_copy(ai_backup, live_ai)
    if bool(manifest.get("module_existed")):
        if not module_backup.is_file():
            raise RuntimeError("parity module backup missing")
        atomic_copy(module_backup, live_module)
    else:
        live_module.unlink(missing_ok=True)

    state_file = Path(str(manifest.get("enable_file") or enable_file()))
    if not bool(manifest.get("enable_file_existed")):
        state_file.unlink(missing_ok=True)

    py_compile.compile(str(live_ai), doraise=True)
    return {
        "rolled_back": True,
        "ai_sha256": sha256(live_ai),
        "module_present": live_module.exists(),
        "enable_file_present": state_file.exists(),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("action", choices=("apply", "verify", "rollback"))
    result.add_argument("--source-root", default="/opt/prixok-v150")
    result.add_argument("--live-root", default="/app")
    result.add_argument("--backup-dir")
    return result


def main() -> int:
    args = parser().parse_args()
    source_root = Path(args.source_root)
    live_root = Path(args.live_root)
    try:
        if args.action == "apply":
            if not args.backup_dir:
                raise RuntimeError("--backup-dir is required for apply")
            result = apply(source_root, live_root, Path(args.backup_dir))
        elif args.action == "verify":
            result = verify(source_root, live_root)
        else:
            if not args.backup_dir:
                raise RuntimeError("--backup-dir is required for rollback")
            result = rollback(live_root, Path(args.backup_dir))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
