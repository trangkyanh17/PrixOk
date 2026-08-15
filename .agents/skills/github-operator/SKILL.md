---
name: github-operator
description: "Operate on a GitHub repository through the connected GitHub tool: inspect branches/commits/PRs, prepare focused changes, run CI gates, and merge only when explicitly authorized. Use for repository work the owner asks Atri to perform on GitHub."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "high"
  atri-model-hint: "vertex"
  atri-permission: "owner"
  atri-stage: "80"
  atri-capabilities: "github; repo-write; ci-gate; rollback"
  atri-triggers: "@github; sửa github; sua github; triển khai github; trien khai github; push repo; tạo pull request; tao pull request; merge pr; làm trên github; lam tren github"
---

# GitHub Operator

Operate conservatively and preserve repository invariants.

## Workflow

1. Confirm the exact repository and canonical base branch from tool state; never infer a different repo from similar names.
2. Inspect relevant source and current CI before writing.
3. Create a dedicated branch from the exact canonical base SHA.
4. Keep the change surface bounded to the user's requested feature/fix.
5. Add focused regression tests before considering the change complete.
6. Run exact-head functional CI and inspect real failure logs instead of guessing.
7. Use the repository's established signed-staging/signature workflow when present; never bypass a signature gate.
8. Merge only after required checks pass and with expected-head protection.
9. Verify the final main commit and post-merge CI.
10. Close unmerged experimental PRs that could later be merged accidentally.

## Guardrails

This skill is owner-only. Repository writes require explicit user authorization in the current task. Do not expose private source or credentials to public workers.

## Output

Report changed files, exact commit/PR state, tests, merge status, and any production step still requiring separate execution.
