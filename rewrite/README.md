# PrixOk rewrite v150

Experimental Rust/Go/TypeScript rewrite. This branch is isolated from main and dev and must not be merged until parity and integration tests pass.

## Termux/Debian build helper

From the `rewrite` directory:

```bash
./termux-build.sh
```

The helper builds the optimized Rust native binaries and a stripped Go supervisor while capping parallel jobs at 2 by default to reduce RAM/heat spikes on Termux. Override with `ATRI_BUILD_JOBS=N` when appropriate.

Useful modes:

```bash
./termux-build.sh --supervisor-only
./termux-build.sh --full-check
ATRI_RUN_RACE=1 ./termux-build.sh --full-check
./termux-build.sh --web
```

`--full-check` runs Rust fmt/Clippy/tests plus Go fmt/vet/tests before the release build. The race detector is intentionally opt-in because it is resource-heavy on a phone.

## Termux/PROot MCP uv runtime

When the MCP lifecycle prewarms an uv-based plugin (`serena` or `semgrep`), the supervisor now prepares the uv environment automatically before any MCP child starts:

- the directory containing `ATRI_UVX` (default `/app/mltbenv/bin/uvx`) is prepended to the supervisor child `PATH`, allowing Serena language-server subprocesses to find `uvx`/`uv`;
- `UV_LINK_MODE` defaults to `copy`, avoiding hardlink failures seen under Termux/PROot filesystems;
- `UV_CACHE_DIR` defaults to an isolated rewrite cache under `${XDG_CACHE_HOME:-$HOME/.cache}/atri-rewrite-v150/uv`, avoiding reuse of unrelated or damaged production uv caches;
- the cache directory is created before prewarm starts.

Explicit overrides remain available:

```bash
ATRI_MCP_UV_LINK_MODE=hardlink
ATRI_MCP_UV_CACHE_DIR=/path/to/cache
ATRI_UVX=/path/to/uvx
```

Existing `UV_LINK_MODE` / `UV_CACHE_DIR` are also respected when no `ATRI_MCP_*` override is supplied. HTTP-only MCP lifecycle runs (for example Context7/GitHub only) do not mutate the uv environment.

The supervisor defaults for `ATRI_MCP_PREWARM_TIMEOUT` and `ATRI_MCP_REQUEST_TIMEOUT` are 240 seconds. This leaves headroom for a cold Serena/Pyright startup on ARM64 Termux while the coordinated supervisor shutdown timeout remains separately bounded.

## One-command Termux validation

After CI is green, the live phone validation can be run from the `rewrite` directory with one command:

```bash
./termux-all-in-one.sh
```

The script is intentionally scoped to the rewrite clone. It validates the expected branch and clean tracked tree, stops only an existing `atri-supervisor` whose `/proc/<pid>/cwd` matches the same rewrite directory, rebuilds the supervisor (or performs a full build if native binaries are missing), runs Go tests and the native SHA-256 smoke test, then exercises Context7 + Serena + Semgrep together.

The MCP phase checks combined startup/health, samples RSS, injects a failure into only the Semgrep child and verifies automatic reconnect, sends SIGTERM and checks bounded shutdown/orphan cleanup, then starts the combined stack a second time to verify restart behavior.

The watchdog phase is an isolated canary: it creates a unique temporary tmux session and temporary health/network/repair/launcher helpers. It checks the healthy/network path and the unhealthy/repair path without using the production bot session or production helper paths. The canary session is removed on exit.

The supervisor accepts `ATRI_LOG_TIMEZONE` (and falls back to `TZ`) for log timestamps. The all-in-one script defaults this to `Asia/Ho_Chi_Minh` so the Termux report matches the phone's local Vietnam time.

The final report contains a PASS/FAIL table plus full MCP/watchdog logs and is written to `/storage/emulated/0/Download` or `/sdcard/Download` when writable, otherwise `rewrite/target`.

Useful tuning variables:

```bash
ATRI_BUILD_JOBS=2
ATRI_ALL_IN_ONE_STARTUP_TIMEOUT=300
ATRI_ALL_IN_ONE_HEALTH_INTERVAL=15
ATRI_ALL_IN_ONE_SOAK_SECONDS=35
ATRI_LOG_TIMEZONE=Asia/Ho_Chi_Minh
```
