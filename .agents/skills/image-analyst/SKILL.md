---
name: image-analyst
description: "Analyze screenshots, photos, diagrams, UI captures, and visual evidence together with surrounding text or files. Use when the user's question depends on what is visible in an image."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "low"
  atri-model-hint: "vertex"
  atri-permission: "authorized"
  atri-stage: "20"
  atri-capabilities: "vision; screenshot-analysis; evidence-selection"
  atri-triggers: "xem ảnh; xem anh; check ảnh; check anh; screenshot này; screenshot nay; lỗi trong ảnh; loi trong anh; giao diện ảnh; giao dien anh; phân tích ảnh; phan tich anh"
---

# Image Analyst

Use visual evidence directly and separate what is visible from what is inferred.

## Workflow

1. Identify the relevant region, labels, states, icons, dialogs, timestamps, or error text.
2. Describe only details that matter to the user's question.
3. If the image is a screenshot of software, correlate visible state with logs/config/source when those artifacts are also available.
4. Do not invent unreadable text or hidden UI state.
5. Prefer native visual understanding; use OCR only when the runtime/tool explicitly provides it and exact text extraction is needed.
6. When comparing screenshots, preserve differences in state, layout, wording, and timing.

## Output

State the visible evidence first, then the likely interpretation and what would confirm it.
