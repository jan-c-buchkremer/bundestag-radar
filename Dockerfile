# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# dependencies first (CPU-only torch via the pytorch-cpu index in pyproject.toml)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm
# uid 1000 matches the host user, so bind-mounted directories stay writable on both sides
RUN useradd --create-home --uid 1000 radar
COPY --from=build /app/.venv /app/.venv
# /work/data holds out/ and the embedding cache (the CLI defaults are relative to the working directory);
# the model lands in /cache; the foundation store is mounted at /foundation
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/huggingface \
    BDF_DB=/foundation/bundestag.sqlite
RUN mkdir -p /work/data /cache && chown radar:radar /work /work/data /cache
USER radar
WORKDIR /work
VOLUME ["/work/data", "/cache"]
ENTRYPOINT ["radar"]
CMD ["--help"]
