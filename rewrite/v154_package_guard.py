#!/usr/bin/env python3
"""Transactional package guard for the V154 phone production canary.

The canary may need to add a small set of Python distributions to the existing
/app virtualenv. A successful install is monotonic: distributions that existed
before the canary must keep the exact same versions. Rollback removes newly
added distributions and, if an interrupted pip operation changed an existing
version, attempts to restore the exact pre-canary version and verifies the final
snapshot byte-for-byte at the logical package/version level.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!~-]{0,127}$")


def _name(value: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", str(value or "").strip()).casefold()
    if not _NAME_RE.fullmatch(normalized):
        raise RuntimeError(f"unsafe distribution name: {value!r}")
    return normalized


def _version(value: str) -> str:
    version = str(value or "").strip()
    if not _VERSION_RE.fullmatch(version):
        raise RuntimeError(f"unsafe distribution version: {value!r}")
    return version


def snapshot() -> dict[str, str]:
    result: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        raw_name = str(dist.metadata.get("Name") or "").strip()
        if not raw_name:
            continue
        name = _name(raw_name)
        version = _version(str(dist.version or ""))
        previous = result.get(name)
        if previous is not None and previous != version:
            raise RuntimeError(
                f"conflicting installed versions for {name}: {previous} vs {version}"
            )
        result[name] = version
    return dict(sorted(result.items()))


def _load(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("package snapshot must be a JSON object")
    parsed = {_name(str(name)): _version(str(version)) for name, version in raw.items()}
    if len(parsed) != len(raw):
        raise RuntimeError("duplicate normalized distribution names in snapshot")
    return dict(sorted(parsed.items()))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def diff(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    new = {name: after[name] for name in after.keys() - before.keys()}
    removed = {name: before[name] for name in before.keys() - after.keys()}
    changed = {
        name: {"before": before[name], "after": after[name]}
        for name in before.keys() & after.keys()
        if before[name] != after[name]
    }
    return {
        "new": dict(sorted(new.items())),
        "removed": dict(sorted(removed.items())),
        "changed": dict(sorted(changed.items())),
    }


def _pip(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pip", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=check,
    )


def _restore_requirements(before: dict[str, str], current: dict[str, str]) -> list[str]:
    requirements: list[str] = []
    for name, old_version in before.items():
        if current.get(name) != old_version:
            requirements.append(f"{name}=={old_version}")
    return requirements


def rollback_to(before: dict[str, str]) -> dict[str, Any]:
    current = snapshot()
    delta_before = diff(before, current)
    new_names = sorted(delta_before["new"])
    if new_names:
        _pip(
            "uninstall",
            "-y",
            *new_names,
        )

    current = snapshot()
    restore = _restore_requirements(before, current)
    if restore:
        _pip(
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--retries",
            "2",
            "--timeout",
            "60",
            "--no-deps",
            "--force-reinstall",
            *restore,
        )

    final = snapshot()
    remaining = diff(before, final)
    if any(remaining[key] for key in ("new", "removed", "changed")):
        raise RuntimeError(
            "package rollback did not restore exact snapshot: "
            + json.dumps(remaining, sort_keys=True)
        )
    return {"rolled_back": True, "delta_before": delta_before, "final": final}


def _planned_mutations(packages: list[str], before: dict[str, str]) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="atri-v154-pip-plan-") as raw_tmp:
        report = Path(raw_tmp) / "report.json"
        result = _pip(
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--retries",
            "2",
            "--timeout",
            "60",
            "--dry-run",
            "--report",
            str(report),
            *packages,
            check=False,
        )
        if result.returncode != 0 or not report.is_file():
            raise RuntimeError("pip dry-run failed: " + result.stdout[-4000:])
        payload = json.loads(report.read_text(encoding="utf-8"))

    planned: dict[str, str] = {}
    for item in payload.get("install") or []:
        metadata = item.get("metadata") or {}
        name = _name(str(metadata.get("name") or ""))
        version = _version(str(metadata.get("version") or ""))
        planned[name] = version

    existing_mutations = {
        name: version
        for name, version in planned.items()
        if name in before and before[name] != version
    }
    if existing_mutations:
        raise RuntimeError(
            "pip plan would change pre-existing distributions: "
            + json.dumps(existing_mutations, sort_keys=True)
        )
    return dict(sorted(planned.items()))


def install_safe(packages: list[str], snapshot_path: Path, delta_path: Path) -> dict[str, Any]:
    if not packages:
        raise RuntimeError("at least one package is required")
    before = snapshot()
    _write(snapshot_path, before)
    planned = _planned_mutations(packages, before)

    install = _pip(
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--retries",
        "2",
        "--timeout",
        "60",
        *packages,
        check=False,
    )
    after = snapshot()
    delta = diff(before, after)
    _write(delta_path, delta)

    if install.returncode != 0:
        rollback_to(before)
        raise RuntimeError("pip install failed: " + install.stdout[-4000:])
    if delta["removed"] or delta["changed"]:
        rollback_to(before)
        raise RuntimeError(
            "pip changed pre-existing distributions; transaction rolled back: "
            + json.dumps(delta, sort_keys=True)
        )
    return {
        "installed": True,
        "planned": planned,
        "delta": delta,
        "pip_tail": install.stdout[-2000:],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("action", choices=("snapshot", "delta", "install-safe", "rollback"))
    result.add_argument("--snapshot", required=True)
    result.add_argument("--delta")
    result.add_argument("packages", nargs="*")
    return result


def main() -> int:
    args = parser().parse_args()
    snapshot_path = Path(args.snapshot)
    delta_path = Path(args.delta) if args.delta else None
    try:
        if args.action == "snapshot":
            current = snapshot()
            _write(snapshot_path, current)
            payload: dict[str, Any] = {"snapshot": current}
        elif args.action == "delta":
            if delta_path is None:
                raise RuntimeError("--delta is required")
            before = _load(snapshot_path)
            current = snapshot()
            delta = diff(before, current)
            _write(delta_path, delta)
            payload = {"delta": delta}
        elif args.action == "install-safe":
            if delta_path is None:
                raise RuntimeError("--delta is required")
            payload = install_safe(args.packages, snapshot_path, delta_path)
        else:
            before = _load(snapshot_path)
            payload = rollback_to(before)
            if delta_path is not None:
                _write(delta_path, diff(before, snapshot()))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **payload}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
