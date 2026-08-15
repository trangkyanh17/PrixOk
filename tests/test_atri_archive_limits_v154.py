from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


def _zip_bytes(name: str, payload: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)
    return output.getvalue()


def test_nested_archive_expansion_stops_at_configured_depth(tmp_path: Path):
    from bot.modules import atri_attachment_runtime as runtime

    deepest = _zip_bytes("secret.txt", b"must-not-expand")
    level_2 = _zip_bytes("level3.zip", deepest)
    level_1 = _zip_bytes("level2.zip", level_2)
    outer = tmp_path / "outer.zip"
    outer.write_bytes(_zip_bytes("level1.zip", level_1))

    entries = runtime._expand_archive_safe(
        outer,
        tmp_path / "expanded",
        runtime._ArchiveBudget(),
    )
    logical = {name for name, _path, _size in entries}

    assert "level1.zip" in logical
    assert "level1.zip!level2.zip" in logical
    assert "level1.zip!level2.zip!level3.zip" in logical
    assert not any(name.endswith("secret.txt") for name in logical)


def test_archive_entry_budget_is_global_across_nested_archives():
    from bot.modules import atri_attachment_runtime as runtime

    budget = runtime._ArchiveBudget()
    for _ in range(runtime.ARCHIVE_ENTRY_LIMIT):
        budget.reserve(1)

    with pytest.raises(runtime.AttachmentRuntimeError, match="ARCHIVE_ENTRY_LIMIT"):
        budget.reserve(1)


def test_archive_stream_cannot_exceed_declared_member_size(tmp_path: Path):
    from bot.modules import atri_attachment_runtime as runtime

    with pytest.raises(runtime.AttachmentRuntimeError, match="ARCHIVE_MEMBER_STREAM_LIMIT"):
        runtime._copy_archive_stream(
            io.BytesIO(b"12345"),
            tmp_path / "member.bin",
            expected=4,
        )
