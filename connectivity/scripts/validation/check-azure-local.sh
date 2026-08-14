#!/usr/bin/env bash
set -eo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
cd "$ROOT"

SUPPORTED="key-vault storage cosmos redis"
SELECTED=""
CLEAN=0

usage() {
  cat >&2 <<USAGE
Usage: $0 [module ...] [--clean]

Modules with Azure-local integration:
  key-vault
  storage
  cosmos
  redis

No modules runs every Azure-local integration currently registered.
--clean also removes the Azure-local runner image before and after the gate.
USAGE
}

contains_module() {
  case " $1 " in
    *" $2 "*) return 0 ;;
    *) return 1 ;;
  esac
}

for arg in "$@"; do
  case "$arg" in
    --clean)
      CLEAN=1
      ;;
    key-vault|storage|cosmos|redis)
      if ! contains_module "$SELECTED" "$arg"; then
        SELECTED="$SELECTED $arg"
      fi
      ;;
    *)
      echo "Unknown Azure-local validation module: $arg" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${SELECTED# }" ]]; then
  SELECTED="$SUPPORTED"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for Azure-local integration tests." >&2
  exit 1
fi

TARGET="$(printf '%s' "$SELECTED" | xargs | tr ' ' ',')"
if [[ "$SELECTED" == "$SUPPORTED" ]]; then
  TARGET="all"
fi

COMPOSE_FILE="docker/azure-local/compose.yaml"
RUNNER_IMAGE="atlanticus-connectivity-azure-local-integration:local"

cleanup() {
  local code=$?
  docker compose -f "$COMPOSE_FILE" down -v --remove-orphans >/dev/null 2>&1 || true
  if [[ "$CLEAN" -eq 1 ]]; then
    docker image rm "$RUNNER_IMAGE" >/dev/null 2>&1 || true
  fi
  exit "$code"
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" down -v --remove-orphans >/dev/null 2>&1 || true
if [[ "$CLEAN" -eq 1 ]]; then
  docker image rm "$RUNNER_IMAGE" >/dev/null 2>&1 || true
fi

printf '> ATLANTICUS_AZURE_LOCAL_TARGET=%q docker compose -f %q up --build --abort-on-container-exit --exit-code-from connectivity-integration\n' "$TARGET" "$COMPOSE_FILE"
set +e
ATLANTICUS_AZURE_LOCAL_TARGET="$TARGET" docker compose -f "$COMPOSE_FILE" up \
  --build \
  --abort-on-container-exit \
  --exit-code-from connectivity-integration
code=$?
set -e
if [[ "$code" -ne 0 ]]; then
  docker compose -f "$COMPOSE_FILE" logs floci-az connectivity-integration || true
  exit "$code"
fi

echo "Azure-local connectivity validation passed: $(printf '%s' "$SELECTED" | xargs)."
