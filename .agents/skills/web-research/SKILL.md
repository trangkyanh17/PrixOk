---
name: web-research
description: "Research current public information on the web, compare reliable sources, verify claims, and synthesize cited findings. Use when the user asks to search online, verify current facts, find sources, or research a public topic."
metadata:
  atri-privacy: "public"
  atri-worker-eligible: "true"
  atri-risk: "low"
  atri-model-hint: "research"
  atri-permission: "authorized"
  atri-stage: "20"
  atri-capabilities: "web-search; source-verification; research"
  atri-triggers: "tìm kiếm trên mạng; tim kiem tren mang; tìm trên mạng; search web; web research; research online; tìm nguồn; verify online; kiểm tra trên mạng; latest public information"
---

# Web Research

Research public, current information and return a source-grounded synthesis.

## Workflow

1. Restate the research question internally and identify which claims are time-sensitive.
2. Prefer primary sources: official documentation, standards, first-party product pages, government data, or original research.
3. Use reputable secondary sources only when primary material is unavailable or when independent reporting is itself relevant.
4. Cross-check high-impact claims when practical.
5. Distinguish:
   - directly supported facts,
   - source disagreement,
   - inference,
   - unknown or unavailable information.
6. Preserve dates, versions, model IDs, prices, quotas, and other unstable facts exactly as sourced.
7. Cite the claims that drive the conclusion.
8. Do not invent browsing, citations, quotes, or source contents.

## Privacy

Only public task material may go to public workers. If the request includes account data, private files, production source, secrets, or private messages, let Atri's privacy gate keep the task on Vertex.

## Output

Lead with the answer, then the evidence and tradeoffs. Do not dump a long bibliography when inline citations are enough.
