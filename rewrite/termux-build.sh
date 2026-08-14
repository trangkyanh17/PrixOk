#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BUILD_JOBS="${ATRI_BUILD_JOBS:-2}"
FULL_CHECK=0
BUILD_WEB=0
SUPERVISOR_ONLY=0
HOST_WATCHDOG_ONLY=0

usage() {
  cat <<'USAGE'
Usage: ./termux-build.sh [--full-check] [--web] [--supervisor-only] [--host-watchdog-only]

Default:
  Build optimized Rust native binaries and the stripped Debian/PROot Go supervisor.

Options:
  --full-check          Run Rust fmt/clippy/tests and Go fmt/vet/tests before build.
  --web                 Also install/typecheck the TypeScript helper.
  --supervisor-only     Skip Rust native build and build only the Debian/PROot Go supervisor.
  --host-watchdog-only  Cross-build only the Android/arm64 Go supervisor used by the Termux-host watchdog canary.

Environment:
  ATRI_BUILD_JOBS=N     Parallel build jobs. Defaults to 2 to avoid Termux RAM/heat spikes.
  ATRI_RUN_RACE=1       With --full-check, also run the Go race detector (resource-heavy).
USAGE
}

while (($#)); do
  case "$1" in
    --full-check)
      FULL_CHECK=1
      ;;
    --web)
      BUILD_WEB=1
      ;;
    --supervisor-only)
      SUPERVISOR_ONLY=1
      ;;
    --host-watchdog-only)
      HOST_WATCHDOG_ONLY=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ((SUPERVISOR_ONLY == 1 && HOST_WATCHDOG_ONLY == 1)); then
  echo "--supervisor-only and --host-watchdog-only are mutually exclusive" >&2
  exit 2
fi
if ((BUILD_WEB == 1 && HOST_WATCHDOG_ONLY == 1)); then
  echo "--web cannot be combined with --host-watchdog-only" >&2
  exit 2
fi
if ! [[ "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ATRI_BUILD_JOBS must be a positive integer; got: $BUILD_JOBS" >&2
  exit 2
fi

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 127
  fi
}

need go
if ((SUPERVISOR_ONLY == 0 && HOST_WATCHDOG_ONLY == 0)); then
  need cargo
fi
if ((BUILD_WEB == 1)); then
  need npm
fi

export CARGO_BUILD_JOBS="$BUILD_JOBS"
export GOMAXPROCS="${GOMAXPROCS:-$BUILD_JOBS}"

mkdir -p "$ROOT_DIR/target/release"

echo "[build] root=$ROOT_DIR jobs=$BUILD_JOBS"

if ((HOST_WATCHDOG_ONLY == 1)); then
  cd "$ROOT_DIR/supervisor"
  if ((FULL_CHECK == 1)); then
    unformatted="$(gofmt -l .)"
    if [[ -n "$unformatted" ]]; then
      echo "Go files need gofmt:" >&2
      printf '%s\n' "$unformatted" >&2
      exit 1
    fi
    echo "[check] go vet"
    go vet ./...
    echo "[check] go tests"
    go test ./...
    if [[ "${ATRI_RUN_RACE:-0}" == "1" ]]; then
      echo "[check] go race (resource-heavy)"
      go test -race -count=1 ./...
    fi
  fi

  echo "[build] Android/arm64 host watchdog supervisor"
  CGO_ENABLED=0 GOOS=android GOARCH=arm64 \
    go build -trimpath -ldflags='-s -w' \
    -o "$ROOT_DIR/target/release/atri-supervisor-android-arm64" .
  echo "[ok] host watchdog build complete"
  ls -lh "$ROOT_DIR/target/release/atri-supervisor-android-arm64"
  exit 0
fi

if ((SUPERVISOR_ONLY == 0)); then
  cd "$ROOT_DIR"
  [[ -f Cargo.lock ]] || { echo "Missing Cargo.lock" >&2; exit 1; }
  if ((FULL_CHECK == 1)); then
    echo "[check] rust fmt"
    cargo fmt --all -- --check
    echo "[check] rust clippy"
    cargo clippy --locked --workspace --all-targets -- -D warnings
    echo "[check] rust tests"
    cargo test --locked --workspace
  fi

  echo "[build] rust release"
  cargo build --locked --workspace --release
fi

cd "$ROOT_DIR/supervisor"
if ((FULL_CHECK == 1)); then
  unformatted="$(gofmt -l .)"
  if [[ -n "$unformatted" ]]; then
    echo "Go files need gofmt:" >&2
    printf '%s\n' "$unformatted" >&2
    exit 1
  fi
  echo "[check] go vet"
  go vet ./...
  echo "[check] go tests"
  go test ./...
  if [[ "${ATRI_RUN_RACE:-0}" == "1" ]]; then
    echo "[check] go race (resource-heavy)"
    go test -race -count=1 ./...
  fi
fi

echo "[build] go supervisor"
go build -trimpath -ldflags='-s -w' -o "$ROOT_DIR/target/release/atri-supervisor" .

if ((BUILD_WEB == 1)); then
  cd "$ROOT_DIR/web"
  [[ -f package-lock.json ]] || { echo "Missing package-lock.json" >&2; exit 1; }
  echo "[check] web dependencies/typecheck"
  npm ci --ignore-scripts --no-audit --no-fund
  npm run build
fi

echo "[ok] build complete"
if ((SUPERVISOR_ONLY == 0)); then
  for binary in \
    "$ROOT_DIR/target/release/atri-native" \
    "$ROOT_DIR/target/release/memory-store" \
    "$ROOT_DIR/target/release/history-normalize"; do
    [[ -f "$binary" ]] && ls -lh "$binary"
  done
fi
ls -lh "$ROOT_DIR/target/release/atri-supervisor"
