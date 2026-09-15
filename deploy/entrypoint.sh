#!/bin/sh
set -e
echo "ARVO entrypoint — waiting for DB..."
# Don't block if DB not ready (Supabase booting) — just log
python -c "import asyncio; from app.database.engine import check_db; print('DB check:', asyncio.run(check_db()))" || echo "DB not ready yet — continuing"
echo "Running migrations (if any)..."
alembic upgrade head 2>&1 || echo "No migrations or alembic not configured — skipping"
exec "$@"
