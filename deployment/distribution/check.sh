#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_VERSION="3.14.2"
RUFF_VERSION="0.15.22"
PYTEST_VERSION="9.1.1"

bash -n "${ROOT}/scripts/distribute-processes.sh"
bash -n "${ROOT}/scripts/commented/distribute-processes.sh"
bash -n "${ROOT}/deployment/distribution/local-process.sh"
bash -n "${ROOT}/deployment/distribution/commented/local-process.sh"

uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff check \
    "${ROOT}/scripts/distribute-processes.py" \
    "${ROOT}/deployment/distribution/tests"

uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff format --check \
    "${ROOT}/scripts/distribute-processes.py" \
    "${ROOT}/deployment/distribution/tests"

uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "pytest==${PYTEST_VERSION}" \
    python -m pytest -q "${ROOT}/deployment/distribution/tests"
