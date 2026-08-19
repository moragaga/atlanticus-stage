#!/usr/bin/env bash
set -euo pipefail

# Este script viaja dentro de cada distribución y no depende del source de Atlanticus.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_ROOT="${ROOT}/local-deployment"
COMPOSE_FILE="${LOCAL_ROOT}/compose.yaml"
BIND_COMPOSE_FILE="${LOCAL_ROOT}/compose.bind.yaml"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

validate_docker() {
    require_command docker
    docker compose version >/dev/null 2>&1 || fail "Docker Compose is not available"
}

require_compose_files() {
    [[ -f "${COMPOSE_FILE}" ]] || fail "Local Compose file not found: ${COMPOSE_FILE}"
    [[ -f "${BIND_COMPOSE_FILE}" ]] || fail "Local bind Compose file not found: ${BIND_COMPOSE_FILE}"
}

compose_file_for_mode() {
    if [[ "$1" == "bind" ]]; then
        printf '%s\n' "${BIND_COMPOSE_FILE}"
    else
        printf '%s\n' "${COMPOSE_FILE}"
    fi
}

# Los .env son configuración local del consumidor y deben existir antes de tocar Docker.
validate_environment_files() {
    local missing=0
    local process_dir
    while IFS= read -r process_dir; do
        if [[ ! -f "${process_dir}/.env" ]]; then
            printf 'Local process .env file not found: %s\n' "${process_dir}/.env" >&2
            missing=1
        fi
    done < <(find "${ROOT}/processes" -mindepth 1 -maxdepth 1 -type d -print | sort)
    [[ "${missing}" -eq 0 ]] || fail "Configure each process .env before running local E2E."
}

compose() {
    local mode="$1"
    shift
    docker compose -f "$(compose_file_for_mode "${mode}")" "$@"
}

command_validate() {
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh validate"
    require_compose_files
    validate_environment_files
}

# Cada servicio se construye sin cache y se ejecuta con --run-once según el Compose generado.
command_up() {
    local mode="named"
    if [[ "${1:-}" == "--bind" ]]; then
        mode="bind"
        shift
    fi
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh up [--bind]"
    require_compose_files
    validate_environment_files
    validate_docker
    compose "${mode}" config >/dev/null
    if [[ "${mode}" == "bind" ]]; then
        mkdir -p "${LOCAL_ROOT}/runtime"
    fi
    compose "${mode}" down --remove-orphans
    compose "${mode}" build --no-cache
    compose "${mode}" up -d
    compose "${mode}" ps -a
}

command_down() {
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh down"
    require_compose_files
    validate_docker
    compose named down --remove-orphans
}

command_ps() {
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh ps"
    require_compose_files
    validate_docker
    compose named ps -a
}

command_logs() {
    [[ "$#" -le 1 ]] || fail "Usage: scripts/local-process.sh logs [process]"
    require_compose_files
    validate_docker
    if [[ "$#" -eq 1 ]]; then
        compose named logs -f "$1"
    else
        compose named logs -f
    fi
}

# Permite repetir un único proceso sin reconstruir toda la distribución.
command_run() {
    [[ "$#" -eq 1 ]] || fail "Usage: scripts/local-process.sh run <process>"
    require_compose_files
    validate_environment_files
    validate_docker
    local process="$1"
    compose named config --services | grep -Fx -- "${process}" >/dev/null \
        || fail "Local Compose service not found: ${process}"
    compose named run --rm "${process}" --run-once
}

case "${1:-}" in
    validate)
        shift
        command_validate "$@"
        ;;
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
        fail "Usage: scripts/local-process.sh {validate|up [--bind]|down|ps|logs [process]|run <process>}"
        ;;
esac
