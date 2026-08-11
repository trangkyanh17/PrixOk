---
name: pdf
description: "Read, analyze, create, edit, merge, split, rotate, fill, or validate PDF files. Use whenever a PDF is the primary input or requested output, including scanned PDFs, forms, page manipulation, text/table extraction, or PDF generation."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-triggers: ".pdf; pdf file; file pdf; tạo pdf; tao pdf; sửa pdf; sua pdf; đọc pdf; doc pdf; merge pdf; split pdf; pdf form; scanned pdf; ocr pdf; xuất pdf; xuat pdf"
---

# PDF

Choose the PDF workflow by task instead of forcing one library or extraction method onto every file.

## Workflow

1. Preserve the original file unless the user explicitly wants in-place replacement.
2. Identify the task:
   - read/extract;
   - inspect tables/layout;
   - create;
   - merge/split/reorder/rotate;
   - fill a form;
   - watermark/encrypt;
   - extract images;
   - OCR a scan.
3. For text extraction, try the document's embedded text layer first.
4. If text is absent or clearly corrupted because pages are scans, use OCR only for the pages that need it.
5. For forms, inspect actual form fields before placing visual text manually.
6. For page operations, preserve page boxes, rotation, metadata, and ordering intentionally.
7. For generated PDFs, verify page count, text visibility, clipping, fonts, and major layout.
8. Re-open the produced PDF with an independent reader/library when available.

## Extraction discipline

A PDF is a visual container. Text order may differ from visual reading order. Tables, multi-column pages, headers/footers, and positioned text require layout-aware inspection.

## Privacy

PDFs are treated as private by default. Do not send their contents to public workers.

Read `references/validation-checklist.md` before final delivery.

<!-- ATRI_DOCUMENT_EXECUTION_V128 -->
On this Atri deployment, document creation is executed by the private
`ATRI_DOCUMENT_EXECUTION_CONTRACT_V128` runtime. When the user explicitly asks
for a new PDF, DOCX, or XLSX file, follow the injected contract and append one
bounded `atri-document` JSON envelope. The runtime strips the envelope, creates
and re-opens the artifact, then sends it through Telegram. Never claim delivery
before the runtime confirms it. Do not emit an envelope for ordinary reading or
analysis requests.
