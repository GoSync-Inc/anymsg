FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.12.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libsndfile1 libopus0 ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-cache && groupadd -g 10001 voiceworker && useradd -u 10001 -g voiceworker -M voiceworker
COPY *.py ./
COPY frontend/dist ./frontend/dist
ENV PATH=/app/.venv/bin:$PATH VOICE_BIND=0.0.0.0 VOICE_PORT=13842 VOICE_DATA_DIR=/data VOICE_EXTERNAL_MCP=1 VOICE_PERSIST_CREDENTIALS=1
USER 10001:10001
CMD ["python", "app.py"]
