#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

uv lock --python 3.14.2
uv sync --python 3.14.2 --frozen --group dev
uv run --python 3.14.2 --frozen ruff check src tests
uv run --python 3.14.2 --frozen ruff format --check src tests
uv run --python 3.14.2 --frozen pytest tests
