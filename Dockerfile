FROM python:3.12-slim-bookworm AS python-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

FROM python-base AS builder

WORKDIR /build
COPY requirements.txt ./
RUN python -m pip wheel --no-cache-dir \
    --only-binary=:all: --wheel-dir /wheels -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY edge_tts_mcp/ ./edge_tts_mcp/
RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels . \
    && python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --no-index \
        --find-links=/wheels /wheels/edge_tts_mcp-*.whl \
    && /opt/venv/bin/python -m pip check

FROM python-base AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

# No compiler, database client, web server, or test dependencies in runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /data/output \
    && chown app:app /data/output \
    && chmod 0700 /data/output

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}" \
    EDGE_TTS_OUTPUT_DIR=/data/output \
    EDGE_TTS_FFMPEG=/usr/bin/ffmpeg

WORKDIR /app
USER app:app

# Let asyncio/AnyIO request cancellation before Docker's forced-stop deadline.
STOPSIGNAL SIGINT

# stdio MCP: use docker run -i, never -t. No HTTP port is needed.
ENTRYPOINT ["edge-tts-mcp"]
