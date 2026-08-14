FROM python:3.14.2-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv

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
COPY connectivity/service-bus/src /workspace/connectivity/service-bus/src
COPY connectivity/service-bus/tests /workspace/connectivity/service-bus/tests

WORKDIR /workspace/connectivity

RUN uv sync \
        --only-group dev \
        --frozen \
    && uv sync \
        --package atlanticus-service-bus \
        --no-default-groups \
        --inexact \
        --frozen \
        --no-editable

CMD ["uv", "run", "--no-sync", "pytest", "service-bus/tests/integration/local"]
