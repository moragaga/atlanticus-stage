#!/usr/bin/env bash
set -eo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$ROOT"

PACKAGES="kernel configuration datasets datasets-parquet datasets-runtime observability observability-azure state runtime"
SELECTED=""
ORDERED_SELECTED=""
CLEAN=0
ALL=0

usage() {
  cat >&2 <<USAGE
Usage: $0 [module ...] [--clean]

Modules:
  kernel
  configuration
  datasets
  datasets-parquet
  datasets-runtime
  observability
  observability-azure
  state
  runtime

No modules validates the complete backend.
USAGE
}

contains_module() {
  case " $1 " in
    *" $2 "*) return 0 ;;
    *) return 1 ;;
  esac
}

distribution_name() {
  case "$1" in
    kernel) echo "atlanticus-kernel" ;;
    configuration) echo "atlanticus-configuration" ;;
    datasets) echo "atlanticus-datasets" ;;
    datasets-parquet) echo "atlanticus-datasets-parquet" ;;
    datasets-runtime) echo "atlanticus-datasets-runtime" ;;
    observability) echo "atlanticus-observability" ;;
    observability-azure) echo "atlanticus-observability-azure" ;;
    state) echo "atlanticus-state" ;;
    runtime) echo "atlanticus-job-runtime" ;;
  esac
}

import_name() {
  case "$1" in
    kernel) echo "atlanticus.kernel" ;;
    configuration) echo "atlanticus.configuration" ;;
    datasets) echo "atlanticus.datasets" ;;
    datasets-parquet) echo "atlanticus.datasets.parquet" ;;
    datasets-runtime) echo "atlanticus.datasets.runtime" ;;
    observability) echo "atlanticus.observability" ;;
    observability-azure) echo "atlanticus.observability_azure" ;;
    state) echo "atlanticus.state" ;;
    runtime) echo "atlanticus.runtime" ;;
  esac
}

for arg in "$@"; do
  case "$arg" in
    --clean)
      CLEAN=1
      ;;
    kernel|configuration|datasets|datasets-parquet|datasets-runtime|observability|observability-azure|state|runtime)
      if ! contains_module "$SELECTED" "$arg"; then
        SELECTED="$SELECTED $arg"
      fi
      ;;
    *)
      echo "Unknown validation module: $arg" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${SELECTED# }" ]]; then
  SELECTED="$PACKAGES"
  ALL=1
fi

for package in $PACKAGES; do
  if contains_module "$SELECTED" "$package"; then
    ORDERED_SELECTED="$ORDERED_SELECTED $package"
  fi
done

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required." >&2
  exit 1
fi

CACHE_ARG=""
if [[ "$CLEAN" -eq 1 ]]; then
  export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"
  CACHE_ARG="--no-cache"
  rm -rf .venv dist
  for package in $PACKAGES; do
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

if [[ "$ALL" -eq 1 ]]; then
  run uv sync \
    --python "$PYTHON_BIN" \
    --no-python-downloads \
    $CACHE_ARG \
    --all-packages \
    --group dev \
    --frozen \
    --no-editable
else
  SYNC_PACKAGE_ARGS=""
  for package in $ORDERED_SELECTED; do
    SYNC_PACKAGE_ARGS="$SYNC_PACKAGE_ARGS --package $(distribution_name "$package")"
  done

  run uv sync \
    --python "$PYTHON_BIN" \
    --no-python-downloads \
    $CACHE_ARG \
    --only-group dev \
    --frozen

  run uv sync \
    --python "$PYTHON_BIN" \
    --no-python-downloads \
    $CACHE_ARG \
    $SYNC_PACKAGE_ARGS \
    --no-default-groups \
    --inexact \
    --frozen \
    --no-editable
fi

UV_RUN=(
  uv run
  --python "$PYTHON_BIN"
  --no-python-downloads
  --no-sync
)

if [[ "$ALL" -eq 1 ]]; then
  run "${UV_RUN[@]}" ruff check .
  run "${UV_RUN[@]}" ruff format --check .
else
  for package in $ORDERED_SELECTED; do
    run "${UV_RUN[@]}" ruff check "$package"
    run "${UV_RUN[@]}" ruff format --check "$package"
  done
fi

for package in $ORDERED_SELECTED; do
  run "${UV_RUN[@]}" pytest "$package/tests"
  module="$(import_name "$package")"
  run "${UV_RUN[@]}" python -c "import $module"
done

rm -rf dist
mkdir -p dist

for package in $ORDERED_SELECTED; do
  rm -rf "$package/build"
  find "$package" -maxdepth 2 -type d -name '*.egg-info' -prune -exec rm -rf {} + 2>/dev/null || true

  run uv build "$package" \
    --python "$PYTHON_BIN" \
    --no-python-downloads \
    $CACHE_ARG \
    --wheel \
    --out-dir dist
done

selected_count=0
for package in $ORDERED_SELECTED; do
  selected_count=$((selected_count + 1))
done

wheel_count="$(find dist -maxdepth 1 -type f -name '*.whl' | wc -l | tr -d ' ')"
if [[ "$wheel_count" != "$selected_count" ]]; then
  echo "Expected $selected_count wheels in dist, found $wheel_count." >&2
  exit 1
fi

if [[ "$ALL" -eq 1 ]]; then
  echo "Backend validation passed: 9 packages, 9 wheels."
elif [[ "$selected_count" -eq 1 ]]; then
  echo "Backend validation passed: 1 selected package, 1 wheel."
else
  echo "Backend validation passed: $selected_count selected packages, $wheel_count wheels."
fi
