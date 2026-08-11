---
name: xlsx
description: "Create, read, clean, edit, calculate, format, chart, or validate spreadsheet files such as XLSX, XLSM, CSV, and TSV when the primary deliverable is a spreadsheet. Use for workbook formulas, sheet restructuring, data cleanup, or spreadsheet generation."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-triggers: ".xlsx; xlsx; .xlsm; xlsm; excel file; file xlsx; spreadsheet file; bảng excel; bang excel; tạo xlsx; tao xlsx; sửa xlsx; sua xlsx; sửa file xlsx; sua file xlsx; sửa excel; sua excel; công thức excel; cong thuc excel; excel formula; workbook; worksheet; csv thành excel; csv thanh excel"
---

# XLSX

Preserve spreadsheet semantics: values, formulas, formats, workbook structure, and recalculation behavior all matter.

## Workflow

1. Identify whether the task is:
   - inspect/read;
   - data cleanup;
   - add/edit formulas;
   - formatting;
   - charting;
   - workbook restructuring;
   - format conversion.
2. Preserve the original workbook for edits.
3. Use a spreadsheet library suited to the task:
   - workbook/formula/style manipulation for cell-level edits;
   - dataframe tooling for bulk tabular transformation.
4. Preserve exact sheet names, headers, requested column order, and user-defined formulas unless change is intentional.
5. Prefer spreadsheet formulas when the result should recalculate as inputs change.
6. Distinguish formula text from cached values when reading workbooks.
7. Treat macro-enabled files carefully; do not silently strip VBA content.
8. Apply number/date formats intentionally instead of converting display values into strings.
9. Validate formulas, merged cells, named ranges, freeze panes, filters, and charts affected by the change.
10. Re-open the saved workbook and inspect representative cells and formulas.

## Data quality

Do not silently guess malformed headers or units when multiple interpretations are plausible. Make assumptions explicit.

## Privacy

Spreadsheet files are treated as private by default.

Read `references/validation-checklist.md` before delivery.

<!-- ATRI_DOCUMENT_EXECUTION_V128 -->
On this Atri deployment, document creation is executed by the private
`ATRI_DOCUMENT_EXECUTION_CONTRACT_V128` runtime. When the user explicitly asks
for a new PDF, DOCX, or XLSX file, follow the injected contract and append one
bounded `atri-document` JSON envelope. The runtime strips the envelope, creates
and re-opens the artifact, then sends it through Telegram. Never claim delivery
before the runtime confirms it. Do not emit an envelope for ordinary reading or
analysis requests.
