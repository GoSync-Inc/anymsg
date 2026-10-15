FROM python:3.13-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.9.26
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY family_calendar ./family_calendar
RUN uv sync --locked --no-dev --no-editable
FROM python:3.13-slim-bookworm
RUN useradd --uid 10001 --no-create-home calendar && mkdir -p /data/service && chown -R 10001:10001 /data && chmod 700 /data /data/service
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" FCS_DATA_DIR=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["family-calendar", "--home", "/data", "service"]
CMD ["serve"]
