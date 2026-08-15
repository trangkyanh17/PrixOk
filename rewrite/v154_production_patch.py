#!/usr/bin/env python3
"""Transactional V154 production hardening patch for customized live /app.

The patcher deliberately avoids bot/modules/atri_ai.py and every git operation.
It only adds the V154/V154.1/V154.2 startup hooks to bot/__init__.py,
adds the post-import tool-round hook to bot/__main__.py, and copies the five
source-controlled guard modules. Apply is backup-first and rollback is
stale-safe: a file changed after apply is never overwritten automatically.
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
from typing import Any

MANIFEST_NAME = "source-manifest.json"
V153_ANCHOR = 'LOGGER.exception("ATRI_AI_RUNTIME_GUARD_V153_INSTALL_FAILED")'
INIT_START = "# ATRI_SYSTEM_CONTRACT_GUARD_V154_BOOT"
INIT_END = 'LOGGER.exception("ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_INSTALL_FAILED")'
INIT_MARKERS = (
    "# ATRI_SYSTEM_CONTRACT_GUARD_V154_BOOT",
    "# ATRI_STICKER_CHAT_PRIVACY_V154_BOOT",
    "# ATRI_WEBAPP_NETWORK_GUARD_V154_BOOT",
    "# ATRI_XLSX_FORMULA_SAFETY_V1541_BOOT",
    "# ATRI_ARTIFACT_RELEVANCE_GUARD_V1542_BOOT",
)
MAIN_IMPORT_ANCHOR = "from .modules.atri_v150_shadow import add_v150_shadow_handlers"
MAIN_IMPORT_LINE = (
    "from .modules.atri_system_guard import install_atri_system_post_import_guard"
)
MAIN_CALL_ANCHOR = "add_handlers()"
MAIN_CALL_LINE = "install_atri_system_post_import_guard()"
MODULE_RELS = (
    "bot/modules/atri_system_guard.py",
    "bot/modules/atri_sticker_privacy_guard.py",
    "bot/modules/atri_webapp_safety_guard.py",
    "bot/modules/atri_xlsx_formula_guard.py",
    "bot/modules/atri_artifact_relevance_guard.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.v154.tmp.{os.getpid()}"
    )
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def atomic_text_replace(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.v154.tmp.{os.getpid()}")
    shutil.copy2(path, temporary)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _source_init_block(source_init: Path) -> str:
    text = source_init.read_text(encoding="utf-8")
    if text.count(INIT_START) != 1 or text.count(INIT_END) != 1:
        raise RuntimeError("source V154 init hook boundaries are not unique")
    start = text.index(INIT_START)
    end_start = text.index(INIT_END, start)
    end = text.find("\n", end_start)
    if end < 0:
        end = len(text)
    else:
        end += 1
    block = text[start:end].rstrip("\n")
    for marker in INIT_MARKERS:
        if block.count(marker) != 1:
            raise RuntimeError(f"source V154 init block missing/duplicate marker: {marker}")
    return block


def _paths(source_root: Path, live_root: Path) -> tuple[Path, Path, Path, dict[str, Path]]:
    source_init = source_root / "bot" / "__init__.py"
    live_init = live_root / "bot" / "__init__.py"
    live_main = live_root / "bot" / "__main__.py"
    source_modules = {rel: source_root / rel for rel in MODULE_RELS}
    return source_init, live_init, live_main, source_modules


def _init_hook_state(text: str, expected_block: str) -> str:
    counts = [text.count(marker) for marker in INIT_MARKERS]
    if all(count == 0 for count in counts):
        return "absent"
    if any(count != 1 for count in counts):
        raise RuntimeError(f"partial/duplicate V154 init hook markers: {counts}")
    if expected_block not in text:
        raise RuntimeError("live V154 init hook differs from isolated main source")
    return "applied"


def _main_hook_state(text: str) -> str:
    import_count = text.count(MAIN_IMPORT_LINE)
    call_count = text.count(MAIN_CALL_LINE)
    if import_count == 0 and call_count == 0:
        return "absent"
    if import_count != 1 or call_count != 1:
        raise RuntimeError(
            f"partial/duplicate V154 __main__ hook import={import_count} call={call_count}"
        )
    return "applied"


def _compile_files(live_init: Path, live_main: Path, live_root: Path) -> None:
    py_compile.compile(str(live_init), doraise=True)
    py_compile.compile(str(live_main), doraise=True)
    for rel in MODULE_RELS:
        py_compile.compile(str(live_root / rel), doraise=True)


def verify(source_root: Path, live_root: Path) -> dict[str, Any]:
    source_init, live_init, live_main, source_modules = _paths(
        source_root, live_root
    )
    for path in (source_init, live_init, live_main):
        if not path.is_file():
            raise RuntimeError(f"required file missing: {path}")
    for rel, source in source_modules.items():
        if not source.is_file():
            raise RuntimeError(f"source guard missing: {rel}")
        live = live_root / rel
        if not live.is_file():
            raise RuntimeError(f"live guard missing: {rel}")
        if sha256(source) != sha256(live):
            raise RuntimeError(f"live guard differs from isolated main source: {rel}")

    expected_block = _source_init_block(source_init)
    init_text = live_init.read_text(encoding="utf-8")
    if _init_hook_state(init_text, expected_block) != "applied":
        raise RuntimeError("V154 bot/__init__.py hook not applied")
    main_text = live_main.read_text(encoding="utf-8")
    if _main_hook_state(main_text) != "applied":
        raise RuntimeError("V154 bot/__main__.py hook not applied")

    _compile_files(live_init, live_main, live_root)
    return {
        "applied": True,
        "init_sha256": sha256(live_init),
        "main_sha256": sha256(live_main),
        "module_sha256": {
            rel: sha256(live_root / rel) for rel in MODULE_RELS
        },
    }


def _backup_file(path: Path, backup: Path) -> dict[str, Any]:
    existed = path.is_file()
    record: dict[str, Any] = {"existed": existed}
    if existed:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        record["before_sha256"] = sha256(backup)
    return record


def _restore_file(path: Path, backup: Path, record: dict[str, Any]) -> None:
    if bool(record.get("existed")):
        if not backup.is_file():
            raise RuntimeError(f"backup missing for {path}")
        atomic_copy(backup, path)
    else:
        path.unlink(missing_ok=True)


def apply(source_root: Path, live_root: Path, backup_dir: Path) -> dict[str, Any]:
    source_init, live_init, live_main, source_modules = _paths(
        source_root, live_root
    )
    for path in (source_init, live_init, live_main):
        if not path.is_file():
            raise RuntimeError(f"required file missing: {path}")
    for rel, source in source_modules.items():
        if not source.is_file():
            raise RuntimeError(f"source guard missing: {rel}")

    init_text = live_init.read_text(encoding="utf-8")
    if init_text.count(V153_ANCHOR) != 1:
        raise RuntimeError("V153 startup anchor must exist exactly once before V154 apply")
    expected_block = _source_init_block(source_init)
    init_state = _init_hook_state(init_text, expected_block)
    main_text = live_main.read_text(encoding="utf-8")
    main_state = _main_hook_state(main_text)

    if main_state == "absent":
        if main_text.count(MAIN_IMPORT_ANCHOR) != 1:
            raise RuntimeError("expected exactly one V151 shadow import anchor")
        if main_text.count(MAIN_CALL_ANCHOR) != 1:
            raise RuntimeError("expected exactly one add_handlers() call anchor")

    backup_dir.mkdir(parents=True, exist_ok=False)
    records: dict[str, dict[str, Any]] = {}
    targets: dict[str, tuple[Path, Path]] = {
        "bot/__init__.py": (live_init, backup_dir / "bot-init.py.before"),
        "bot/__main__.py": (live_main, backup_dir / "bot-main.py.before"),
    }
    for rel in MODULE_RELS:
        targets[rel] = (
            live_root / rel,
            backup_dir / (Path(rel).name + ".before"),
        )
    for rel, (path, backup) in targets.items():
        records[rel] = _backup_file(path, backup)

    mutation_started = False
    try:
        if init_state == "absent":
            anchor_end = init_text.index(V153_ANCHOR) + len(V153_ANCHOR)
            new_init = (
                init_text[:anchor_end]
                + "\n\n"
                + expected_block
                + init_text[anchor_end:]
            )
            mutation_started = True
            atomic_text_replace(live_init, new_init)

        if main_state == "absent":
            new_main = main_text.replace(
                MAIN_IMPORT_ANCHOR,
                MAIN_IMPORT_ANCHOR + "\n" + MAIN_IMPORT_LINE,
                1,
            )
            new_main = new_main.replace(
                MAIN_CALL_ANCHOR,
                MAIN_CALL_ANCHOR + "\n" + MAIN_CALL_LINE,
                1,
            )
            mutation_started = True
            atomic_text_replace(live_main, new_main)

        for rel, source in source_modules.items():
            mutation_started = True
            atomic_copy(source, live_root / rel)

        result = verify(source_root, live_root)
    except Exception:
        if mutation_started:
            for rel, (path, backup) in reversed(list(targets.items())):
                _restore_file(path, backup, records[rel])
        raise

    for rel, (path, _backup) in targets.items():
        records[rel]["after_sha256"] = sha256(path)

    manifest = {
        "version": 1542,
        "files": records,
        "init_already_hooked": init_state == "applied",
        "main_already_hooked": main_state == "applied",
    }
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **result,
        "backup": str(backup_dir),
        "init_already_hooked": init_state == "applied",
        "main_already_hooked": main_state == "applied",
    }


def rollback(live_root: Path, backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"backup manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("invalid V154 backup manifest")

    backup_names = {
        "bot/__init__.py": "bot-init.py.before",
        "bot/__main__.py": "bot-main.py.before",
        **{rel: Path(rel).name + ".before" for rel in MODULE_RELS},
    }

    for rel, raw_record in files.items():
        if rel not in backup_names or not isinstance(raw_record, dict):
            raise RuntimeError(f"unexpected V154 manifest entry: {rel}")
        live = live_root / rel
        current = sha256(live) if live.is_file() else "missing"
        expected = str(raw_record.get("after_sha256") or "")
        if expected and current != expected:
            raise RuntimeError(
                f"live file changed after V154 apply; refusing destructive rollback: {rel}"
            )

    for rel, raw_record in reversed(list(files.items())):
        live = live_root / rel
        backup = backup_dir / backup_names[rel]
        _restore_file(live, backup, raw_record)

    live_init = live_root / "bot" / "__init__.py"
    live_main = live_root / "bot" / "__main__.py"
    py_compile.compile(str(live_init), doraise=True)
    py_compile.compile(str(live_main), doraise=True)
    for rel in MODULE_RELS:
        live = live_root / rel
        if live.exists():
            py_compile.compile(str(live), doraise=True)

    return {
        "rolled_back": True,
        "init_sha256": sha256(live_init),
        "main_sha256": sha256(live_main),
        "module_present": {
            rel: (live_root / rel).is_file() for rel in MODULE_RELS
        },
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
