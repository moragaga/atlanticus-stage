ARG PYTHON_IMAGE=python:3.14.2-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.10.0
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE}
ENV UV_NO_CACHE=1 UV_NO_DEV=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=uv /uv /uvx /bin/
WORKDIR /workspace
COPY . .
WORKDIR /workspace/scopes/ada/processes/alarms-runtime
RUN uv sync --frozen --no-dev --no-cache --python 3.14.2
RUN uv pip install --python .venv/bin/python --no-deps --editable ../../../../backend/datasets-runtime --editable ../../../../backend/datasets-parquet
ENV PATH="/workspace/scopes/ada/processes/alarms-runtime/.venv/bin:${PATH}"
ENTRYPOINT ["python", "-m", "performance.docker_stress"]
