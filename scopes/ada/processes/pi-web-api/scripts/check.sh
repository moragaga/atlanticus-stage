#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="3.14.2"
RUFF_VERSION="0.15.22"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROCESS_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPOSITORY_ROOT="$(cd -- "${PROCESS_ROOT}/../../../.." && pwd)"
ARTIFACT_ROOT="${REPOSITORY_ROOT}/artifacts/processes"
ARTIFACT_PATH="${ARTIFACT_ROOT}/pi-web-api"
BUNDLER="${REPOSITORY_ROOT}/scopes/ada/scripts/processes/process_bundle.py"

if [[ "${1:-}" == "--clean" ]]; then
    rm -rf "${ARTIFACT_PATH}" "${PROCESS_ROOT}/.venv"
fi

if [[ ! -f "${BUNDLER}" ]]; then
    echo "Process bundle script not found: ${BUNDLER}" >&2
    exit 1
fi

echo "[1/8] Applying safe Ruff fixes to process source"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff check --fix --exit-zero --config "${PROCESS_ROOT}/pyproject.toml" \
    "${PROCESS_ROOT}/src" "${PROCESS_ROOT}/tests"

echo "[2/8] Formatting process source"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff format --config "${PROCESS_ROOT}/pyproject.toml" \
    "${PROCESS_ROOT}/src" "${PROCESS_ROOT}/tests"

echo "[3/8] Building process artifact"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    "${BUNDLER}" \
    "${PROCESS_ROOT}" \
    --repository-root "${REPOSITORY_ROOT}" \
    --output-root "${ARTIFACT_ROOT}"

cd "${ARTIFACT_PATH}"

echo "[4/8] Installing locked artifact environment"
uv sync --python "${PYTHON_VERSION}" --no-python-downloads --no-cache --group dev --frozen

echo "[5/8] Verifying Python runtime"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-sync python -c \
    "import sys; assert sys.version_info[:3] == (3, 14, 2), sys.version"

validation_failed=0

run_validation() {
    local label="$1"
    shift
    echo "[check] ${label}"
    if "$@"; then
        echo "[pass] ${label}"
    else
        echo "[fail] ${label}" >&2
        validation_failed=1
    fi
}

run_validation \
    "Ruff lint" \
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-sync ruff check .

run_validation \
    "Ruff format verification" \
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-sync ruff format --check .

run_validation \
    "Pytest" \
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-sync python -m pytest -ra tests

if [[ -d commented ]]; then
    run_validation \
        "Commented mirror compilation" \
        uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-sync python -m compileall -q commented
fi

rm -rf .venv

if (( validation_failed != 0 )); then
    echo "PI Web API process artifact validation failed: ${ARTIFACT_PATH}" >&2
    exit 1
fi

echo "PI Web API process artifact validated: ${ARTIFACT_PATH}"
