# The server's image (docs/reference/running.md): the package and its locked dependencies, run as a user without
# rights, on a root filesystem that may be read-only. The configuration and what it names are mounted at /config.
#
#   docker build -t semantic-warehouse-mcp .
#   docker run --read-only --tmpfs /tmp -v "$PWD/examples:/config:ro" -e SWM_TOKEN=… -p 8000:8000 semantic-warehouse-mcp

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["semantic-warehouse-mcp"]
CMD ["--config", "/config/config.yaml", "--http", "--host", "0.0.0.0", "--port", "8000", "--token-env", "SWM_TOKEN"]
