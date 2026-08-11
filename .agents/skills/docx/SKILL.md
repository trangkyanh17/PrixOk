---
name: docx
description: "Create, read, edit, restructure, or validate Microsoft Word DOCX documents and templates. Use when the user mentions .docx/.dotx, asks for a Word document, or needs headings, tables, images, page structure, styles, comments, or professional Word formatting."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-triggers: ".docx; .dotx; word document; word doc; file word; tạo docx; tao docx; sửa docx; sua docx; tài liệu word; tai lieu word; xuất word; xuat word"
---

# DOCX

Treat a DOCX as a structured document package, not plain text with formatting pasted on top.

## Workflow

1. Decide whether the task is reading, creating, or modifying an existing DOCX.
2. Preserve the original for modification tasks.
3. Use document styles for hierarchy instead of manually styling every paragraph.
4. Keep headings, lists, tables, captions, images, headers/footers, and page breaks semantically structured.
5. When editing an existing file, preserve relationships and package parts that are unrelated to the requested change.
6. Avoid using spaces or repeated punctuation to simulate layout.
7. Use real list/numbering structures for bullets and numbered sections.
8. Check table widths, image sizing, page margins, and page-break behavior.
9. If the document needs a TOC, ensure heading structure is compatible with TOC generation.
10. Re-open the output and inspect core paragraphs/tables/styles after saving.

## Existing documents

Do not recreate an entire document just to make a small edit if a targeted change can preserve more of the original formatting and metadata.

## Privacy

DOCX files are treated as private by default.

Read `references/validation-checklist.md` before final delivery.

<!-- ATRI_DOCUMENT_EXECUTION_V128 -->
On this Atri deployment, document creation is executed by the private
`ATRI_DOCUMENT_EXECUTION_CONTRACT_V128` runtime. When the user explicitly asks
for a new PDF, DOCX, or XLSX file, follow the injected contract and append one
bounded `atri-document` JSON envelope. The runtime strips the envelope, creates
and re-opens the artifact, then sends it through Telegram. Never claim delivery
before the runtime confirms it. Do not emit an envelope for ordinary reading or
analysis requests.
