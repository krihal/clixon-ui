# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# dependencies first: this layer is cached until the lock file changes
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim-bookworm
RUN useradd --system --create-home --uid 10001 clixon
COPY --from=build --chown=clixon /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CLIXON_UI_HOST=0.0.0.0 \
    CLIXON_UI_PORT=8080
USER clixon
# NiceGUI keeps per-browser session data in ./.nicegui; the YANG cache lives in ~/.cache/clixon-ui
WORKDIR /home/clixon
VOLUME ["/home/clixon/.nicegui", "/home/clixon/.cache/clixon-ui"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:'+os.environ['CLIXON_UI_PORT']+'/', timeout=4)"
# The controller URL comes from CLIXON_URL (or pass it as an argument). Set CLIXON_INSECURE=1 for a self-signed certificate.
ENTRYPOINT ["clixon-ui"]
