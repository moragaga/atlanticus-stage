#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="3.14.2"
PYTEST_VERSION="9.1.1"
RUFF_VERSION="0.15.22"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADA_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
RUFF_CONFIG="${SCRIPT_DIR}/pyproject.toml"

if [[ "${1:-}" == "--clean" ]]; then
    rm -rf \
        "${SCRIPT_DIR}/.pytest_cache" \
        "${SCRIPT_DIR}/.ruff_cache" \
        "${SCRIPT_DIR}/tests/__pycache__" \
        "${SCRIPT_DIR}/commented/__pycache__" \
        "${SCRIPT_DIR}/__pycache__"
fi

echo "[1/6] Applying safe Ruff fixes to process tooling source"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff check --fix --exit-zero --config "${RUFF_CONFIG}" \
    "${SCRIPT_DIR}/process_bundle.py" "${SCRIPT_DIR}/tests"

echo "[2/6] Formatting process tooling source"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff format --config "${RUFF_CONFIG}" \
    "${SCRIPT_DIR}/process_bundle.py" "${SCRIPT_DIR}/tests"

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
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        --with "ruff==${RUFF_VERSION}" \
        ruff check --config "${RUFF_CONFIG}" \
        "${SCRIPT_DIR}/process_bundle.py" "${SCRIPT_DIR}/tests"

run_validation \
    "Ruff format verification" \
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        --with "ruff==${RUFF_VERSION}" \
        ruff format --check --config "${RUFF_CONFIG}" \
        "${SCRIPT_DIR}/process_bundle.py" "${SCRIPT_DIR}/tests"

run_validation \
    "Pytest" \
    bash -c "cd \"${ADA_ROOT}\" && uv run --python \"${PYTHON_VERSION}\" --no-python-downloads --no-project --with \"pytest==${PYTEST_VERSION}\" python -m pytest -ra scripts/processes/tests"

run_validation \
    "Commented mirror compilation" \
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        python -m compileall -q "${SCRIPT_DIR}/commented"

if (( validation_failed != 0 )); then
    echo "ADA process tooling validation failed: ${SCRIPT_DIR}" >&2
    exit 1
fi

echo "ADA process tooling validated: ${SCRIPT_DIR}"
