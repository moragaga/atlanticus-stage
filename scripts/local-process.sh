#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${ROOT}/.runtime/local-deployment"
COMPOSE_FILE="${WORKSPACE}/compose.yaml"
GENERATOR="${ROOT}/deployment/local/generate_compose.py"
BUNDLER="${ROOT}/scopes/ada/scripts/processes/process_bundle.py"
PYTHON_VERSION="3.14.2"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

compose() {
    docker compose -f "${COMPOSE_FILE}" "$@"
}

require_compose_file() {
    [[ -f "${COMPOSE_FILE}" ]] || fail "Local Compose workspace not found. Run: scripts/local-process.sh up"
}

validate_tools() {
    require_command docker
    require_command uv
    docker compose version >/dev/null 2>&1 || fail "Docker Compose is not available"
}

validate_environment() {
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        "${GENERATOR}" validate \
        --repository-root "${ROOT}"
}

prepare_environment() {
    local volume_mode="$1"

    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        "${BUNDLER}" \
        --repository-root "${ROOT}" \
        --output-root "${ROOT}/artifacts/processes"

    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        "${GENERATOR}" prepare \
        --repository-root "${ROOT}" \
        --workspace-root "${WORKSPACE}" \
        --volume-mode "${volume_mode}"
}

command_up() {
    local volume_mode="named"
    if [[ "${1:-}" == "--bind" ]]; then
        volume_mode="bind"
        shift
    fi
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh up [--bind]"
    validate_tools
    validate_environment
    if [[ -f "${COMPOSE_FILE}" ]]; then
        compose down --remove-orphans
    fi
    prepare_environment "${volume_mode}"
    compose build --no-cache
    compose up -d
    compose ps
}

command_down() {
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh down"
    validate_tools
    require_compose_file
    compose down --remove-orphans
}

command_ps() {
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh ps"
    validate_tools
    require_compose_file
    compose ps
}

command_logs() {
    [[ "$#" -le 1 ]] || fail "Usage: scripts/local-process.sh logs [process]"
    validate_tools
    require_compose_file
    if [[ "$#" -eq 1 ]]; then
        compose logs -f "$1"
    else
        compose logs -f
    fi
}

command_run() {
    [[ "$#" -eq 1 ]] || fail "Usage: scripts/local-process.sh run <process>"
    validate_tools
    require_compose_file
    local process="$1"
    compose config --services | grep -Fx -- "${process}" >/dev/null \
        || fail "Local Compose service not found: ${process}"
    compose run --rm "${process}" --run-once
}

case "${1:-}" in
    up)
        shift
        command_up "$@"
        ;;
    down)
        shift
        command_down "$@"
        ;;
    ps)
        shift
        command_ps "$@"
        ;;
    logs)
        shift
        command_logs "$@"
        ;;
    run)
        shift
        command_run "$@"
        ;;
    *)
        fail "Usage: scripts/local-process.sh {up [--bind]|down|ps|logs [process]|run <process>}"
        ;;
esac
