from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def test_document_envelope_rejects_multiple_or_unterminated_specs():
    from bot.modules import atri_document_runtime as documents

    with pytest.raises(documents.DocumentBridgeError, match="DOCUMENT_SPEC_FENCE_UNTERMINATED"):
        documents.extract_document_spec(
            'answer\n```atri-document\n{"version":1,"format":"pdf"}',
            strict=True,
        )

    with pytest.raises(documents.DocumentBridgeError, match="DOCUMENT_SPEC_MULTIPLE_ENVELOPES"):
        documents.extract_document_spec(
            '```atri-document\n{"version":1,"format":"pdf"}\n```\n'
            '```atri-document\n{"version":1,"format":"docx"}\n```',
            strict=True,
        )


def test_document_filename_is_confined_and_format_is_allowlisted():
    from bot.modules import atri_document_runtime as documents

    assert documents._safe_filename("../../owned.txt", "pdf") == "owned.pdf"
    with pytest.raises(documents.DocumentBridgeError, match="DOCUMENT_FORMAT_NOT_ALLOWED"):
        documents._normalize_format({"format": "exe", "filename": "x.exe"})


def test_document_runtime_creates_and_reopens_pdf_docx_xlsx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.modules import atri_document_runtime as documents

    root = tmp_path / "documents"
    monkeypatch.setattr(documents, "ARTIFACT_DIR", root)

    specs = [
        {
            "version": 1,
            "format": "pdf",
            "filename": "../../report.pdf",
            "title": "Atri PDF",
            "blocks": [
                {"type": "heading", "level": 1, "text": "Result"},
                {"type": "paragraph", "text": "PDF runtime verification."},
            ],
        },
        {
            "version": 1,
            "format": "docx",
            "filename": "../../report.docx",
            "title": "Atri DOCX",
            "blocks": [
                {"type": "heading", "level": 1, "text": "Result"},
                {"type": "paragraph", "text": "DOCX runtime verification."},
            ],
        },
        {
            "version": 1,
            "format": "xlsx",
            "filename": "../../report.xlsx",
            "title": "Atri XLSX",
            "sheets": [
                {
                    "name": "Data/Unsafe",
                    "rows": [["Name", "Value"], ["Atri", 154]],
                    "freeze_panes": "A2",
                    "auto_filter": True,
                }
            ],
        },
    ]

    for spec in specs:
        result = documents.execute_document_spec(spec)
        path = Path(result["artifact_path"])
        assert result["executed"] is True
        assert result["artifact_bytes"] > 100
        assert path.is_file()
        assert root.resolve() in path.resolve().parents
        assert ".." not in result["filename"]


def test_xlsx_sheet_names_are_sanitized_and_unique():
    from bot.modules import atri_document_runtime as documents

    sheets = documents._normalize_sheets(
        {
            "sheets": [
                {"name": "bad/name", "rows": [[1]]},
                {"name": "bad/name", "rows": [[2]]},
            ]
        }
    )
    assert sheets[0]["name"] == "bad_name"
    assert sheets[1]["name"].startswith("bad_name-")
    assert len({sheet["name"].casefold() for sheet in sheets}) == 2


def test_long_memory_is_key_isolated_and_manual_cards_do_not_cross_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from bot.modules import atri_long_memory as memory

    monkeypatch.setattr(memory, "DB_PATH", tmp_path / "memory.sqlite3")
    memory._INITIALIZED = False

    key_a = (1001, 0)
    key_b = (2002, 0)

    async def scenario():
        assert await memory.add_memory_card(
            key_a,
            "Hãy nhớ dự án alpha dùng Python.",
            source="manual",
        )
        assert await memory.add_memory_card(
            key_b,
            "Hãy nhớ dự án beta dùng Rust.",
            source="manual",
        )
        await memory.archive_chat_turn(
            key_a,
            "alpha gặp lỗi database timeout",
            "ignored model text",
        )
        await memory.archive_chat_turn(
            key_b,
            "beta đang build firmware",
            "ignored model text",
        )

    asyncio.run(scenario())

    cards_a, archive_a = memory._search_archive_sync(
        memory._key_to_text(key_a),
        "alpha Python database timeout",
        set(),
    )
    cards_b, archive_b = memory._search_archive_sync(
        memory._key_to_text(key_b),
        "beta Rust firmware",
        set(),
    )

    text_a = "\n".join(str(row["content"]) for row in [*cards_a, *archive_a])
    text_b = "\n".join(str(row["content"]) for row in [*cards_b, *archive_b])

    assert "alpha" in text_a.casefold()
    assert "beta" not in text_a.casefold()
    assert "beta" in text_b.casefold()
    assert "alpha" not in text_b.casefold()
