---
name: skill-creator
description: "Create, revise, and evaluate Agent Skills. Use when the user asks to create a skill, edit SKILL.md, improve triggering, add skill metadata/resources, or benchmark a skill."
metadata:
  atri-privacy: "auto"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-triggers: "tạo skill; tao skill; create skill; skill creator; SKILL.md; agent skill; sửa skill; edit skill; tối ưu skill; optimize skill"
---

# Skill Creator

Create reusable skills that follow the Agent Skills open format and Atri's privacy model.

## Workflow

1. Identify the repeatable task, required expertise, inputs, outputs, and failure modes.
2. Decide whether the skill needs only `SKILL.md` or also `scripts/`, `references/`, or `assets/`.
3. Write frontmatter with `name` and `description`. Put Atri-specific policy only under `metadata`.
4. Make the description explain both what the skill does and when it should trigger.
5. Keep core instructions concise and procedural. Move long references out of `SKILL.md`.
6. Add gotchas that the model would not reliably infer on its own.
7. For destructive or fragile workflows, use plan → validate → execute.
8. Create positive and negative trigger examples.
9. Validate structure and trigger quality before calling the skill finished.

## Atri metadata

Use string values:

- `atri-privacy`: `public`, `private`, or `auto`.
- `atri-worker-eligible`: `true` only if skill instructions and expected task material are safe for public workers.
- `atri-risk`: `low`, `medium`, or `high`.
- `atri-model-hint`: `vertex`, `coding`, `research`, or `auto`.
- `atri-triggers`: semicolon-separated concrete phrases used by Atri's deterministic fast matcher.

Privacy metadata is not a replacement for Atri's global privacy gate.

## Trigger quality

Prefer specific intent phrases. Avoid generic triggers such as `help`, `fix`, `data`, or `code` by themselves. Include Vietnamese and English variants when both are expected.

## Resources

Read `references/spec-summary.md` when checking format constraints.
Use `scripts/init_skill.py` only when a filesystem tool is available and the target directory is explicitly known.
