---
name: skill-auditor
description: "Validate and audit Agent Skills for spec compliance, triggering quality, privacy metadata, worker eligibility, resource structure, and instruction quality. Use when reviewing a SKILL.md or checking whether an Atri skill is safe and reliable."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-triggers: "audit skill; validate skill; kiểm tra skill; kiem tra skill; skill audit; skill trigger test; skill quality; skill privacy; SKILL.md audit"
---

# Skill Auditor

Audit both format and behavior.

## Structural checks

1. Required frontmatter exists.
2. Name format/length and directory match are valid.
3. Description is non-empty, <=1024 chars, and says what + when.
4. Optional standard fields use the documented types.
5. Atri-specific fields live under `metadata`.
6. Main instructions are not unnecessarily large; long material moves to references.
7. Relative resource references stay inside the skill directory.

## Behavioral checks

Create positive and negative trigger examples. Flag descriptions or `atri-triggers` that are too broad. Confirm the skill adds concrete procedure/gotchas rather than generic advice.

## Privacy checks

Verify `atri-privacy` and `atri-worker-eligible` against the real data the skill expects. A public worker must never receive private source, account data, secrets, private logs, or private conversation history.

## Execution checks

Scripts are not trusted merely because they are bundled. Review script behavior, arguments, path traversal, destructive operations, and dependency assumptions before execution.

## Output

Return PASS/WARN/FAIL with specific fixes, not vague suggestions.
