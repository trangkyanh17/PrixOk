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
