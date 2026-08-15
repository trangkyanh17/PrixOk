from __future__ import annotations

# ATRI_XLSX_FORMULA_SAFETY_V1541
#
# Atri can generate XLSX from private/untrusted file content. openpyxl treats a
# string beginning with '=' as a formula, so blindly forwarding model-generated
# strings creates a spreadsheet-formula injection boundary. Raw strings must be
# data. Formulas require an explicit {"formula":"=..."} cell object and pass a
# deliberately conservative local-workbook validator.

import logging
import re
from typing import Any


_LOGGER = logging.getLogger("bot")
_INSTALLED = False
_MAX_FORMULA_CHARS = 4000
_ALLOWED_FUNCTIONS = frozenset(
    {
        "ABS",
        "AND",
        "AVERAGE",
        "AVERAGEIF",
        "AVERAGEIFS",
        "CHOOSE",
        "COLUMN",
        "COLUMNS",
        "CONCAT",
        "CONCATENATE",
        "COUNT",
        "COUNTA",
        "COUNTIF",
        "COUNTIFS",
        "DATE",
        "DAY",
        "DAYS",
        "EDATE",
        "EOMONTH",
        "FILTER",
        "FIND",
        "HLOOKUP",
        "HOUR",
        "IF",
        "IFERROR",
        "IFNA",
        "IFS",
        "INDEX",
        "INT",
        "LARGE",
        "LEFT",
        "LEN",
        "LOWER",
        "MATCH",
        "MAX",
        "MEDIAN",
        "MID",
        "MIN",
        "MINUTE",
        "MOD",
        "MONTH",
        "NOT",
        "NOW",
        "OR",
        "POWER",
        "PRODUCT",
        "PROPER",
        "RANK",
        "REPLACE",
        "RIGHT",
        "ROUND",
        "ROUNDDOWN",
        "ROUNDUP",
        "ROW",
        "ROWS",
        "SEARCH",
        "SECOND",
        "SMALL",
        "SORT",
        "SQRT",
        "SUBSTITUTE",
        "SUM",
        "SUMIF",
        "SUMIFS",
        "TEXT",
        "TEXTJOIN",
        "TIME",
        "TODAY",
        "TRANSPOSE",
        "TRIM",
        "UNIQUE",
        "UPPER",
        "VALUE",
        "VLOOKUP",
        "WEEKDAY",
        "WORKDAY",
        "XLOOKUP",
        "YEAR",
    }
)
_FUNCTION_RE = re.compile(r"(?i)(?<![A-Z0-9_.])([A-Z][A-Z0-9_.]*)\s*\(")
_EXTERNAL_MARKERS = (
    "://",
    "file:",
    "ftp:",
    "http:",
    "https:",
    "\\\\",
)


def _safe_formula(value: Any) -> str:
    formula = str(value or "").strip()
    if not formula.startswith("="):
        raise ValueError("XLSX_FORMULA_MUST_START_EQUALS")
    if len(formula) > _MAX_FORMULA_CHARS:
        raise ValueError("XLSX_FORMULA_TOO_LARGE")
    if any(ord(char) < 32 and char not in {"\t"} for char in formula):
        raise ValueError("XLSX_FORMULA_CONTROL_CHAR")

    lowered = formula.casefold()
    if any(marker in lowered for marker in _EXTERNAL_MARKERS):
        raise ValueError("XLSX_FORMULA_EXTERNAL_REFERENCE_BLOCKED")
    # External workbook/sheet references and DDE-style syntax are not allowed.
    if "[" in formula or "]" in formula or "!" in formula:
        raise ValueError("XLSX_FORMULA_EXTERNAL_REFERENCE_BLOCKED")

    functions = {
        match.group(1).upper()
        for match in _FUNCTION_RE.finditer(formula)
    }
    unknown = sorted(functions - _ALLOWED_FUNCTIONS)
    if unknown:
        raise ValueError("XLSX_FORMULA_FUNCTION_BLOCKED:" + ",".join(unknown[:8]))
    return formula


def _safe_cell_value(original: Any, value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"formula"}:
        return _safe_formula(value.get("formula"))
    normalized = original(value)
    if isinstance(normalized, str) and normalized.startswith("="):
        # Prefixing an apostrophe keeps the value as text in Excel/openpyxl.
        return "'" + normalized
    return normalized


def _formula_skill_contract() -> str:
    return (
        "\n\n[ATRI_XLSX_FORMULA_SAFETY_V1541]\n"
        "XLSX formula safety supersedes any older generic statement that strings "
        "starting with '=' become formulas. Ordinary string cells are always data; "
        "a raw '=...' string will be escaped as text. To intentionally create a "
        "formula, encode that cell as exactly {\"formula\":\"=SUM(A2:A10)\"}. "
        "Only bounded local-workbook formulas and allowlisted functions are accepted. "
        "External workbook/sheet references, URL/network functions, DDE/COM-style "
        "functions and unknown functions are rejected. If a requested formula cannot "
        "fit this safe subset, explain the limitation instead of bypassing it.\n"
        "[END_ATRI_XLSX_FORMULA_SAFETY_V1541]"
    )


def install_atri_xlsx_formula_guard() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from bot.modules import atri_document_runtime as documents
    from bot.modules import atri_skills

    if getattr(documents, "_ATRI_V1541_XLSX_FORMULA_GUARD", False):
        _INSTALLED = True
        return

    original_cell_value = documents._cell_value
    original_vertex_context = atri_skills.skill_vertex_context

    def guarded_cell_value(value: Any) -> Any:
        try:
            return _safe_cell_value(original_cell_value, value)
        except ValueError as exc:
            raise documents.DocumentBridgeError(str(exc)) from exc

    def guarded_skill_vertex_context(activation: dict[str, Any]) -> str:
        context = original_vertex_context(activation)
        names = {
            str(name).strip().casefold()
            for name in activation.get("names", [])
        }
        if "xlsx" in names:
            context += _formula_skill_contract()
        return context

    documents._cell_value = guarded_cell_value
    atri_skills.skill_vertex_context = guarded_skill_vertex_context
    documents._ATRI_V1541_XLSX_FORMULA_GUARD = True
    _INSTALLED = True
    _LOGGER.info("ATRI_XLSX_FORMULA_SAFETY_V1541_INSTALLED")
