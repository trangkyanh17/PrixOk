from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "rewrite" / "v154_package_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("atri_v154_package_guard_test", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_delta_tracks_new_removed_and_changed_versions():
    guard = _load_guard()
    before = {"alpha": "1.0", "beta": "2.0", "gone": "3.0"}
    after = {"alpha": "1.0", "beta": "2.1", "newpkg": "4.0"}

    delta = guard.diff(before, after)

    assert delta["new"] == {"newpkg": "4.0"}
    assert delta["removed"] == {"gone": "3.0"}
    assert delta["changed"] == {"beta": {"before": "2.0", "after": "2.1"}}


def test_package_name_normalization_and_version_validation_are_bounded():
    guard = _load_guard()
    assert guard._name("Python_Docx") == "python-docx"
    assert guard._version("1!2.3.0+local") == "1!2.3.0+local"
    with pytest.raises(RuntimeError):
        guard._name("bad;name")
    with pytest.raises(RuntimeError):
        guard._version("1.0; python_version<'4'")


def test_install_safe_rejects_post_install_mutation_of_existing_distribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    guard = _load_guard()
    before = {"existing": "1.0"}
    after = {"existing": "2.0", "newpkg": "1.0"}
    snapshots = iter((before, after))
    rolled_back: list[dict[str, str]] = []

    monkeypatch.setattr(guard, "snapshot", lambda: dict(next(snapshots)))
    monkeypatch.setattr(
        guard,
        "_planned_mutations",
        lambda packages, original: {"newpkg": "1.0"},
    )
    monkeypatch.setattr(
        guard,
        "_pip",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""),
    )
    monkeypatch.setattr(
        guard,
        "rollback_to",
        lambda original: rolled_back.append(dict(original)) or {"rolled_back": True},
    )

    with pytest.raises(RuntimeError, match="pre-existing distributions"):
        guard.install_safe(
            ["newpkg"],
            tmp_path / "before.json",
            tmp_path / "delta.json",
        )

    assert rolled_back == [before]


def test_canary_source_contract_checks_untracked_origin_and_backup_provenance():
    source = (REPO_ROOT / "rewrite" / "termux-v154-production-canary.sh").read_text(
        encoding="utf-8"
    )
    assert "git status --porcelain=v1 --untracked-files=all" in source
    assert '"$origin_head" != "$head"' in source
    assert "validate_backup_path" in source
    assert '"$candidate" == "$REPO_SHA"' in source
    assert "v154_package_guard.py" in source
    assert "pip-before.json" in source
    assert "pip-delta.json" in source
