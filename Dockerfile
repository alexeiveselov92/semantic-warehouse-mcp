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
# Every engine's driver: the image serves any of them.
RUN uv sync --frozen --no-dev --no-install-project --extra all
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra all

FROM python:3.12-slim
# git fetches context and skills kept in a repository (docs/reference/configuration.md, "Repositories"). Fetching and
# checking out need none of its Perl scripts: Perl and the manual pages are taken out again (70 MB of 120).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git \
    && dpkg --purge --force-depends $(dpkg-query -W -f '${Package}\n' perl 'perl-modules-*' 'libperl5*' liberror-perl git-man) \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["semantic-warehouse-mcp"]
CMD ["--config", "/config/config.yaml", "--http", "--host", "0.0.0.0", "--port", "8000", "--token-env", "SWM_TOKEN"]
