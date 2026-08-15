---
name: project-planner
description: "Plan and continue multi-step work inside Atri's active private project workspace. Use when the user wants a persistent project context, a staged implementation plan, or continuity across related tasks."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "low"
  atri-model-hint: "vertex"
  atri-permission: "authorized"
  atri-stage: "5"
  atri-capabilities: "project-context; planning; task-chain"
  atri-triggers: "project này; project nay; dự án này; du an nay; kế hoạch project; ke hoach project; làm tiếp project; lam tiep project; chia bước; chia buoc; plan task; project context"
---

# Project Planner

Use the active Atri project workspace as private continuity, not as a substitute for current evidence.

## Workflow

1. Read the active project summary and recent notes supplied by the runtime.
2. Reconcile the current request with the project's stated goal and constraints.
3. Build the smallest ordered plan that can reach the next verifiable milestone.
4. Mark dependencies and validation gates before mutation-heavy steps.
5. Prefer reusing existing artifacts, logs, code maps, and decisions rather than asking the user to repeat them.
6. Do not assume old project notes are still true when current runtime/source evidence conflicts.
7. Keep private project context on Vertex; never include it in public-worker prompts.

## Output

Give the next executable plan or result. Keep project continuity explicit only when it materially affects the task.
