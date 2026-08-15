---
name: archive-explorer
description: "Inspect ZIP/TAR archives safely, map their contents, identify relevant files, and hand focused evidence to downstream file/log/code analysis. Use when the user uploads or references an archive, source bundle, backup, or compressed diagnostic package."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-permission: "authorized"
  atri-stage: "10"
  atri-capabilities: "archive; artifact-rag; file-map"
  atri-triggers: "file zip; archive zip; giải nén; giai nen; source zip; log zip; compressed source; tar.gz; backup archive; kiểm tra zip; check zip"
---

# Archive Explorer

Treat the archive as a bounded evidence container, not as one giant prompt.

## Workflow

1. Use Atri's attachment/archive runtime rather than inventing file contents.
2. Read the archive inventory first and classify entries as source, config, logs, docs, media, generated output, or noise.
3. Prioritize files that match the user's question; do not dump every file into context.
4. Preserve paths so downstream reasoning can correlate imports, configs, logs, and generated artifacts.
5. When an archive contains logs plus source, pass the earliest causal log evidence to log analysis and the implicated paths to code analysis.
6. Respect archive depth, size, entry-count, ratio and media limits from the runtime. Never ask to disable those protections merely to inspect more data.
7. If the runtime reports truncation or unsupported archive format, state the limitation instead of pretending the missing entries were inspected.

## Output

Start with archive map and the files selected for deeper inspection. Then continue with the next applicable skill in the orchestration plan.
