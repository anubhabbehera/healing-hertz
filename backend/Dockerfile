# syntax=docker/dockerfile:1

# --- build: resolve dependencies from the committed lock -----------------
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first so they stay cached when only app code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN uv sync --frozen --no-dev

# --- runtime -------------------------------------------------------------
FROM python:3.12-slim

ARG VERSION=0.1.0
ARG REVISION=unknown
ARG BUILD_DATE

LABEL org.opencontainers.image.title="healing-hertz backend" \
      org.opencontainers.image.description="UniFi network diagnostics API: collectors, rule engine, and LLM advisor" \
      org.opencontainers.image.authors="Anubhab Behera" \
      org.opencontainers.image.vendor="Anubhab Behera" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/anubhabbehera/healing-hertz" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}"

# Run unprivileged; /data is the SQLite volume mount point.
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /data && chown appuser:appuser /data

WORKDIR /app
COPY --from=build --chown=appuser:appuser /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/data/healing_hertz.db \
    PYTHONUNBUFFERED=1

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
