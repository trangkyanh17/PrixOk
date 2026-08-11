---
name: mcp-builder
description: "Design, implement, review, and debug Model Context Protocol servers and tool integrations. Use when the user asks to build an MCP server, expose an API as MCP tools/resources/prompts, choose MCP transports, or improve MCP tool schemas and reliability."
compatibility: "Requires current MCP documentation for protocol-sensitive work; execution may require an MCP SDK and network access."
metadata:
  atri-privacy: "auto"
  atri-worker-eligible: "false"
  atri-risk: "high"
  atri-model-hint: "vertex"
  atri-triggers: "mcp server; mcp builder; model context protocol; fastmcp; streamable http mcp; stdio mcp; mcp tool; mcp resource; mcp prompt; xây mcp; xay mcp"
---

# MCP Builder

Build MCP integrations that are easy for an LLM to discover, call, debug, and operate safely.

## Workflow

1. Define the external system and the real user workflows before designing tools.
2. Read the current official MCP specification when protocol details or transports matter; MCP evolves and old examples can become stale.
3. Choose transport intentionally:
   - local subprocess integrations usually fit `stdio`;
   - remote services usually fit Streamable HTTP.
4. Design a coherent tool namespace with action-oriented names and concise descriptions.
5. Keep input schemas explicit. Prefer enums and bounded fields over free-form strings when the domain is constrained.
6. Return focused structured results. Add pagination/filtering for large collections rather than dumping entire datasets.
7. Make errors actionable: distinguish authentication, authorization, validation, not-found, quota/rate-limit, network, and upstream failures.
8. Treat credentials as server-side configuration. Never expose secrets through tool descriptions, results, logs, or resource URIs.
9. For Streamable HTTP, review origin validation, authentication, binding/interface choice, and request isolation.
10. Test discovery plus realistic multi-call workflows, not only single happy-path tool invocations.
11. Use MCP Inspector or equivalent protocol-level debugging when available.

## Tool design

Prefer primitives that compose well, but add workflow-level tools when they remove repeated error-prone sequences. Avoid giant tools with dozens of unrelated optional parameters.

Every write/delete tool must make its side effects obvious. Read tools should be safe to retry.

## Evaluation

Read `references/design-checklist.md` before finalizing a server or reviewing an MCP implementation.

## Atri privacy

MCP work stays on Vertex by default because integrations commonly involve private endpoints, credentials, production APIs, or tool permissions. Public workers may still be used indirectly for sanitized generic subproblems if Atri's supervisor explicitly isolates them.
