# Congelamos Python y UV en las mismas versiones contractuales del repositorio.
ARG PYTHON_IMAGE=python:3.14.2-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.10.0
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE}
# El contenedor no descarga otra versión de Python y deja la salida sin buffering para conservar evidencia en vivo.
ENV UV_NO_CACHE=1 UV_NO_DEV=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=uv /uv /uvx /bin/
# Copiamos el monorepo porque alarms-runtime consume dependencias locales nombradas por path.
WORKDIR /workspace
COPY . .
WORKDIR /workspace/scopes/ada/processes/alarms-runtime
# UV resuelve el lock autoritativo; no usamos pip.
RUN uv sync --frozen --no-dev --no-cache --python 3.14.2
# El harness físico agrega sólo los dos adaptadores backend que necesita el reader; siguen fuera del lock productivo.
RUN uv pip install --python .venv/bin/python --no-deps --editable ../../../../backend/datasets-runtime --editable ../../../../backend/datasets-parquet
ENV PATH="/workspace/scopes/ada/processes/alarms-runtime/.venv/bin:${PATH}"
# El mismo módulo sirve como orquestador host y como preflight/runner dentro del contenedor.
ENTRYPOINT ["python", "-m", "performance.docker_stress"]
