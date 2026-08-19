#!/usr/bin/env bash
set -euo pipefail

# Todo el detalle de bundling, workspace y Compose queda oculto detrás de una interfaz mínima para desarrollo.
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

# El .env se obtiene siempre desde scopes/ada/processes/<proceso>/.env y nunca se copia al artifact ni a la imagen.
validate_environment() {
    uv run --python "${PYTHON_VERSION}" --no-python-downloads --no-project \
        "${GENERATOR}" validate \
        --repository-root "${ROOT}"
}

# Bundling genera artifacts reales y el generador los copia al workspace descartable usado como contexto Docker.
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
    # Named volume es el default aislado de las ejecuciones source; --bind deja runtime visible en el filesystem.
    if [[ "${1:-}" == "--bind" ]]; then
        volume_mode="bind"
        shift
    fi
    [[ "$#" -eq 0 ]] || fail "Usage: scripts/local-process.sh up [--bind]"
    validate_tools
    validate_environment
    # Se baja el ambiente anterior sin -v para conservar el named volume y su state.
    if [[ -f "${COMPOSE_FILE}" ]]; then
        compose down --remove-orphans
    fi
    prepare_environment "${volume_mode}"
    # El deployment local prioriza reproducibilidad sobre velocidad de cache.
    compose build --no-cache
    # -d devuelve la consola inmediatamente después de levantar el ambiente integrado.
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

# run reutiliza exactamente el servicio Compose ya preparado, pero fuerza una sola iteración para diagnóstico.
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
