FROM python:3.14.2-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=120

WORKDIR /workspace

COPY backend/kernel/pyproject.toml backend/kernel/README.md /workspace/backend/kernel/
COPY backend/kernel/src /workspace/backend/kernel/src
COPY backend/observability/pyproject.toml backend/observability/README.md /workspace/backend/observability/
COPY backend/observability/src /workspace/backend/observability/src

COPY connectivity/pyproject.toml connectivity/uv.lock /workspace/connectivity/
COPY connectivity/http-client/pyproject.toml /workspace/connectivity/http-client/pyproject.toml
COPY connectivity/key-vault/pyproject.toml /workspace/connectivity/key-vault/pyproject.toml
COPY connectivity/cosmos/pyproject.toml /workspace/connectivity/cosmos/pyproject.toml
COPY connectivity/service-bus/pyproject.toml /workspace/connectivity/service-bus/pyproject.toml
COPY connectivity/sql/pyproject.toml /workspace/connectivity/sql/pyproject.toml
COPY connectivity/storage/pyproject.toml /workspace/connectivity/storage/pyproject.toml
COPY connectivity/redis/pyproject.toml /workspace/connectivity/redis/pyproject.toml

COPY connectivity/key-vault/src /workspace/connectivity/key-vault/src
COPY connectivity/key-vault/tests /workspace/connectivity/key-vault/tests
COPY connectivity/storage/src /workspace/connectivity/storage/src
COPY connectivity/storage/tests /workspace/connectivity/storage/tests
COPY connectivity/cosmos/src /workspace/connectivity/cosmos/src
COPY connectivity/cosmos/tests /workspace/connectivity/cosmos/tests
COPY connectivity/redis/src /workspace/connectivity/redis/src
COPY connectivity/redis/tests /workspace/connectivity/redis/tests
COPY connectivity/docker/azure-local/provisioning /workspace/connectivity/docker/azure-local/provisioning
COPY connectivity/docker/azure-local/run-connectivity.sh /workspace/connectivity/docker/azure-local/run-connectivity.sh

WORKDIR /workspace/connectivity

RUN uv sync \
        --only-group dev \
        --frozen \
    && uv sync \
        --package atlanticus-key-vault \
        --no-default-groups \
        --inexact \
        --frozen \
        --no-editable \
    && uv sync \
        --package atlanticus-storage \
        --no-default-groups \
        --inexact \
        --frozen \
        --no-editable \
    && uv sync \
        --package atlanticus-cosmos \
        --no-default-groups \
        --inexact \
        --frozen \
        --no-editable \
    && uv sync \
        --package atlanticus-redis \
        --no-default-groups \
        --inexact \
        --frozen \
        --no-editable \
    && .venv/bin/python -m compileall -q \
        docker/azure-local/provisioning \
        key-vault/tests/integration/azure_local \
        storage/tests/integration/azure_local \
        cosmos/tests/integration/azure_local \
        redis/tests/integration/azure_local

ENTRYPOINT ["/bin/bash", "docker/azure-local/run-connectivity.sh"]
