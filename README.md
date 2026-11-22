# ARVO — AI Revenue Value Orchestrator

Find the leak. Protect the value. Control the revenue.

**Stack:** FastAPI + Jinja2 + Tailwind + SQLAlchemy/Supabase Postgres (DB principal em produção) — sem Next.js/Node (spec §144-151). AI interpreta, Python calcula.

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
ARVO_DATABASE_URL='postgresql+asyncpg://user:pass@host:5432/arvo' ARVO_REDIS_PASSWORD=$(openssl rand -hex 16) docker compose up --build -d
# redis (dados em volume nomeado redis-data), migrate (one-shot), web:9777, worker.
# Postgres não é subido pelo compose: ARVO_DATABASE_URL é obrigatório (externo/Supabase).
# A aplicação não guarda estado local: escala horizontalmente.
# Migrations: serviço `migrate` roda `alembic upgrade head` uma vez antes do
# web/worker subirem (ARVO_SKIP_MIGRATIONS=1 neles).
```

Sem Docker (dev local) o padrão é SQLite (`sqlite+aiosqlite:///./arvo.db`); defina `ARVO_DATABASE_URL` para usar Postgres. Em produção aponte `ARVO_DATABASE_URL` para Supabase Postgres; SQLite é default de dev/local, não DB de produção (compose não sobe Postgres).

## Coolify deploy

- Project: `ARVO` (`n9mblbh5y56x7rthezbqhbsd`) — env `production`
- Service: `arvo-supabase` (`r4afp9igrytbuir563nsbqem`) — Studio exposto 9771:3000
- App `l7p2wj6ysqcnnymtynr08up1`: build nixpacks, `ports_exposes 9777:9777`, env vars do `.env.example`
- DSN prod usa host `arvo-db-bridge:5432` (socat → `supabase-db-r4afp9igrytbuir563nsbqem:5432`); Redis prod `arvo-redis-1:6379` na rede `coolify`.
- Sem worker arq no Coolify: flush da outbox depende de trigger externo (tarefa agendada `arvo-outbox-flush` ou `POST /api/v1/integrations/aios/v1/outbox/flush`); agendamento no Coolify é passo manual, não versionado neste repo. `ARVO_USE_ARQ_WORKER=true` reativa enqueue imediato quando worker existir.
- `ARVO_AIOS_ALLOWED_ORGS` (opcional, CSV de ids/slugs) restringe orgs AIOS com escrita/SSO permitida; vazio = sem restrição.

## AIOS integration

Integração service-to-service, desligada por padrão (`ARVO_AIOS_INTEGRATION_ENABLED=false`; rotas fechadas retornam 404). Variáveis obrigatórias no ambiente (via Coolify/manual — nunca versionar segredos reais):

- `ARVO_AIOS_INTEGRATION_ENABLED` — `true` liga as rotas; `false` mantém comportamento anterior.
- `ARVO_AIOS_BASE_URL` — base URL da AIOS (ex.: `http://aios:8000`); sem ela o cliente falha.
- `ARVO_AIOS_SERVICE_KEY_ID` — id da chave de serviço.
- `ARVO_AIOS_SERVICE_KEY` — segredo compartilhado; gere com `openssl rand -hex 32`.
- `ARVO_AIOS_ALLOWED_ORGS` — CSV opcional de ids/slugs de orgs AIOS com escrita/SSO permitidos; vazio = sem restrição.
- `ARVO_REDIS_URL` — Redis para enqueue/worker; sem Redis, `/agent-executions` responde 503.

Endpoints assinados sob `/api/v1/integrations/aios/v1/*`: `GET` e `POST /health`, `POST /agent-executions`, `GET /executions/{id}`, `POST /events`, `POST /outbox/flush`, `POST /sso-ticket`. Auth de serviço é HMAC-SHA256 com `X-Service-Key-Id`, `X-Service-Timestamp`, `X-Service-Nonce` e `X-Service-Signature` sobre método + path + hash SHA-256 do corpo; nonce é deduplicado em cache e na tabela `integration_nonces`. Tipos de evento não suportados em `POST /events` são rejeitados com HTTP 400.

Migrations Alembic rodam no entrypoint da imagem (ou no serviço `migrate` do compose); injeção de env/keys e agendamento de flush externo no Coolify são passos manuais. O código fornece apenas endpoints, outbox e cron do worker.

## Supabase Studio

`http://178.105.181.38:9771` — SQL Editor + Table Editor. DB interno do serviço `arvo-supabase`.

## Estrutura

`app/main.py`, `app/core/config.py`, `app/web/templates/base.html`, `app/database/engine.py`, `Dockerfile` (9777), `pyproject.toml`

Principio verdade: limites só de `app/core/config.py:PLANS` + `app/core/limits.py` — home/pricing não prometem o que código não entrega.

## Limites conhecidos (implementação atual)

- Listagens de API aceitam `limit` (1–200, padrão 50) e `offset`, com ordem estável (`created_at`, `id`). A página `/evidence` aceita os mesmos parâmetros.
- Rate limit de login/cadastro usa Redis (`INCR` + `EXPIRE`, chave por IP+rota) quando acessível; se o Redis cair, cai para contador em memória por processo.
- Adaptadores CRM/e-mail respondem 501 (não implementados); entrada real hoje é CSV e upload.
- Execução de action registra a transição de estado; sem integração ligada, nenhuma chamada externa.
- Integração AIOS ↔ ARVO é flag-gated: `ARVO_AIOS_INTEGRATION_ENABLED=false` por padrão; endpoints `/api/v1/integrations/aios/v1/*` exigem HMAC + nonce e o outbox só publica com a flag ligada.
- Control Center lê dados reais da org: `exposure` = soma de `finding.exposure_amount`; `verified` = soma de `financial_ledger` com `entry_type='VERIFIED'` (recuperação comprovada). Sem org, zeros — sem demo ou estimativa.
