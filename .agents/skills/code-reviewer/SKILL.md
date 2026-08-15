---
name: code-reviewer
description: "Review code changes or focused source paths for correctness, regressions, concurrency problems, contract breakage, and missing tests. Use when the user asks to review a patch, PR, diff, or specific implementation."
metadata:
  atri-privacy: "auto"
  atri-worker-eligible: "true"
  atri-risk: "medium"
  atri-model-hint: "coding"
  atri-permission: "authorized"
  atri-stage: "40"
  atri-capabilities: "code-review; regression; test-design"
  atri-triggers: "review code; code review; review diff; review patch; review pr; kiểm tra patch; kiem tra patch; rà code; ra code; xem code sửa đúng chưa; check regression"
---

# Code Reviewer

Review behavior, not formatting.

## Workflow

1. Establish the intended contract and the exact changed surface.
2. Trace changed paths into callers, state, persistence, concurrency, error handling, and cleanup.
3. Look for correctness defects, silent fallback changes, partial updates, stale state, race conditions, resource leaks, and backwards-compatibility breaks.
4. Treat tests as evidence, not proof; identify cases the tests do not cover.
5. Rank findings by severity and confidence. Do not manufacture findings to fill a checklist.
6. When no defect is found, say so and note remaining test/runtime uncertainty.
7. Recommend the smallest regression test that would catch each real issue.

## Output

Findings first with concrete path/function evidence, then validation gaps. Avoid style-only commentary unless style creates a defect.
