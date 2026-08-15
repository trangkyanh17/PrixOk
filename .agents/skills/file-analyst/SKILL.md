---
name: file-analyst
description: "Analyze uploaded files and persisted artifacts, locate the relevant sections, summarize structure, and answer focused questions from real file evidence. Use for logs, configs, text files, source files, documents, and artifact follow-ups."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-permission: "authorized"
  atri-stage: "20"
  atri-capabilities: "artifact-rag; file-search; evidence-selection"
  atri-triggers: "đọc file; doc file; check file; phân tích file; phan tich file; xem file này; file config; file log; artifact này; artifact nay; tìm trong file; tim trong file"
---

# File Analyst

Use the file as the source of truth.

## Workflow

1. Identify file kind, structure, and the user's actual question before reading broadly.
2. Prefer Atri artifact retrieval/search for follow-ups instead of asking for the same upload again.
3. Select the smallest relevant chunks while retaining enough neighboring context to avoid false conclusions.
4. Preserve path, section, key names, line ranges, timestamps, and identifiers when they matter.
5. Distinguish direct file evidence from inference. If the relevant section is truncated or unavailable, say so.
6. For multiple files, defer cross-file claims to the cross-file reasoning step when available.
7. Never echo secrets found in configs/logs; refer to them as redacted credentials or secret material.

## Output

Answer the question first, then give the supporting file evidence and any missing information that prevents certainty.
