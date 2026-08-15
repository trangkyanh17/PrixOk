#!/usr/bin/env python3
"""Transactional V155 network-egress patch for the customized live /app tree.

Only narrow, source-controlled changes are applied. Large legacy modules are not
replaced: the V155 runtime guard shadows their network constructors before
Telegram handlers are registered. Rollback validates every post-apply digest
before restoring any file, so a later live edit cannot be partially overwritten.
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
MODULE_RELS = (
    "bot/helper/ext_utils/network_utils.py",
    "bot/modules/atri_network_egress_guard.py",
)
MANAGED_RELS = (
    "bot/__main__.py",
    "bot/helper/ext_utils/bot_utils.py",
    "sabnzbdapi/requests.py",
    *MODULE_RELS,
)

MAIN_IMPORT_ANCHOR = (
    "from .modules.atri_system_guard import install_atri_system_post_import_guard"
)
MAIN_IMPORT_LINE = (
    "from .modules.atri_network_egress_guard import install_atri_network_egress_guard"
)
MAIN_CALL_ANCHOR = "create_help_buttons()"
MAIN_CALL_LINE = "install_atri_network_egress_guard()"
MAIN_HANDLERS_LINE = "add_handlers()"

BOT_UTILS_OLD_IMPORT = "from httpx import AsyncClient\n"
BOT_UTILS_NEW_IMPORT_ANCHOR = "from .telegraph_helper import telegraph\n"
BOT_UTILS_NEW_IMPORT = (
    "from .network_utils import NetworkTargetBlocked, probe_public_http_url\n"
)
BOT_UTILS_OLD_FUNCTION = '''async def get_content_type(url):
    try:
        async with AsyncClient() as client:
            response = await client.get(url, allow_redirects=True, verify=False)
            return response.headers.get("Content-Type")
    except:
        return None
'''
BOT_UTILS_NEW_FUNCTION = '''async def get_content_type_with_final_url(url):
    """Probe a public HTTP(S) URL without allowing private-network redirects.

    Security policy violations are deliberately not converted to None so callers
    can stop the operation instead of accidentally handing the same blocked URL
    to a downloader. Ordinary transport failures retain the legacy None result.
    """
    try:
        probe = await probe_public_http_url(url)
        return probe.content_type, probe.final_url
    except NetworkTargetBlocked:
        raise
    except Exception:
        return None, None


async def get_content_type(url):
    content_type, _ = await get_content_type_with_final_url(url)
    return content_type
'''

SAB_OLD_DEFAULT = "VERIFY_CERTIFICATE: bool = False,"
SAB_NEW_DEFAULT = "VERIFY_CERTIFICATE: bool = True,"
SAB_OLD_REDIRECT = "follow_redirects=True,"
SAB_NEW_REDIRECT = "follow_redirects=False,"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.v155.tmp.{os.getpid()}")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def atomic_text_replace(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.v155.tmp.{os.getpid()}")
    shutil.copy2(path, temporary)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _backup_name(rel: str) -> str:
    return rel.replace("/", "__") + ".before"


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


def _main_state(text: str) -> str:
    import_count = text.count(MAIN_IMPORT_LINE)
    call_count = text.count(MAIN_CALL_LINE)
    if import_count == 0 and call_count == 0:
        return "absent"
    if import_count != 1 or call_count != 1:
        raise RuntimeError(
            f"partial/duplicate V155 main hook import={import_count} call={call_count}"
        )
    if text.index(MAIN_CALL_LINE) > text.index(MAIN_HANDLERS_LINE):
        raise RuntimeError("V155 guard must install before add_handlers()")
    return "applied"


def _patch_main(text: str) -> tuple[str, str]:
    state = _main_state(text)
    if state == "applied":
        return text, state
    if text.count(MAIN_IMPORT_ANCHOR) != 1:
        raise RuntimeError("V154 system guard import anchor must exist exactly once")
    if text.count(MAIN_CALL_ANCHOR) != 1 or text.count(MAIN_HANDLERS_LINE) != 1:
        raise RuntimeError("V155 handler-order anchors must exist exactly once")
    result = text.replace(
        MAIN_IMPORT_ANCHOR,
        MAIN_IMPORT_ANCHOR + "\n" + MAIN_IMPORT_LINE,
        1,
    )
    result = result.replace(
        MAIN_CALL_ANCHOR,
        MAIN_CALL_ANCHOR
        + "\n# V155 public-egress guard must be active before request handlers.\n"
        + MAIN_CALL_LINE,
        1,
    )
    if result.index(MAIN_CALL_LINE) > result.index(MAIN_HANDLERS_LINE):
        raise RuntimeError("generated V155 hook order is unsafe")
    return result, state


def _bot_utils_state(text: str) -> str:
    new_import = text.count(BOT_UTILS_NEW_IMPORT)
    new_function = text.count("async def get_content_type_with_final_url(url):")
    old_function = text.count(BOT_UTILS_OLD_FUNCTION)
    old_import = text.count(BOT_UTILS_OLD_IMPORT)
    if new_import == 1 and new_function == 1 and old_function == 0 and old_import == 0:
        return "applied"
    if new_import == 0 and new_function == 0 and old_function == 1 and old_import == 1:
        return "absent"
    raise RuntimeError(
        "bot_utils V155 state is partial/custom; refusing broad overwrite "
        f"new_import={new_import} new_function={new_function} "
        f"old_function={old_function} old_import={old_import}"
    )


def _patch_bot_utils(text: str) -> tuple[str, str]:
    state = _bot_utils_state(text)
    if state == "applied":
        return text, state
    if text.count(BOT_UTILS_NEW_IMPORT_ANCHOR) != 1:
        raise RuntimeError("bot_utils import anchor must exist exactly once")
    result = text.replace(BOT_UTILS_OLD_IMPORT, "", 1)
    result = result.replace(
        BOT_UTILS_NEW_IMPORT_ANCHOR,
        BOT_UTILS_NEW_IMPORT_ANCHOR + BOT_UTILS_NEW_IMPORT,
        1,
    )
    result = result.replace(BOT_UTILS_OLD_FUNCTION, BOT_UTILS_NEW_FUNCTION, 1)
    if _bot_utils_state(result) != "applied":
        raise RuntimeError("generated bot_utils V155 patch did not verify")
    return result, state


def _sab_state(text: str) -> str:
    old_default = text.count(SAB_OLD_DEFAULT)
    new_default = text.count(SAB_NEW_DEFAULT)
    old_redirect = text.count(SAB_OLD_REDIRECT)
    new_redirect = text.count(SAB_NEW_REDIRECT)
    if old_default == 0 and new_default == 1 and old_redirect == 0 and new_redirect == 1:
        return "applied"
    if old_default == 1 and new_default == 0 and old_redirect == 1 and new_redirect == 0:
        return "absent"
    raise RuntimeError(
        "SAB V155 state is partial/custom; refusing broad overwrite "
        f"old_default={old_default} new_default={new_default} "
        f"old_redirect={old_redirect} new_redirect={new_redirect}"
    )


def _patch_sab(text: str) -> tuple[str, str]:
    state = _sab_state(text)
    if state == "applied":
        return text, state
    result = text.replace(SAB_OLD_DEFAULT, SAB_NEW_DEFAULT, 1)
    result = result.replace(SAB_OLD_REDIRECT, SAB_NEW_REDIRECT, 1)
    if _sab_state(result) != "applied":
        raise RuntimeError("generated SAB V155 patch did not verify")
    return result, state


def _compile_managed(live_root: Path) -> None:
    for rel in MANAGED_RELS:
        path = live_root / rel
        if path.is_file() and path.suffix == ".py":
            py_compile.compile(str(path), doraise=True)


def verify(source_root: Path, live_root: Path) -> dict[str, Any]:
    live_main = live_root / "bot/__main__.py"
    live_bot_utils = live_root / "bot/helper/ext_utils/bot_utils.py"
    live_sab = live_root / "sabnzbdapi/requests.py"
    for path in (live_main, live_bot_utils, live_sab):
        if not path.is_file():
            raise RuntimeError(f"required live file missing: {path}")

    if _main_state(live_main.read_text(encoding="utf-8")) != "applied":
        raise RuntimeError("V155 __main__ hook not applied")
    if _bot_utils_state(live_bot_utils.read_text(encoding="utf-8")) != "applied":
        raise RuntimeError("V155 bot_utils probe guard not applied")
    if _sab_state(live_sab.read_text(encoding="utf-8")) != "applied":
        raise RuntimeError("V155 SAB defaults not applied")

    module_sha: dict[str, str] = {}
    for rel in MODULE_RELS:
        source = source_root / rel
        live = live_root / rel
        if not source.is_file() or not live.is_file():
            raise RuntimeError(f"V155 module missing: {rel}")
        if sha256(source) != sha256(live):
            raise RuntimeError(f"V155 live module differs from trusted source: {rel}")
        module_sha[rel] = sha256(live)

    _compile_managed(live_root)
    return {
        "applied": True,
        "main_sha256": sha256(live_main),
        "bot_utils_sha256": sha256(live_bot_utils),
        "sab_sha256": sha256(live_sab),
        "module_sha256": module_sha,
    }


def apply(source_root: Path, live_root: Path, backup_dir: Path) -> dict[str, Any]:
    for rel in MODULE_RELS:
        if not (source_root / rel).is_file():
            raise RuntimeError(f"trusted source module missing: {rel}")

    live_main = live_root / "bot/__main__.py"
    live_bot_utils = live_root / "bot/helper/ext_utils/bot_utils.py"
    live_sab = live_root / "sabnzbdapi/requests.py"
    for path in (live_main, live_bot_utils, live_sab):
        if not path.is_file():
            raise RuntimeError(f"required live file missing: {path}")

    main_text, main_state = _patch_main(live_main.read_text(encoding="utf-8"))
    bot_utils_text, bot_utils_state = _patch_bot_utils(
        live_bot_utils.read_text(encoding="utf-8")
    )
    sab_text, sab_state = _patch_sab(live_sab.read_text(encoding="utf-8"))

    backup_dir.mkdir(parents=True, exist_ok=False)
    records: dict[str, dict[str, Any]] = {}
    for rel in MANAGED_RELS:
        path = live_root / rel
        records[rel] = _backup_file(path, backup_dir / _backup_name(rel))

    mutation_started = False
    try:
        if main_state == "absent":
            mutation_started = True
            atomic_text_replace(live_main, main_text)
        if bot_utils_state == "absent":
            mutation_started = True
            atomic_text_replace(live_bot_utils, bot_utils_text)
        if sab_state == "absent":
            mutation_started = True
            atomic_text_replace(live_sab, sab_text)
        for rel in MODULE_RELS:
            mutation_started = True
            atomic_copy(source_root / rel, live_root / rel)
        result = verify(source_root, live_root)
    except Exception:
        if mutation_started:
            for rel in reversed(MANAGED_RELS):
                _restore_file(
                    live_root / rel,
                    backup_dir / _backup_name(rel),
                    records[rel],
                )
        raise

    for rel in MANAGED_RELS:
        records[rel]["after_sha256"] = sha256(live_root / rel)

    manifest = {
        "version": 1550,
        "files": records,
        "main_already_hooked": main_state == "applied",
        "bot_utils_already_hooked": bot_utils_state == "applied",
        "sab_already_hardened": sab_state == "applied",
    }
    (backup_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **result,
        "backup": str(backup_dir),
        "main_already_hooked": main_state == "applied",
        "bot_utils_already_hooked": bot_utils_state == "applied",
        "sab_already_hardened": sab_state == "applied",
    }


def rollback(live_root: Path, backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"backup manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(MANAGED_RELS):
        raise RuntimeError("invalid V155 backup manifest")

    # Preflight every target before restoring any file: all-or-nothing stale gate.
    for rel in MANAGED_RELS:
        record = files[rel]
        if not isinstance(record, dict):
            raise RuntimeError(f"invalid V155 manifest record: {rel}")
        live = live_root / rel
        current = sha256(live) if live.is_file() else "missing"
        expected = str(record.get("after_sha256") or "")
        if not expected or current != expected:
            raise RuntimeError(
                f"live file changed after V155 apply; refusing rollback: {rel}"
            )

    for rel in reversed(MANAGED_RELS):
        _restore_file(
            live_root / rel,
            backup_dir / _backup_name(rel),
            files[rel],
        )

    for rel in ("bot/__main__.py", "bot/helper/ext_utils/bot_utils.py", "sabnzbdapi/requests.py"):
        py_compile.compile(str(live_root / rel), doraise=True)
    return {
        "rolled_back": True,
        "restored_sha256": {
            rel: sha256(live_root / rel) if (live_root / rel).is_file() else "missing"
            for rel in MANAGED_RELS
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
