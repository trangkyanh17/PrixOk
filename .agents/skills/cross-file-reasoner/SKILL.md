---
name: cross-file-reasoner
description: "Correlate evidence across multiple uploaded or persisted files, trace references and state across paths, and resolve contradictions between logs, configs, source, and generated output. Use when the answer depends on more than one file."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-permission: "authorized"
  atri-stage: "30"
  atri-capabilities: "artifact-rag; cross-file; dependency-trace"
  atri-triggers: "so sánh các file; so sanh cac file; nhiều file; nhieu file; cross file; correlate files; đối chiếu log và code; doi chieu log va code; tìm liên quan giữa file; tim lien quan giua file"
---

# Cross-file Reasoner

Build one evidence graph from multiple files instead of summarizing them independently.

## Workflow

1. List the files/chunks that actually contribute to the question.
2. Trace shared identifiers: paths, symbols, config keys, process IDs, timestamps, request IDs, model IDs, hashes, or filenames.
3. Resolve order and ownership: which component produced the state, which component consumed it, and what changed between them.
4. Prefer explicit references/imports/calls over name similarity.
5. When files disagree, preserve both claims and determine whether they represent different versions, environments, or time windows.
6. Do not infer unseen files. Ask artifact search for additional evidence when the index can answer the gap.
7. Hand the narrowed failing path to log/code/repo skills when applicable.

## Output

State the cross-file conclusion, then the evidence chain in path/order form. Mark unresolved links explicitly.
