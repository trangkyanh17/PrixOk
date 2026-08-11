---
name: repo-auditor
description: "Audit a code repository or production source tree for architecture problems, bugs, dead code, unsafe configuration, dependency issues, and regressions. Use when the user asks to review or audit an entire repo/codebase."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "high"
  atri-model-hint: "vertex"
  atri-triggers: "audit repo; repo audit; audit source; rà soát source; ra soat source; review codebase; audit codebase; kiểm tra toàn bộ source; kiem tra toan bo source; dead code audit"
---

# Repo Auditor

Audit the real repository systematically while preserving private source boundaries.

## Workflow

1. Map the repository: entry points, runtime services, config, storage/state, integrations, tests, and deployment.
2. Identify the user's stated invariants before recommending changes.
3. Trace critical paths end-to-end rather than reviewing files in isolation.
4. Look for:
   - unreachable/dead code,
   - duplicated state,
   - broad exception swallowing,
   - stale fallbacks,
   - unsafe secret handling,
   - dependency/version drift,
   - race conditions,
   - inconsistent configuration,
   - missing regression coverage.
5. Rank findings by impact and confidence.
6. Cite concrete file/function/line evidence when available.
7. Do not rewrite large areas solely for style.

## Privacy

Repository contents and production source stay on Vertex/private tools. Do not send them to public workers.

## Output

Prioritized findings first, then recommended minimal patches and tests.
