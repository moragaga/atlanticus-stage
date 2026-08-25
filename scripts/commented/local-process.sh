#!/usr/bin/env bash
set -euo pipefail

# La interfaz pública es mínima; toda la mecánica de artifacts, Compose y workspace queda encapsulada aquí.
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

# prepare solo necesita UV; Docker se exige únicamente cuando realmente vamos a construir o ejecutar imágenes.
validate_uv() {
    require_command uv
}

validate_docker() {
    require_command docker
    docker compose version >/dev/null 2>&1 || fail "Docker Compose is not available"
}

# up valida artifacts y sus .env locales sin volver a scopes, preservando cualquier catálogo personalizado.
validate_artifacts() {
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        "${GENERATOR}" validate \
        --repository-root "${ROOT}"
}

# El workspace Docker se genera desde artifacts y omite el .env al copiar el proceso al contexto de build.
generate_workspace() {
    local volume_mode="$1"

    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        "${GENERATOR}" generate \
        --repository-root "${ROOT}" \
        --workspace-root "${WORKSPACE}" \
        --volume-mode "${volume_mode}"
}

# prepare conserva el modo histórico sin argumentos y además expone la selección que ya soporta process_bundle.py.
# --all se consume con shift y deja cero argumentos posicionales; esto evita arrays vacíos incompatibles con Bash 3.2 + set -u.
command_prepare() {
    if [[ "${1:-}" == "--all" ]]; then
        [[ "$#" -eq 1 ]] || fail "Usage: scripts/local-process.sh prepare [--all|PROCESS [PROCESS ...]]"
        shift
    fi

    validate_uv
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        "${BUNDLER}" \
        "$@" \
        --repository-root "${ROOT}" \
        --output-root "${ROOT}/artifacts/processes"
    printf '%s\n' "Process artifacts prepared in: ${ROOT}/artifacts/processes"
    printf '%s\n' "Configure each artifact .env and any local catalog changes before running: scripts/local-process.sh up"
}

# up no bundlea: consume los artifacts configurados, reconstruye sin cache y deja todos los E2E en background.
command_up() {
    local volume_mode="named"
    if [[ "${1:-}" == "--bind" ]]; then
        volume_mode="bind"
        shift
    fi
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh up [--bind]"
    validate_uv
    validate_docker
    validate_artifacts
    if [[ -f "${COMPOSE_FILE}" ]]; then
        compose down --remove-orphans
    fi
    generate_workspace "${volume_mode}"
    compose build --no-cache
    compose up -d
    compose ps -a
}

command_down() {
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh down"
    validate_docker
    require_compose_file
    compose down --remove-orphans
}

# Como los servicios usan --run-once, ps incluye contenedores finalizados para que Exited (0) sea visible como éxito.
command_ps() {
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh ps"
    validate_docker
    require_compose_file
    compose ps -a
}

command_logs() {
    [[ "$#" -le 1 ]] || fail "Usage: scripts/local-process.sh logs [process]"
    validate_docker
    require_compose_file
    if [[ "$#" -eq 1 ]]; then
        compose logs -f "$1"
    else
        compose logs -f
    fi
}

# run permite repetir una sola prueba puntual usando el mismo servicio y configuración ya generados.
command_run() {
    [[ "$#" -eq 1 ]] || fail "Usage: scripts/local-process.sh run <process>"
    validate_docker
    require_compose_file
    local process="$1"
    compose config --services | grep -Fx -- "${process}" >/dev/null \
        || fail "Local Compose service not found: ${process}"
    compose run --rm "${process}" --run-once
}

case "${1:-}" in
    prepare)
        shift
        command_prepare "$@"
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
        fail "Usage: scripts/local-process.sh {prepare [--all|PROCESS [PROCESS ...]]|up [--bind]|down|ps|logs [process]|run <process>}"
        ;;
esac
