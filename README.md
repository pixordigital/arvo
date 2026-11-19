# ARVO — AI Revenue Value Orchestrator

Find the leak. Protect the value. Control the revenue.

**Stack:** FastAPI + Jinja2 + Tailwind + SQLAlchemy/SQLite local ou Postgres (Supabase) — sem Next.js/Node (spec §144-151). AI interpreta, Python calcula.

**Ports:** `9777` ARVO web, `9771` Supabase Studio (ARVO project)

## Quickstart (local)

```bash
cp .env.example .env  # fill ARVO_DATABASE_URL, ARVO_JWT_SECRET, ARVO_OPENROUTER_API_KEY
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 9777 --reload
# http://localhost:9777 — Control Center
# http://localhost:9777/api/docs — OpenAPI
```

## Docker

```bash
docker compose up --build
# web:9777, worker, redis; SQLite em volume nomeado (arvo-data)
# Postgres: sobrescreva ARVO_DATABASE_URL no compose e rode as migrations no entrypoint
```

## Coolify deploy

- Project: `ARVO` (`n9mblbh5y56x7rthezbqhbsd`) — env `production`
- Service: `arvo-supabase` (`r4afp9igrytbuir563nsbqem`) — Studio exposto 9771:3000
- App: criar `Application` dockercompose apontando repo, `ports_exposes 9777:9777`, env vars do `.env.example`

## Supabase Studio

`http://178.105.181.38:9771` — SQL Editor + Table Editor. DB `supabase-db:5432` interno.

## Estrutura

`app/main.py`, `app/core/config.py`, `app/web/templates/base.html`, `app/database/engine.py`, `Dockerfile` (9777), `pyproject.toml`

Principio verdade: limites só de `app/core/config.py:PLANS` + `app/core/limits.py` — home/pricing não prometem o que código não entrega.

## Limites conhecidos (implementação atual)

- Listagens de API retornam os registros mais recentes (limite de 50; export LGPD completo). Paginação ainda não implementada.
- Rate limit de login/cadastro é em memória por processo: com múltiplos workers (gunicorn `--workers 2`) o limite efetivo é por worker. Para limite global, usar Redis.
- Adaptadores CRM/e-mail respondem 501 (não implementados); entrada real hoje é CSV e upload.
- Execução de action registra a transição de estado; nenhuma integração externa é chamada.
