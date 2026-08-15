#!/usr/bin/env python3
"""Guarded V153 AI runtime-guard patch for the customized live /app tree.

Only bot/__init__.py and bot/modules/atri_ai_runtime_guard.py are touched.
No git command is executed. Apply is transactional and rollback refuses to
overwrite live files that changed after the V153 patch was installed.
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
HOOK_MARKER = "# ATRI_AI_RUNTIME_GUARD_V153_BOOT"
HOOK_IMPORT = "from bot.modules.atri_ai_runtime_guard import install_atri_ai_runtime_guard"
HOOK_INSTALL = "install_atri_ai_runtime_guard()"
HOOK_ANCHOR = "scheduler = AsyncIOScheduler(event_loop=bot_loop)"
HOOK_BLOCK = """

# ATRI_AI_RUNTIME_GUARD_V153_BOOT
# Install after LOGGER/event-loop initialization but before bot modules import
# the code-plugin hub. The guard is read-only and does not start any network
# request during import.
try:
    from bot.modules.atri_ai_runtime_guard import install_atri_ai_runtime_guard

    install_atri_ai_runtime_guard()
except Exception:
    LOGGER.exception("ATRI_AI_RUNTIME_GUARD_V153_INSTALL_FAILED")
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.v153.tmp.{os.getpid()}")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def atomic_text_replace(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.v153.tmp.{os.getpid()}")
    shutil.copy2(path, temporary)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def paths(source_root: Path, live_root: Path) -> tuple[Path, Path, Path]:
    source_module = source_root / "bot" / "modules" / "atri_ai_runtime_guard.py"
    live_init = live_root / "bot" / "__init__.py"
    live_module = live_root / "bot" / "modules" / "atri_ai_runtime_guard.py"
    return source_module, live_init, live_module


def marker_state(text: str) -> tuple[int, int, int]:
    return (
        text.count(HOOK_MARKER),
        text.count(HOOK_IMPORT),
        text.count(HOOK_INSTALL),
    )


def verify(source_root: Path, live_root: Path) -> dict[str, object]:
    source_module, live_init, live_module = paths(source_root, live_root)
    if not source_module.is_file():
        raise RuntimeError(f"source V153 guard module missing: {source_module}")
    if not live_init.is_file():
        raise RuntimeError(f"live bot/__init__.py missing: {live_init}")
    if not live_module.is_file():
        raise RuntimeError(f"live V153 guard module missing: {live_module}")

    state = marker_state(live_init.read_text(encoding="utf-8"))
    if state != (1, 1, 1):
        raise RuntimeError(f"live V153 startup hook markers invalid: {state}")
    if sha256(source_module) != sha256(live_module):
        raise RuntimeError("live V153 guard module differs from isolated main clone")

    py_compile.compile(str(live_init), doraise=True)
    py_compile.compile(str(live_module), doraise=True)
    return {
        "applied": True,
        "init_sha256": sha256(live_init),
        "module_sha256": sha256(live_module),
    }


def restore_pre_apply(
    live_init: Path,
    live_module: Path,
    init_backup: Path,
    module_backup: Path,
    module_existed: bool,
) -> None:
    atomic_copy(init_backup, live_init)
    if module_existed:
        atomic_copy(module_backup, live_module)
    else:
        live_module.unlink(missing_ok=True)


def apply(source_root: Path, live_root: Path, backup_dir: Path) -> dict[str, object]:
    source_module, live_init, live_module = paths(source_root, live_root)
    if not source_module.is_file():
        raise RuntimeError(f"source V153 guard module missing: {source_module}")
    if not live_init.is_file():
        raise RuntimeError(f"live bot/__init__.py missing: {live_init}")

    backup_dir.mkdir(parents=True, exist_ok=False)
    init_backup = backup_dir / "bot-init.py.before"
    module_backup = backup_dir / "atri_ai_runtime_guard.py.before"
    shutil.copy2(live_init, init_backup)
    module_existed = live_module.exists()
    if module_existed:
        shutil.copy2(live_module, module_backup)

    text = live_init.read_text(encoding="utf-8")
    state = marker_state(text)
    if state not in {(0, 0, 0), (1, 1, 1)}:
        raise RuntimeError(f"partial V153 startup hook already present: {state}")

    already_hooked = state == (1, 1, 1)
    if not already_hooked:
        if text.count(HOOK_ANCHOR) != 1:
            raise RuntimeError("expected exactly one AsyncIOScheduler startup anchor")
        text = text.replace(HOOK_ANCHOR, HOOK_ANCHOR + HOOK_BLOCK, 1)

    mutation_started = False
    try:
        if not already_hooked:
            mutation_started = True
            atomic_text_replace(live_init, text)
        mutation_started = True
        atomic_copy(source_module, live_module)
        result = verify(source_root, live_root)
    except Exception:
        if mutation_started:
            restore_pre_apply(
                live_init,
                live_module,
                init_backup,
                module_backup,
                module_existed,
            )
        raise

    manifest: dict[str, object] = {
        "module_existed": module_existed,
        "init_before_sha256": sha256(init_backup),
        "init_after_sha256": result["init_sha256"],
        "module_after_sha256": result["module_sha256"],
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
    }


def rollback(live_root: Path, backup_dir: Path) -> dict[str, object]:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"backup manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    live_init = live_root / "bot" / "__init__.py"
    live_module = live_root / "bot" / "modules" / "atri_ai_runtime_guard.py"
    init_backup = backup_dir / "bot-init.py.before"
    module_backup = backup_dir / "atri_ai_runtime_guard.py.before"
    if not init_backup.is_file():
        raise RuntimeError("bot/__init__.py backup missing")

    current_init_hash = sha256(live_init) if live_init.is_file() else "missing"
    expected_init_after = str(manifest.get("init_after_sha256", ""))
    if expected_init_after and current_init_hash != expected_init_after:
        raise RuntimeError(
            "live bot/__init__.py changed after V153 apply; refusing destructive rollback"
        )

    current_module_hash = sha256(live_module) if live_module.is_file() else "missing"
    expected_module_after = str(manifest.get("module_after_sha256", ""))
    if expected_module_after and current_module_hash != expected_module_after:
        raise RuntimeError(
            "live atri_ai_runtime_guard.py changed after V153 apply; refusing destructive rollback"
        )

    atomic_copy(init_backup, live_init)
    if bool(manifest.get("module_existed")):
        if not module_backup.is_file():
            raise RuntimeError("V153 guard module backup missing")
        atomic_copy(module_backup, live_module)
    else:
        live_module.unlink(missing_ok=True)

    py_compile.compile(str(live_init), doraise=True)
    if live_module.exists():
        py_compile.compile(str(live_module), doraise=True)
    return {
        "rolled_back": True,
        "init_sha256": sha256(live_init),
        "module_present": live_module.exists(),
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
