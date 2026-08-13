FROM python:3.14.2-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=120

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgssapi-krb5-2 \
        libkrb5-3 \
        libltdl7 \
    && rm -rf /var/lib/apt/lists/*

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
COPY connectivity/sql/pyproject.toml connectivity/sql/README.md /workspace/connectivity/sql/
COPY connectivity/storage/pyproject.toml /workspace/connectivity/storage/pyproject.toml
COPY connectivity/sql/src /workspace/connectivity/sql/src
COPY connectivity/sql/tests /workspace/connectivity/sql/tests

WORKDIR /workspace/connectivity

RUN uv sync \
        --only-group dev \
        --frozen \
    && uv sync \
        --package atlanticus-sql \
        --no-default-groups \
        --inexact \
        --frozen \
        --no-editable

ENTRYPOINT [".venv/bin/python", "-m", "pytest", "-q", "-m", "integration", "sql/tests/integration"]
