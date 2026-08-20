#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="3.14.2"
RUFF_VERSION="0.15.22"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROCESS_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPOSITORY_ROOT="$(cd -- "${PROCESS_ROOT}/../../../.." && pwd)"
ARTIFACT_ROOT="${REPOSITORY_ROOT}/artifacts/processes"
ARTIFACT_PATH="${ARTIFACT_ROOT}/kpis-historian"
BUNDLER="${REPOSITORY_ROOT}/scopes/ada/scripts/processes/process_bundle.py"

if [[ "${1:-}" == "--clean" ]]; then
    rm -rf "${ARTIFACT_PATH}" "${PROCESS_ROOT}/.venv"
fi

if [[ ! -f "${BUNDLER}" ]]; then
    echo "Process bundle script not found: ${BUNDLER}" >&2
    exit 1
fi

echo "[1/6] Applying safe Ruff fixes to KPI Historian process source"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff check --fix --exit-zero --config "${PROCESS_ROOT}/pyproject.toml" \
    "${PROCESS_ROOT}/src" "${PROCESS_ROOT}/tests" "${PROCESS_ROOT}/commented"

echo "[2/6] Formatting KPI Historian process source"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    --with "ruff==${RUFF_VERSION}" \
    ruff format --config "${PROCESS_ROOT}/pyproject.toml" \
    "${PROCESS_ROOT}/src" "${PROCESS_ROOT}/tests" "${PROCESS_ROOT}/commented"

echo "[3/6] Building and validating transport artifact"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    "${BUNDLER}" \
    "${PROCESS_ROOT}" \
    --repository-root "${REPOSITORY_ROOT}" \
    --output-root "${ARTIFACT_ROOT}"

cd "${ARTIFACT_PATH}"

echo "[4/6] Verifying transport bundle contents"
for forbidden in tests commented docs scripts; do
    if [[ -e "${forbidden}" ]]; then
        echo "Transport bundle must not contain ${forbidden}: ${ARTIFACT_PATH}" >&2
        exit 1
    fi
done
for required in FIRST_STEP.txt .env.detail config.detail.json secrets.detail.json pyproject.toml uv.lock wheels src; do
    if [[ ! -e "${required}" ]]; then
        echo "Transport bundle is missing ${required}: ${ARTIFACT_PATH}" >&2
        exit 1
    fi
done

echo "[5/6] Installing locked transport runtime"
uv sync --python "${PYTHON_VERSION}" --no-python-downloads --no-cache --frozen

echo "[6/6] Verifying transport runtime"
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-sync python -c \
    "import sys; import ada.processes.kpis_historian; assert sys.version_info[:3] == (3, 14, 2), sys.version"

rm -rf .venv
find src -type d \( -name '*.egg-info' -o -name '*.dist-info' -o -name '__pycache__' \) -prune -exec rm -rf {} +
find src -type f -name '*.pyc' -delete
if find src -type d \( -name '*.egg-info' -o -name '*.dist-info' \) -print -quit | grep -q .; then
    echo "Transport bundle contains generated package metadata after validation: ${ARTIFACT_PATH}" >&2
    exit 1
fi

echo "KPI Historian transport artifact validated: ${ARTIFACT_PATH}"
