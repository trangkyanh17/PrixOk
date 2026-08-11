---
name: frontend-design
description: "Design or redesign distinctive production-quality frontend interfaces, web pages, dashboards, components, and visual systems. Use when the user asks to create, style, beautify, or substantially redesign a web UI, landing page, React component, HTML/CSS interface, or dashboard."
metadata:
  atri-privacy: "auto"
  atri-worker-eligible: "true"
  atri-risk: "low"
  atri-model-hint: "coding"
  atri-triggers: "frontend design; thiết kế frontend; thiet ke frontend; redesign ui; thiết kế ui web; thiet ke ui web; landing page design; dashboard design; beautify ui; style web page; react ui design"
---

# Frontend Design

Produce an interface with a deliberate visual system tied to the subject, not a generic template with interchangeable decoration.

## Workflow

1. Establish the concrete subject, audience, primary task, content hierarchy, and technical constraints.
2. Make a small design system before implementation:
   - color roles;
   - type roles and scale;
   - spacing rhythm;
   - radius/border/elevation policy;
   - interaction/motion policy.
3. Define one signature visual idea that belongs to this product or subject.
4. Sketch the major information architecture before writing detailed CSS.
5. Implement responsive behavior from the start rather than patching mobile at the end.
6. Use semantic HTML and accessible interaction patterns.
7. Keep keyboard focus visible and respect reduced-motion preferences where motion is used.
8. Avoid decorative elements that do not communicate hierarchy, state, identity, or interaction.
9. Review the rendered result and remove generic/templated choices that are not justified by the brief.
10. Verify common breakpoints, empty/error/loading states, and long content.

## Code quality

Keep design tokens centralized. Avoid conflicting selector specificity and duplicated magic values. Components should expose intent through props/variants rather than scattered one-off overrides.

## Existing private source

The skill is worker-eligible only for public-safe task material. Existing private frontend source remains protected by Atri's global privacy gate.

Read `references/review-checklist.md` before declaring a UI finished.
