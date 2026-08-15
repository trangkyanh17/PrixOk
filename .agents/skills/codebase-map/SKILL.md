---
name: codebase-map
description: "Map an unfamiliar repository into entry points, modules, state, data flow, tests, integrations, and deployment boundaries before deeper debugging or modification. Use when the user asks how a codebase is structured or when a repo-wide task needs orientation first."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "coding"
  atri-permission: "authorized"
  atri-stage: "20"
  atri-capabilities: "repo-map; dependency-trace; architecture"
  atri-triggers: "map codebase; codebase map; cấu trúc repo; cau truc repo; map repo; kiến trúc source; kien truc source; luồng code; luong code; entry point repo"
---

# Codebase Map

Build a compact architecture map before proposing repo-wide changes.

## Workflow

1. Identify runtime entry points and process boundaries.
2. Map primary packages/modules and what state each owns.
3. Trace configuration, persistence, external integrations, background jobs, and user-facing handlers.
4. Locate tests and deployment/runtime scripts that protect the critical paths.
5. Record important invariants and version markers instead of listing every file.
6. When the task targets one feature, trace only the paths that can affect that feature.
7. Hand the resulting map to repo-auditor, code-debugger, or code-reviewer as needed.

## Output

Return an architecture map with entry points, state owners, critical flows, tests, and the minimal file set relevant to the task.
