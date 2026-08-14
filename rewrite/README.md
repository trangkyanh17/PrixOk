# PrixOk rewrite v150

Experimental Rust/Go/TypeScript rewrite. This branch remains isolated from `main`; production validation and deployment work must stay on `rewrite/rust-go-ts-v150` until an explicit merge decision is made.

## Termux/Debian build helper

From the `rewrite` directory:

```bash
./termux-build.sh
```

The helper builds the optimized Rust native binaries and a stripped Go supervisor while capping parallel jobs at 2 by default to reduce RAM/heat spikes on Termux. Override with `ATRI_BUILD_JOBS=N` when appropriate.

Useful modes:

```bash
./termux-build.sh --supervisor-only
./termux-build.sh --host-watchdog-only
./termux-build.sh --full-check
ATRI_RUN_RACE=1 ./termux-build.sh --full-check
./termux-build.sh --web
```

`--host-watchdog-only` cross-builds the Android/arm64 supervisor used by the Termux-host production watchdog. `--full-check` runs Rust fmt/Clippy/tests plus Go fmt/vet/tests before release builds. The race detector is intentionally opt-in on the phone because it is resource-heavy.

## Termux/PROot MCP uv runtime

When the MCP lifecycle prewarms an uv-based plugin (`serena` or `semgrep`), the supervisor prepares the uv environment automatically before any MCP child starts:

- the directory containing `ATRI_UVX` (default `/app/mltbenv/bin/uvx`) is prepended to the supervisor child `PATH`;
- `UV_LINK_MODE` defaults to `copy`, avoiding hardlink failures under Termux/PROot filesystems;
- `UV_CACHE_DIR` defaults to an isolated rewrite cache under `${XDG_CACHE_HOME:-$HOME/.cache}/atri-rewrite-v150/uv`;
- the cache directory is created before prewarm starts.

Explicit overrides remain available:

```bash
ATRI_MCP_UV_LINK_MODE=hardlink
ATRI_MCP_UV_CACHE_DIR=/path/to/cache
ATRI_UVX=/path/to/uvx
```

Existing `UV_LINK_MODE` / `UV_CACHE_DIR` are respected when no `ATRI_MCP_*` override is supplied. HTTP-only MCP lifecycle runs do not mutate the uv environment.

The supervisor defaults for `ATRI_MCP_PREWARM_TIMEOUT` and `ATRI_MCP_REQUEST_TIMEOUT` are 240 seconds. This leaves headroom for cold Serena/Pyright startup on ARM64 Termux while coordinated supervisor shutdown remains separately bounded.

## Validation history and production stages

The branch contains dedicated harnesses for isolated validation, production topology discovery, recovery/handoff, persistence, and reboot proof:

- `termux-all-in-one.sh`
- `termux-production-canary.sh`
- `termux-production-topology.sh`
- `termux-production-recovery-host.sh`
- `termux-v150-persistence-host.sh`
- `termux-v150-pre-reboot-check.sh`

These are validation/migration tools. They are not the normal ongoing upgrade path after V150 has taken production ownership.

## Managed production deploy / upgrade

After V150 production handoff and reboot persistence are proven, use:

```bash
bash "$HOME/termux-v150-deploy.sh" status
bash "$HOME/termux-v150-deploy.sh" install
bash "$HOME/termux-v150-deploy.sh" upgrade
bash "$HOME/termux-v150-deploy.sh" rollback
bash "$HOME/termux-v150-deploy.sh" cleanup-legacy
```

The source-controlled manager is `termux-v150-deploy.sh`. Full operational semantics, automatic rollback behavior, backup locations, and legacy restoration are documented in [`PRODUCTION_DEPLOY.md`](PRODUCTION_DEPLOY.md).

The deploy manager builds from the isolated `/opt/prixok-v150` clone and verifies the live `/app` source fingerprint before/after deployment. It never performs source update/reset/checkout/clean operations against `/app`.

## Reporting

Phone validation and production management scripts write reports to `/storage/emulated/0/Download` or `/sdcard/Download` when writable, with a state-directory fallback when external storage is unavailable.
