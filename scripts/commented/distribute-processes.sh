#!/usr/bin/env bash
set -euo pipefail

# Resuelve siempre la raíz Atlanticus desde la ubicación del script.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DISTRIBUTOR="${ROOT}/scripts/distribute-processes.py"
PYTHON_VERSION="3.14.2"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

# La interfaz humana evita que el desarrollador tenga que recordar la invocación interna de UV.
show_help() {
    cat <<'EOF'
Usage:
  scripts/distribute-processes.sh DISTRIBUTION PROCESS [PROCESS ...]
  scripts/distribute-processes.sh DISTRIBUTION --all

Processes:
  01  pi-web-api
  02  notpii
  03  dispatch
  04  blockgrade
  05  fabrica
  06  remanentes
  21  kpis
  22  kpis-historian
  41  kpis-delivery

Output:
  distribution/DISTRIBUTION/
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    show_help
    exit 0
fi

[[ "$#" -ge 2 ]] || {
    show_help >&2
    exit 1
}

# El wrapper fija Python 3.14.2 y delega la lógica al distribuidor Python.
require_command uv
uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
    "${DISTRIBUTOR}" \
    --repository-root "${ROOT}" \
    "$@"
