#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON_VERSION="3.14.2"
RUFF_VERSION="0.15.22"
PYTEST_VERSION="9.1.1"

# El gate local valida el generador sin convertir deployment/local en otro proyecto Python independiente.
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff check \
    "${ROOT}/deployment/local/generate_compose.py" \
    "${ROOT}/deployment/local/tests"

# El formato se verifica explícitamente y nunca modifica archivos durante el gate.
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff format --check \
    "${ROOT}/deployment/local/generate_compose.py" \
    "${ROOT}/deployment/local/tests"

# Las pruebas cubren defaults, overrides, seguridad del .env y ambos modos de volumen.
PYTHONPATH="${ROOT}/deployment/local" \
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "pytest==${PYTEST_VERSION}" \
    python -m pytest -q "${ROOT}/deployment/local/tests"

# Los helpers shell se validan al menos sintácticamente en el mismo gate.
bash -n "${ROOT}/scripts/local-process.sh"
bash -n "${ROOT}/scripts/commented/local-process.sh"
