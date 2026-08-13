#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$ROOT"

PACKAGES=(
  kernel
  configuration
  datasets
  datasets-parquet
  datasets-runtime
  observability
  observability-azure
  state
  runtime
)

CLEAN=0
if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [--clean]" >&2
  exit 2
fi
if [[ $# -eq 1 ]]; then
  if [[ "$1" != "--clean" ]]; then
    echo "Usage: $0 [--clean]" >&2
    exit 2
  fi
  CLEAN=1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required." >&2
  exit 1
fi

CACHE_ARG=""
if [[ "$CLEAN" -eq 1 ]]; then
  export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"
  CACHE_ARG="--no-cache"
  rm -rf .venv dist
  for package in "${PACKAGES[@]}"; do
    rm -rf "$package/build" "$package/.pytest_cache" "$package/.ruff_cache"
    find "$package" -maxdepth 2 -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true
  done
fi

PYTHON_BIN="$(uv python find 3.14.2 --no-python-downloads)"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 14, 2) else 1)'

run() {
  printf '> '
  printf '%q ' "$@"
  printf '\n'
  "$@"
}

run uv lock \
  --python "$PYTHON_BIN" \
  --no-python-downloads \
  $CACHE_ARG \
  --check

run uv sync \
  --python "$PYTHON_BIN" \
  --no-python-downloads \
  $CACHE_ARG \
  --all-packages \
  --group dev \
  --frozen \
  --no-editable

UV_RUN=(
  uv run
  --python "$PYTHON_BIN"
  --no-python-downloads
  --no-sync
)

run "${UV_RUN[@]}" ruff check .
run "${UV_RUN[@]}" ruff format --check .

for package in "${PACKAGES[@]}"; do
  run "${UV_RUN[@]}" pytest "$package/tests"
done

run "${UV_RUN[@]}" python -c \
  'import atlanticus.configuration, atlanticus.datasets, atlanticus.datasets.parquet, atlanticus.datasets.runtime, atlanticus.kernel, atlanticus.observability, atlanticus.observability_azure, atlanticus.runtime, atlanticus.state'

rm -rf dist
mkdir -p dist

for package in "${PACKAGES[@]}"; do
  rm -rf "$package/build"
  find "$package" -maxdepth 2 -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true

  run uv build "$package" \
    --python "$PYTHON_BIN" \
    --no-python-downloads \
    $CACHE_ARG \
    --wheel \
    --out-dir dist
done

wheel_count="$(find dist -maxdepth 1 -type f -name '*.whl' | wc -l | tr -d ' ')"
if [[ "$wheel_count" != "9" ]]; then
  echo "Expected 9 wheels in dist, found $wheel_count." >&2
  exit 1
fi

echo "Backend validation passed: 9 packages, 9 wheels."
