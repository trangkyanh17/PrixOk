# PrixOk rewrite V150

V150 is the Rust/Go/TypeScript runtime and Termux supervisor track for PrixOk. Its `main` integration is **additive**: the existing Python/Pyrogram bot remains the production Telegram worker, while V150 provides the validated supervisor/watchdog, MCP lifecycle, native helpers, runtime libraries and managed deployment tooling.

Merging V150 code does not authorize replacing Telegram message handling. Full replacement has a separate parity/canary gate documented in [`PORTING_STATUS.md`](PORTING_STATUS.md).

## Termux/Debian build helper

From the `rewrite` directory:

```bash
./termux-build.sh
```

The helper builds optimized Rust native binaries and a stripped Go supervisor while capping parallel jobs at 2 by default to reduce RAM/heat spikes on Termux. Override with `ATRI_BUILD_JOBS=N` when appropriate.

Useful modes:

```bash
./termux-build.sh --supervisor-only
./termux-build.sh --host-watchdog-only
./termux-build.sh --full-check
ATRI_RUN_RACE=1 ./termux-build.sh --full-check
./termux-build.sh --web
```

`--host-watchdog-only` cross-builds the Android/arm64 supervisor used by the Termux-host production watchdog. `--full-check` runs Rust fmt/Clippy/tests plus Go fmt/vet/tests before release builds. The race detector is intentionally opt-in on the phone because it is resource-heavy.

Rust builds require the committed `Cargo.lock` and use Cargo locked mode. Web builds require the committed `package-lock.json` and use `npm ci`, so a repository SHA resolves the same dependency graph in CI and on the managed build path.

## Production worker invariant

Production Telegram handling remains Python-backed:

```text
start.sh -> exec python3 -m bot
```

The canonical Termux launcher enters Debian `/app` with `RUN_SOURCE_UPDATE=0`. V150 supervises lifecycle/health but does not update or replace the customized live `/app` source tree during managed watchdog deployment.

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

Dedicated harnesses cover isolated validation, production topology discovery, recovery/handoff, persistence and reboot proof:

- `termux-all-in-one.sh`
- `termux-production-canary.sh`
- `termux-production-topology.sh`
- `termux-production-recovery-host.sh`
- `termux-v150-persistence-host.sh`
- `termux-v150-pre-reboot-check.sh`

These are validation/migration tools. They are not the normal ongoing upgrade path after V150 has taken production watchdog ownership.

## Managed production deploy / upgrade

`main` is the canonical source branch for the isolated `/opt/prixok-v150` build/deploy clone. The historical `rewrite/rust-go-ts-v150` branch is retired from the operational deployment path.

After V150 production handoff and reboot persistence are proven, use:

```bash
bash "$HOME/termux-v150-deploy.sh" status
bash "$HOME/termux-v150-deploy.sh" install
bash "$HOME/termux-v150-deploy.sh" upgrade
bash "$HOME/termux-v150-deploy.sh" rollback
bash "$HOME/termux-v150-deploy.sh" cleanup-legacy
```

The source-controlled manager is `termux-v150-deploy.sh`. Full operational semantics, automatic rollback behavior, backup locations, one-time branch migration and legacy restoration are documented in [`PRODUCTION_DEPLOY.md`](PRODUCTION_DEPLOY.md).

The deploy manager builds from the isolated `/opt/prixok-v150` clone and verifies the live `/app` source fingerprint before/after deployment. It requires the clone to be on clean `main` and never performs source update/reset/checkout/clean operations against `/app`.

## CI

`Rewrite V150` CI runs for relevant pull requests into `main` and relevant pushes on `main`. It verifies:

- Cargo lock presence, fmt/check/Clippy/tests/release build in locked mode;
- Go fmt/vet/tests/race/build and Android/arm64 cross-build;
- shell syntax and production helper self-tests;
- `main` as the operational V150 source branch;
- boot-hook lock lifecycle invariants, including closing FD 9 before the long-lived watchdog starts;
- the Python production-worker invariant;
- npm lock presence, `npm ci`, and TypeScript build.

## Reporting

Phone validation and production management scripts write reports to `/storage/emulated/0/Download` or `/sdcard/Download` when writable, with a state-directory fallback when external storage is unavailable.
