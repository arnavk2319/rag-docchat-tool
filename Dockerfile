# syntax=docker/dockerfile:1
# Build: docker build -t docchat:test .   Run: see docs/deploy.md
# Models (embedder + reranker) are NOT baked in: they download on first start into HF_HOME
# (mount a volume at /data so restarts do not re-download). Keeps the image smaller and rebuilds fast.

FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5.31 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
# NOTE: Linux torch from PyPI bundles CUDA wheels (~4 GB) and cannot import without them; a CPU-only
# torch index needs an ADR (see docs/deploy.md).

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app \
 && mkdir -p /data && chown app:app /data
COPY --from=builder --chown=app:app /opt/venv /opt/venv
COPY --chown=app:app migrations /app/migrations
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/data/hf \
    OBJECT_STORE=local \
    OBJECT_STORE_PATH=/data/objects \
    DB_PATH=/data/docchat.db \
    ENABLE_DOCS=false
WORKDIR /app
USER app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
