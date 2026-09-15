# ─── Builder ───
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir . && pip install --no-cache-dir gunicorn asyncpg && pip freeze > /installed.txt

# ─── Runtime ───
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
WORKDIR /app
COPY . .
RUN mkdir -p /data && groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser && chown -R appuser:appuser /app /data
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown appuser:appuser /entrypoint.sh
USER appuser
EXPOSE 9777
HEALTHCHECK --interval=15s --timeout=10s --start-period=90s --retries=10 CMD curl -sf http://localhost:9777/health/live || exit 1
STOPSIGNAL SIGTERM
CMD ["gunicorn", "app.main:app", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:9777", "--workers", "2", "--timeout", "120", "--keep-alive", "5", "--access-logfile", "-", "--error-logfile", "-", "--forwarded-allow-ips", "*"]
