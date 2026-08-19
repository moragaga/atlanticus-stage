ARG PYTHON_IMAGE=python:3.14.2-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.10.0

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
ARG FILENAME
ENV UV_NO_CACHE=1 \
    UV_NO_DEV=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY --from=uv /uv /uvx /bin/
RUN case "${FILENAME}" in ''|*[!a-z0-9-]*|-*|*-) echo "Invalid FILENAME build argument: ${FILENAME}" >&2; exit 1;; esac
WORKDIR /app/process
COPY processes/${FILENAME}/ ./
RUN test -f pyproject.toml || (echo "Process pyproject.toml not found" >&2; exit 1); \
    test -f uv.lock || (echo "Process uv.lock not found" >&2; exit 1); \
    test -d wheels || (echo "Process wheels directory not found" >&2; exit 1)
RUN python -c 'import re,tomllib; from pathlib import Path; d=tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8")); project=d.get("project", {}); tool=d.get("tool", {}); uv=tool.get("uv", {}) if isinstance(tool, dict) else {}; sources=uv.get("sources", {}) if isinstance(uv, dict) else {}; atlanticus=tool.get("atlanticus", {}) if isinstance(tool, dict) else {}; container=atlanticus.get("container", {}) if isinstance(atlanticus, dict) else {}; command=container.get("command"); profile=container.get("system-profile"); scripts=project.get("scripts", {}) if isinstance(project, dict) else {}; required=project.get("requires-python") if isinstance(project, dict) else None; assert required == "==3.14.2", "Process must require Python 3.14.2"; assert uv.get("default-groups") == [], "Process is not an Atlanticus transport artifact"; assert isinstance(sources, dict) and all(isinstance(v, dict) and isinstance(v.get("path"), str) and v["path"].startswith("wheels/") for v in sources.values()), "Process contains non-transport dependency sources"; assert isinstance(command, str) and re.fullmatch(r"[A-Za-z0-9._-]+", command), "Invalid container command"; assert command in scripts, "Container command is not declared in project scripts"; assert profile in {"base", "sqlserver"}, "Unsupported container system profile"; Path("/tmp/atlanticus-command").write_text(command, encoding="utf-8"); Path("/tmp/atlanticus-system-profile").write_text(profile, encoding="utf-8")'
RUN uv sync --frozen --no-dev --no-cache --python 3.14.2
RUN command="$(cat /tmp/atlanticus-command)" && test -x "/app/process/.venv/bin/${command}"

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/app/process/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=builder /app/process /app/process
COPY --from=builder /tmp/atlanticus-command /etc/atlanticus/command
COPY --from=builder /tmp/atlanticus-system-profile /etc/atlanticus/system-profile
RUN set -eu; \
    profile="$(cat /etc/atlanticus/system-profile)"; \
    case "$profile" in \
        base) \
            ;; \
        sqlserver) \
            apt-get update; \
            apt-get install -y --no-install-recommends ca-certificates curl; \
            debian_major="$(. /etc/os-release; printf '%s' "${VERSION_ID%%.*}")"; \
            curl -sSL -o /tmp/packages-microsoft-prod.deb "https://packages.microsoft.com/config/debian/${debian_major}/packages-microsoft-prod.deb"; \
            dpkg -i /tmp/packages-microsoft-prod.deb; \
            rm -f /tmp/packages-microsoft-prod.deb; \
            apt-get update; \
            ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18; \
            rm -rf /var/lib/apt/lists/*; \
            ;; \
        *) \
            echo "Unsupported container system profile: $profile" >&2; \
            exit 1; \
            ;; \
    esac
RUN command="$(cat /etc/atlanticus/command)"; \
    printf '#!/bin/sh\nset -eu\nexec "/app/process/.venv/bin/%s" "$@"\n' "$command" > /usr/local/bin/atlanticus-entrypoint; \
    chmod +x /usr/local/bin/atlanticus-entrypoint
WORKDIR /app/process
ENTRYPOINT ["/usr/local/bin/atlanticus-entrypoint"]
