# syntax=docker/dockerfile:1.7
# quanta runtime image (recorder today; trader/guardian later use the same image).
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app
# Dependencies first (cached layer), then the project itself. --frozen: the lock file is the
# single source of truth for versions (supply-chain hygiene, ADR-007).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra s3 --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra s3

RUN useradd --system --uid 10001 --home-dir /var/lib/quanta quanta \
    && mkdir -p /var/lib/quanta/data /etc/quanta \
    && chown -R quanta:quanta /var/lib/quanta
USER quanta
ENV PATH="/app/.venv/bin:${PATH}"

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:9101/metrics', timeout=3)" || exit 1

ENTRYPOINT ["quanta"]
CMD ["recorder", "run", "--config", "/etc/quanta/recorder.yaml"]
