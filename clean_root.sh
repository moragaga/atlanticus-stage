#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${1:-}"

remove_directory() {
    local directory="$1"

    if [[ "$DRY_RUN" == "--dry-run" ]]; then
        printf 'Would remove directory: %s\n' "$directory"
    else
        printf 'Removing directory: %s\n' "$directory"
        rm -rf -- "$directory"
    fi
}

remove_file() {
    local file="$1"

    if [[ "$DRY_RUN" == "--dry-run" ]]; then
        printf 'Would remove file: %s\n' "$file"
    else
        printf 'Removing file: %s\n' "$file"
        rm -f -- "$file"
    fi
}

export DRY_RUN
export -f remove_directory
export -f remove_file

echo "Atlanticus clean started in: $ROOT"

find "$ROOT" \
    -path "$ROOT/.git" -prune \
    -o -type d \( \
        -name '__pycache__' \
        -o -name '.pytest_cache' \
        -o -name '.mypy_cache' \
        -o -name '.ruff_cache' \
        -o -name '.hypothesis' \
        -o -name '.tox' \
        -o -name '.nox' \
        -o -name '.ipynb_checkpoints' \
        -o -name 'htmlcov' \
        -o -name '.venv' \
        -o -name 'venv' \
        -o -name '.virtualenv' \
        -o -name 'virtualenv' \
        -o -name 'build' \
        -o -name 'dist' \
        -o -name '*.egg-info' \
        -o -name 'resultados' \
        -o -name 'volumen' \
        -o -name 'artifacts' \
        -o -name '.local-assets' \
        -o -name '.local-data' \
        -o -name '.local-volume' \
        -o -name '.runtime' \
    \) -prune \
    -o -type f \( \
        -name '*:Zone.Identifier' \
        -o -name '*.pyc' \
        -o -name '*.pyo' \
        -o -name '*.pyd' \
        -o -name '.DS_Store' \
        -o -name 'Thumbs.db' \
        -o -name 'desktop.ini' \
        -o -name '.coverage' \
        -o -name '.coverage.*' \
        -o -name 'coverage.xml' \
        -o -name '*.tmp' \
        -o -name '*.temp' \
        -o -name '*~' \
    \) -exec bash -c 'remove_file "$1"' _ {} \;

find "$ROOT" \
    -path "$ROOT/.git" -prune \
    -o -type d \( \
        -name '__pycache__' \
        -o -name '.pytest_cache' \
        -o -name '.mypy_cache' \
        -o -name '.ruff_cache' \
        -o -name '.hypothesis' \
        -o -name '.tox' \
        -o -name '.nox' \
        -o -name '.ipynb_checkpoints' \
        -o -name 'htmlcov' \
        -o -name '.venv' \
        -o -name 'venv' \
        -o -name '.virtualenv' \
        -o -name 'virtualenv' \
        -o -name 'build' \
        -o -name 'dist' \
        -o -name '*.egg-info' \
        -o -name 'resultados' \
        -o -name 'volumen' \
        -o -name 'artifacts' \
        -o -name '.local-assets' \
        -o -name '.local-data' \
        -o -name '.local-volume' \
        -o -name '.runtime' \
    \) -prune -exec bash -c 'remove_directory "$1"' _ {} \;

echo "Atlanticus clean completed successfully."
