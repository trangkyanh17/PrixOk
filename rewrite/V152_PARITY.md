# V152 Gate B1 — deterministic decision parity

V152 Gate B1 runs only after V151 Gate A is healthy. It does **not** transfer Telegram ownership away from the Python worker and does not run a second AI request.

## What Gate B1 compares

The production Python path publishes bounded decision metadata to the existing loopback shadow ingress. The Go supervisor independently evaluates:

1. **Route parity** — `chat`, `web`, `tools`, `code`, attachment override, and explicit GitHub-MCP forcing.
2. **Vertex execution-plan parity** — runtime/base/resolved model, automatic/manual thinking, Vertex provider overrides, and the expected tool profile.
3. **Tool-boundary parity** — the name of an actual function call selected by the production model is checked against the active mode/profile. The tool is still executed only once by the existing Python production path.

The Go `/healthz` response exposes counters only:

- `route_total`, `route_match`, `route_mismatch`
- `plan_total`, `plan_match`, `plan_mismatch`
- `tool_total`, `tool_match`, `tool_mismatch`
- parity `accepted` / `rejected`

Route text is used ephemerally inside the loopback request so the Go implementation can calculate routing independently. It is not written to the parity status, ready marker, report, or Go log. Tool arguments/results and model output are never sent to the parity engine.

## Explicitly out of scope

Gate B1 does **not**:

- call Vertex/Gemini a second time;
- call a free-provider worker a second time;
- invoke MCP/plugins or Google/weather tools a second time;
- send/edit Telegram messages;
- compare generative answer text;
- change `start.sh` or Telegram ownership;
- pull/reset/checkout the live `/app` tree.

Generative output shadowing is a later Gate B2 and must not be enabled until B1 has zero unexplained mismatches in production.

## Production patch boundary

`rewrite/v152_parity_patch.py` is the only V152 source mutation mechanism. It backs up and touches only:

- `/app/bot/modules/atri_ai.py`
- `/app/bot/modules/atri_v152_parity.py`

The patcher uses exact anchors, compiles the resulting Python, verifies hashes, and records a manifest. Rollback refuses to overwrite `atri_ai.py` if it changed after V152 was applied.

`rewrite/termux-v152-parity-canary.sh` also requires V151 Gate A to remain healthy before and after V152 activation. A failed V152 apply restores the pre-V152 source/runtime and keeps V151 active.

## Acceptance criteria

A V152 B1 canary is infrastructure-PASS only when all of the following hold:

- production has one V150 owner, no legacy watchdog, bot lock held, and local health healthy;
- V151 observer-ready and loopback ingress are still healthy;
- V152 patch verify/compile/hash checks pass;
- V150 supervisor upgrade succeeds without changing unrelated live source;
- controlled bot restart returns healthy with a fresh V151 observer-ready marker;
- a synthetic local route decision is independently matched by Go;
- boot-hook lock FD is not inherited;
- rollback metadata is complete.

The subsequent real-message proof should exercise normal production messages and finish with one `status` report. Gate B1 is considered parity-PASS only when observed real traffic has no `route_mismatch`, `plan_mismatch`, `tool_mismatch`, or rejected parity events.
