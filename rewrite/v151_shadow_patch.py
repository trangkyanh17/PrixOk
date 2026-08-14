#!/usr/bin/env python3
"""Guarded V151 shadow hook patcher for the customized live /app tree.

This tool is only invoked by the Termux V151 canary manager. It never runs git
commands and only mutates bot/__main__.py plus bot/modules/atri_v150_shadow.py.
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

IMPORT_ANCHOR = "from .core.handlers import add_handlers"
IMPORT_LINE = "from .modules.atri_v150_shadow import add_v150_shadow_handlers"
CALL_ANCHOR = "add_handlers()"
CALL_LINE = "add_v150_shadow_handlers(TgClient.bot)"
MANIFEST_NAME = "source-manifest.json"
DEFAULT_DEBIAN_ENABLE_FILE = "/root/.local/state/atri-v151-shadow/enabled"


def debian_enable_file() -> Path:
    return Path(os.getenv("ATRI_V151_DEBIAN_ENABLE_FILE", DEFAULT_DEBIAN_ENABLE_FILE))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.v151.tmp.{os.getpid()}")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def atomic_text_replace(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.v151.tmp.{os.getpid()}")
    shutil.copy2(path, temporary)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def paths(source_root: Path, live_root: Path) -> tuple[Path, Path, Path]:
    source_module = source_root / "bot" / "modules" / "atri_v150_shadow.py"
    live_main = live_root / "bot" / "__main__.py"
    live_module = live_root / "bot" / "modules" / "atri_v150_shadow.py"
    return source_module, live_main, live_module


def marker_state(text: str) -> tuple[int, int]:
    return text.count(IMPORT_LINE), text.count(CALL_LINE)


def verify(source_root: Path, live_root: Path) -> dict[str, object]:
    source_module, live_main, live_module = paths(source_root, live_root)
    if not source_module.is_file():
        raise RuntimeError(f"source shadow module missing: {source_module}")
    if not live_main.is_file():
        raise RuntimeError(f"live main missing: {live_main}")
    if not live_module.is_file():
        raise RuntimeError(f"live shadow module missing: {live_module}")

    text = live_main.read_text(encoding="utf-8")
    import_count, call_count = marker_state(text)
    if import_count != 1 or call_count != 1:
        raise RuntimeError(
            f"live shadow hook invalid import_count={import_count} call_count={call_count}"
        )
    if sha256(source_module) != sha256(live_module):
        raise RuntimeError("live shadow module differs from isolated main clone")

    py_compile.compile(str(live_main), doraise=True)
    py_compile.compile(str(live_module), doraise=True)
    return {
        "applied": True,
        "main_sha256": sha256(live_main),
        "module_sha256": sha256(live_module),
    }


def restore_pre_apply(
    live_main: Path,
    live_module: Path,
    main_backup: Path,
    module_backup: Path,
    module_existed: bool,
) -> None:
    atomic_copy(main_backup, live_main)
    if module_existed:
        atomic_copy(module_backup, live_module)
    else:
        live_module.unlink(missing_ok=True)


def apply(source_root: Path, live_root: Path, backup_dir: Path) -> dict[str, object]:
    source_module, live_main, live_module = paths(source_root, live_root)
    if not source_module.is_file():
        raise RuntimeError(f"source shadow module missing: {source_module}")
    if not live_main.is_file():
        raise RuntimeError(f"live main missing: {live_main}")

    backup_dir.mkdir(parents=True, exist_ok=False)
    main_backup = backup_dir / "bot-main.py.before"
    module_backup = backup_dir / "atri_v150_shadow.py.before"
    shutil.copy2(live_main, main_backup)
    module_existed = live_module.exists()
    if module_existed:
        shutil.copy2(live_module, module_backup)

    enable_file = debian_enable_file()
    enable_file_existed = enable_file.exists()

    text = live_main.read_text(encoding="utf-8")
    import_count, call_count = marker_state(text)
    if (import_count, call_count) not in {(0, 0), (1, 1)}:
        raise RuntimeError(
            f"partial V151 hook already present import_count={import_count} call_count={call_count}"
        )
    if import_count == 0:
        if text.count(IMPORT_ANCHOR) != 1:
            raise RuntimeError("expected exactly one core handler import anchor")
        if text.count(CALL_ANCHOR) != 1:
            raise RuntimeError("expected exactly one add_handlers() call anchor")
        text = text.replace(IMPORT_ANCHOR, f"{IMPORT_ANCHOR}\n{IMPORT_LINE}", 1)
        text = text.replace(CALL_ANCHOR, f"{CALL_ANCHOR}\n{CALL_LINE}", 1)

    mutation_started = False
    enable_created = False
    try:
        if import_count == 0:
            mutation_started = True
            atomic_text_replace(live_main, text)
        mutation_started = True
        atomic_copy(source_module, live_module)
        result = verify(source_root, live_root)
        if not enable_file_existed:
            enable_file.parent.mkdir(parents=True, exist_ok=True)
            enable_file.touch()
            enable_created = True
    except Exception:
        if mutation_started:
            restore_pre_apply(
                live_main,
                live_module,
                main_backup,
                module_backup,
                module_existed,
            )
        if enable_created:
            enable_file.unlink(missing_ok=True)
        raise

    manifest = {
        "module_existed": module_existed,
        "main_before_sha256": sha256(main_backup),
        "main_after_sha256": result["main_sha256"],
        "module_after_sha256": result["module_sha256"],
        "debian_enable_file": str(enable_file),
        "debian_enable_file_existed": enable_file_existed,
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
        "already_hooked": import_count == 1,
        "debian_enable_file": str(enable_file),
    }


def rollback(live_root: Path, backup_dir: Path) -> dict[str, object]:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"backup manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    live_main = live_root / "bot" / "__main__.py"
    live_module = live_root / "bot" / "modules" / "atri_v150_shadow.py"
    main_backup = backup_dir / "bot-main.py.before"
    module_backup = backup_dir / "atri_v150_shadow.py.before"
    if not main_backup.is_file():
        raise RuntimeError("main backup missing")

    current_main_hash = sha256(live_main) if live_main.is_file() else "missing"
    expected_after = str(manifest.get("main_after_sha256", ""))
    if expected_after and current_main_hash != expected_after:
        raise RuntimeError(
            "live bot/__main__.py changed after V151 apply; refusing destructive rollback"
        )

    atomic_copy(main_backup, live_main)
    if bool(manifest.get("module_existed")):
        if not module_backup.is_file():
            raise RuntimeError("module backup missing")
        atomic_copy(module_backup, live_module)
    else:
        live_module.unlink(missing_ok=True)

    enable_file = Path(str(manifest.get("debian_enable_file") or debian_enable_file()))
    if not bool(manifest.get("debian_enable_file_existed")):
        enable_file.unlink(missing_ok=True)

    py_compile.compile(str(live_main), doraise=True)
    return {
        "rolled_back": True,
        "main_sha256": sha256(live_main),
        "module_present": live_module.exists(),
        "debian_enable_file_present": enable_file.exists(),
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
