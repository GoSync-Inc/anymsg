FROM python:3.13-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.9.26
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
FROM python:3.13-slim-bookworm
RUN useradd --uid 10001 --no-create-home gateway
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["deedkit-gateway"]
