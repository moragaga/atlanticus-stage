#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CLEAN=false
if [[ "${1:-}" == "pi-contracts" ]]; then
    shift
fi
if [[ "${1:-}" == "--clean" ]]; then
    CLEAN=true
    shift
fi
if [[ "$#" -ne 0 ]]; then
    echo "Usage: ./scripts/validation/check.sh [pi-contracts] [--clean]" >&2
    exit 2
fi

PYTHON_VERSION="3.14.2"
UV_COMMON=(--python "$PYTHON_VERSION" --no-python-downloads)
if "$CLEAN"; then
    rm -rf .venv dist pi/contracts/build pi/contracts/src/atlanticus_pi_contracts.egg-info
    UV_COMMON+=(--no-cache)
fi

if [[ ! -f uv.lock ]]; then
    echo "Missing integrations/uv.lock. Bootstrap it once with: uv lock --python 3.14.2 --no-python-downloads" >&2
    exit 2
fi

run() {
    printf '> '
    printf '%q ' "$@"
    printf '\n'
    "$@"
}

run uv lock "${UV_COMMON[@]}" --check
run uv sync "${UV_COMMON[@]}" --only-group dev --frozen
run uv sync "${UV_COMMON[@]}" --package atlanticus-pi-contracts --no-default-groups --inexact --frozen --no-editable

run uv run "${UV_COMMON[@]}" --no-sync ruff check --fix pi/contracts
run uv run "${UV_COMMON[@]}" --no-sync ruff format pi/contracts

mapfile -t COMMENTED_FILES < <(find pi/contracts/commented -type f -name '*.py' -print | sort)
if [[ "${#COMMENTED_FILES[@]}" -gt 0 ]]; then
    run uv run "${UV_COMMON[@]}" --no-sync ruff check --fix "${COMMENTED_FILES[@]}"
    run uv run "${UV_COMMON[@]}" --no-sync ruff format "${COMMENTED_FILES[@]}"
fi

run uv run "${UV_COMMON[@]}" --no-sync ruff check pi/contracts
run uv run "${UV_COMMON[@]}" --no-sync ruff format --check pi/contracts
run uv run "${UV_COMMON[@]}" --no-sync pytest pi/contracts/tests/unit
run uv run "${UV_COMMON[@]}" --no-sync python -c 'import atlanticus.integrations.pi.contracts'

mkdir -p dist
run uv build pi/contracts "${UV_COMMON[@]}" --wheel --out-dir dist

run uv run "${UV_COMMON[@]}" --no-sync python - <<'PY'
from pathlib import Path
from zipfile import ZipFile

wheels = tuple(Path('dist').glob('atlanticus_pi_contracts-0.1.0-*.whl'))
assert len(wheels) == 1, wheels
with ZipFile(wheels[0]) as archive:
    names = set(archive.namelist())
    assert 'atlanticus/integrations/pi/contracts/py.typed' in names
    assert not any('/tests/' in name or '/commented/' in name for name in names)
PY

echo "Integrations validation passed: pi-contracts, 1 wheel."
