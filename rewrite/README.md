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
