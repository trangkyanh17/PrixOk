---
name: code-debugger
description: "Diagnose and fix software bugs, exceptions, incorrect runtime behavior, and broken code paths. Use when the user asks to debug code, fix a bug, analyze an exception, or determine why code behaves incorrectly."
metadata:
  atri-privacy: "auto"
  atri-worker-eligible: "true"
  atri-risk: "medium"
  atri-model-hint: "coding"
  atri-permission: "authorized"
  atri-stage: "50"
  atri-capabilities: "debugging; code-analysis; test-design"
  atri-triggers: "debug code; fix bug; lỗi code; loi code; bắt bệnh code; bat benh code; exception python; traceback code; runtime bug; code không chạy; code khong chay"
---

# Code Debugger

Find the root cause before changing unrelated code.

## Workflow

1. Reproduce the failure mentally or with available safe tools.
2. Separate symptom, triggering input, failing layer, and root cause.
3. Inspect the actual relevant source instead of guessing unseen code.
4. Build the smallest explanation that accounts for all observed evidence.
5. Patch only the necessary code path.
6. Preserve unrelated behavior and existing invariants.
7. Compile, lint, or run a focused regression test when tools allow.
8. If the fix changes a public contract, configuration, state format, or concurrency behavior, call that out explicitly.

## Failure classification

Check for syntax/import errors, wrong assumptions about types/state, async misuse, race conditions, stale state, I/O failures, API contract mismatch, and fallback logic.

## Privacy

Generic/public examples may use workers. Private repositories, production paths, secrets, credentials, and account context remain governed by Atri's global privacy gate.

## Output

State root cause briefly, then the focused fix and verification result. Avoid speculative rewrites.
