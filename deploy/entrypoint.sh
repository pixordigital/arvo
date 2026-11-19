#!/bin/sh
set -e

echo "[entrypoint] waiting for database..."
i=0
until python -c "import asyncio,sys; from app.database.engine import check_db; sys.exit(0 if asyncio.run(check_db()) else 1)"; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "[entrypoint] database unreachable after 30 tries — aborting" >&2
    exit 1
  fi
  echo "[entrypoint] database not ready, retry $i/30..."
  sleep 2
done
echo "[entrypoint] database ready"

if [ "${ARVO_SKIP_MIGRATIONS:-0}" = "1" ]; then
  echo "[entrypoint] ARVO_SKIP_MIGRATIONS=1 — skipping alembic"
else
  echo "[entrypoint] running migrations..."
  i=0
  until alembic upgrade head; do
    i=$((i + 1))
    if [ "$i" -ge 5 ]; then
      echo "[entrypoint] alembic upgrade head FAILED after 5 tries — aborting" >&2
      exit 1
    fi
    echo "[entrypoint] migration failed, retry $i/5 in 5s..."
    sleep 5
  done
  echo "[entrypoint] migrations up to date"
fi

exec "$@"
