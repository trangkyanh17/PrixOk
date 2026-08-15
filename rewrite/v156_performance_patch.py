#!/usr/bin/env python3
"""Transactional V156 performance patch for customized live /app.

Only the global sync executor anchor in bot_utils.py is edited. The dedicated
runtime_tuning.py module is copied from the trusted repo clone. Rollback checks
all post-apply hashes before restoring anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import shutil
import tempfile
from pathlib import Path
from typing import Any

MANIFEST_NAME = "source-manifest.json"
BOT_UTILS_REL = "bot/helper/ext_utils/bot_utils.py"
TUNING_REL = "bot/helper/ext_utils/runtime_tuning.py"
MANAGED_RELS = (BOT_UTILS_REL, TUNING_REL)

BOT_IMPORT_ANCHOR = (
    "from .network_utils import NetworkTargetBlocked, probe_public_http_url\n"
)
BOT_NEW_IMPORT = "from .runtime_tuning import ATRI_THREAD_POOL_WORKERS\n"
BOT_OLD_POOL = "THREAD_POOL = ThreadPoolExecutor(max_workers=500)"
BOT_NEW_POOL = '''THREAD_POOL = ThreadPoolExecutor(
    max_workers=ATRI_THREAD_POOL_WORKERS,
    thread_name_prefix="atri-global",
)'''


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.v156.tmp.{os.getpid()}")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.v156.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _backup_name(rel: str) -> str:
    return rel.replace("/", "__") + ".before"


def _state(text: str) -> str:
    new_import = text.count(BOT_NEW_IMPORT)
    old_pool = text.count(BOT_OLD_POOL)
    new_pool = text.count(BOT_NEW_POOL)
    if new_import == 0 and old_pool == 1 and new_pool == 0:
        return "absent"
    if new_import == 1 and old_pool == 0 and new_pool == 1:
        return "applied"
    raise RuntimeError(
        "bot_utils V156 state is partial/custom; refusing broad overwrite "
        f"new_import={new_import} old_pool={old_pool} new_pool={new_pool}"
    )


def _patch_text(text: str) -> tuple[str, str]:
    state = _state(text)
    if state == "applied":
        return text, state
    if text.count(BOT_IMPORT_ANCHOR) != 1:
        raise RuntimeError("V156 bot_utils import anchor must exist exactly once")
    result = text.replace(BOT_IMPORT_ANCHOR, BOT_IMPORT_ANCHOR + BOT_NEW_IMPORT, 1)
    result = result.replace(BOT_OLD_POOL, BOT_NEW_POOL, 1)
    if _state(result) != "applied":
        raise RuntimeError("generated V156 bot_utils patch did not verify")
    return result, state


def _compile_text(text: str, suffix: str = ".py") -> None:
    fd, name = tempfile.mkstemp(prefix="atri-v156-", suffix=suffix)
    os.close(fd)
    path = Path(name)
    try:
        path.write_text(text, encoding="utf-8")
        py_compile.compile(str(path), doraise=True)
    finally:
        path.unlink(missing_ok=True)


def _backup_file(path: Path, backup_file: Path) -> dict[str, Any]:
    existed = path.is_file()
    record: dict[str, Any] = {"existed": existed}
    if existed:
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_file)
        record["before_sha256"] = sha256(backup_file)
    return record


def _restore(path: Path, backup_file: Path, record: dict[str, Any]) -> None:
    if bool(record.get("existed")):
        if not backup_file.is_file():
            raise RuntimeError(f"backup missing for {path}")
        atomic_copy(backup_file, path)
    else:
        path.unlink(missing_ok=True)


def verify(source_root: Path, live_root: Path) -> dict[str, Any]:
    source_tuning = source_root / TUNING_REL
    live_tuning = live_root / TUNING_REL
    live_bot = live_root / BOT_UTILS_REL
    if not source_tuning.is_file() or not live_tuning.is_file() or not live_bot.is_file():
        raise RuntimeError("V156 managed source missing")
    text = live_bot.read_text(encoding="utf-8")
    if _state(text) != "applied":
        raise RuntimeError("V156 bot_utils patch absent")
    py_compile.compile(str(live_bot), doraise=True)
    py_compile.compile(str(live_tuning), doraise=True)
    expected = sha256(source_tuning)
    actual = sha256(live_tuning)
    if actual != expected:
        raise RuntimeError("V156 runtime_tuning SHA mismatch")
    return {
        "ok": True,
        "applied": True,
        "bot_utils_sha256": sha256(live_bot),
        "runtime_tuning_sha256": actual,
    }


def apply(source_root: Path, live_root: Path, backup_dir: Path) -> dict[str, Any]:
    source_tuning = source_root / TUNING_REL
    live_bot = live_root / BOT_UTILS_REL
    live_tuning = live_root / TUNING_REL
    if not source_tuning.is_file() or not live_bot.is_file():
        raise RuntimeError("V156 source/live prerequisites missing")
    if backup_dir.exists():
        raise RuntimeError("backup directory already exists")

    original_text = live_bot.read_text(encoding="utf-8")
    patched_text, prior_state = _patch_text(original_text)
    source_tuning_text = source_tuning.read_text(encoding="utf-8")
    _compile_text(patched_text)
    _compile_text(source_tuning_text)

    if prior_state == "applied":
        result = verify(source_root, live_root)
        result.update({"changed": False, "backup": None})
        return result

    if live_tuning.exists():
        raise RuntimeError("partial/custom V156 runtime_tuning exists before apply")

    records: dict[str, dict[str, Any]] = {}
    backup_dir.mkdir(parents=True, exist_ok=False)
    try:
        for rel in MANAGED_RELS:
            records[rel] = _backup_file(
                live_root / rel, backup_dir / _backup_name(rel)
            )
        atomic_write_text(live_bot, patched_text)
        atomic_copy(source_tuning, live_tuning)
        py_compile.compile(str(live_bot), doraise=True)
        py_compile.compile(str(live_tuning), doraise=True)
        for rel in MANAGED_RELS:
            path = live_root / rel
            records[rel]["after_sha256"] = sha256(path)
        manifest = {
            "version": 156,
            "files": records,
            "runtime_tuning_source_sha256": sha256(source_tuning),
        }
        (backup_dir / MANIFEST_NAME).write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        for rel in reversed(MANAGED_RELS):
            if rel in records:
                _restore(
                    live_root / rel,
                    backup_dir / _backup_name(rel),
                    records[rel],
                )
        raise

    result = verify(source_root, live_root)
    result.update({"changed": True, "backup": str(backup_dir)})
    return result


def rollback(live_root: Path, backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError("V156 source-manifest.json missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("version", 0)) != 156:
        raise RuntimeError("invalid V156 manifest version")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(MANAGED_RELS):
        raise RuntimeError("invalid V156 manifest file set")

    # Stale gate: inspect every managed path before restoring any of them.
    for rel in MANAGED_RELS:
        record = files[rel]
        path = live_root / rel
        expected = str(record.get("after_sha256") or "")
        if not path.is_file() or not expected or sha256(path) != expected:
            raise RuntimeError(f"{rel} changed after V156 apply")

    for rel in reversed(MANAGED_RELS):
        _restore(live_root / rel, backup_dir / _backup_name(rel), files[rel])

    return {"ok": True, "rolled_back": True}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "verify", "rollback"))
    parser.add_argument("--source-root", default=".")
    parser.add_argument("--live-root", default="/app")
    parser.add_argument("--backup-dir")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    source_root = Path(args.source_root).resolve()
    live_root = Path(args.live_root).resolve()
    try:
        if args.action == "verify":
            result = verify(source_root, live_root)
        elif args.action == "apply":
            if not args.backup_dir:
                raise RuntimeError("--backup-dir required for apply")
            result = apply(source_root, live_root, Path(args.backup_dir).resolve())
        else:
            if not args.backup_dir:
                raise RuntimeError("--backup-dir required for rollback")
            result = rollback(live_root, Path(args.backup_dir).resolve())
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
