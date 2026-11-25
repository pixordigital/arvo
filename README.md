# ARVO — AI Revenue Value Orchestrator

Find the leak. Protect the value. Control the revenue.

**Stack:** FastAPI + Jinja2 + Tailwind + SQLAlchemy/Supabase Postgres (DB principal em produção) — sem Next.js/Node (spec §144-151). AI interpreta, Python calcula.

**Portas locais (compose/dev):** `9777` ARVO web; `9771` aparece apenas como default de `ARVO_SUPABASE_STUDIO_URL`, não como origem pública do produto. Em produção o Traefik publica tudo em `https://178.105.181.38`.

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

## Deploy / topologia em produção

- Origem único: `https://178.105.181.38` via Traefik. AIOS dashboard em `/dashboard`; ARVO UI sob `/arvo/...` (ex.: `/arvo/control-center`, `/arvo/login`); rotas de integração assinadas sob `/api/v1/integrations/aios/v1/*`. Não há origem, porta ou subdomínio próprio para ARVO em produção.
- App Coolify `l7p2wj6ysqcnnymtynr08up1`: build nixpacks, `ports_exposes 9777:9777` (interno), env vars do `.env.example`. `ARVO_ROOT_PATH=/arvo` prefixa links e redirects da UI.
- DB: Supabase Postgres compartilhado com AIOS; schema `arvo` no DB `aios`, atrás de ponte socat na rede Coolify. Compose deste repo não sobe Postgres.
- Outbox: `enqueue_outbox` registra hook after-commit; com loop de eventos ativo, agenda flush imediato (enfileira no arq só com `ARVO_USE_ARQ_WORKER=true`). Sem worker/loop, entrega via cron `arvo-outbox-flush` ou `POST /api/v1/integrations/aios/v1/outbox/flush` (HMAC). Agendamento no Coolify é manual, não versionado neste repo.
- `ARVO_AIOS_ALLOWED_ORGS` (opcional, CSV de ids/slugs) restringe orgs AIOS com escrita/SSO permitida; vazio = sem restrição.

## AIOS integration

Integração service-to-service. No código, desligada por padrão (`ARVO_AIOS_INTEGRATION_ENABLED=false`; rotas fechadas retornam 404); em produção está ligada — ver status abaixo. Variáveis obrigatórias no ambiente (via Coolify/manual — nunca versionar segredos reais):

- `ARVO_AIOS_INTEGRATION_ENABLED` — `true` liga as rotas; `false` mantém comportamento anterior.
- `ARVO_AIOS_BASE_URL` — base URL da AIOS (ex.: `http://aios:8000`); sem ela o cliente falha.
- `ARVO_AIOS_SERVICE_KEY_ID` — id da chave de serviço.
- `ARVO_AIOS_SERVICE_KEY` — segredo compartilhado; gere com `openssl rand -hex 32`.
- `ARVO_AIOS_ALLOWED_ORGS` — CSV opcional de ids/slugs de orgs AIOS com escrita/SSO permitidos; vazio = sem restrição.
- `ARVO_AIOS_SERVICE_KEYS` — CSV opcional `kid:secret,...` para rotação HMAC; verificação aceita secret mapeado para o `kid` e depois a chave primária; vazio = só a primária.
- `ARVO_REDIS_URL` — Redis para enqueue/worker; sem Redis, `/agent-executions` responde 503.

Endpoints sob `/api/v1/integrations/aios/v1/*`: `GET` e `POST /health`, `POST /agent-executions`, `GET /executions/{id}`, `POST /events`, `POST /outbox/flush`, `POST /sso-ticket`. Rotas service-to-service usam HMAC-SHA256 com `X-Service-Key-Id`, `X-Service-Timestamp`, `X-Service-Nonce` e `X-Service-Signature`; a canonical cobre método + path + query ordenada + `Idempotency-Key` + hash SHA-256 do corpo. `POST /sso-ticket` é exceção: consome ticket JWT assinado pela AIOS (HS256, segredo compartilhado), verificando `key_id`/`kid`, nonce (`jti`) e claims (`iss=aios`, `aud=arvo`, `exp`, `sub`, `org_id`, `role`) — não usa a assinatura canonical HMAC. Nonce é deduplicado em cache e na tabela `integration_nonces`. Tipos de evento não suportados em `POST /events` são rejeitados com HTTP 400. Deploy misto dos peers falha com 401 até ambos usarem a mesma canonical.

SSO: ticket assinado cria novo subject AIOS apenas dentro de org ARVO já provisionada; org desconhecida retorna 403 e membership revogada/inativa retorna 403. Com `AIOS_ARVO_INTEGRATION_ENABLED` e `ARVO_AIOS_INTEGRATION_ENABLED` ligadas (default `false`), `/deals` usa o CRM do AIOS como fonte da verdade; com as flags desligadas, usa apenas o CRUD local do ARVO. ARVO mantém CRUD local de `Opportunity` só para o fluxo nativo ARVO, sem sincronização bidirecional.

Migrations Alembic rodam no entrypoint da imagem (ou no serviço `migrate` do compose); injeção de env/keys e agendamento de flush externo no Coolify são passos manuais.

## AIOS↔ARVO status

- Código novo de integração (canonical HMAC atual + peer) commitado e deployado. Deploys pareados da correção de integração: AIOS `#810` commit `63f6c33d1e8af51f108eeea793664fd1e87451a6` (`finished` 2026-11-24T22:34:55Z) e ARVO `#811` commit `e619d220e50c0c720fb98322433742a150269975` (`finished` 2026-11-24T22:34:07Z). Pares anteriores: `#809`/`#808` e `#807`/`#806` (AIOS/ARVO). Deploys posteriores observados em 2026-11-25: AIOS `#816` `6f0c1b594813fb55266f2b38253ece0fb23ada24`, ARVO `#813` (mesmo commit `e619d22`), ambos `finished`.
- Entrega assinada entre peers VERIFICADA em 2026-11-25 com pares de service-key não-preview (`ids_match=true`, `secrets_match=true`; valores não expostos): `GET https://178.105.181.38/api/integrations/arvo/v1/health` → 200 `{"status":"ok","service":"aios","peer":"arvo"}`; `GET https://178.105.181.38/arvo/api/v1/integrations/aios/v1/health` → 200 `{"status":"ok","service":"arvo","peer":"aios"}`. Paths assinados exatamente como enviados (sem reescrita); TLS com `verify=False` (certificado self-signed). Nada além desses dois probes foi verificado.
- AIOS `/health/ready` 200 `migrations:ok` no deploy `#827`; head `b2c3d4e5f6a7` no banco usado pelo app. Estado anterior (503, `behind:b8c9d0e1f2a3->d0e1f2a3b4c5`) superado.
- Defaults do código: `AIOS_ARVO_INTEGRATION_ENABLED=false`, `ARVO_AIOS_INTEGRATION_ENABLED=false`. Em produção as duas flags estão ligadas e as chaves configuradas; antes do deploy pareado health sem credenciais respondia 401. Flag desligada daria 404 e sem chave 503.
- Mudança da canonical HMAC (query ordenada + `Idempotency-Key` + hash do corpo) deployada nos dois peers; rollout misturado retorna 401.
- Com `AIOS_ARVO_INTEGRATION_ENABLED` e `ARVO_AIOS_INTEGRATION_ENABLED` ligadas (default `false`), `/deals` compartilhados têm o CRM do AIOS como fonte da verdade; com as flags desligadas, ficam no CRUD local do ARVO. ARVO tem só CRUD local de `Opportunity` para fluxo nativo; sem sync bidirecional.

## Supabase Studio

`ARVO_SUPABASE_STUDIO_URL` (default `http://178.105.181.38:9771`) aponta para o Studio do Supabase quando o serviço existe. Não é a origem pública da aplicação; a UI do ARVO fica em `https://178.105.181.38/arvo/...`.

## Estrutura

`app/main.py`, `app/core/config.py`, `app/web/templates/base.html`, `app/database/engine.py`, `Dockerfile` (9777), `pyproject.toml`

Principio verdade: limites só de `app/core/config.py:PLANS` + `app/core/limits.py` — home/pricing não prometem o que código não entrega.

## Limites conhecidos (implementação atual)

- Listagens de API aceitam `limit` (1–200, padrão 50) e `offset`, com ordem estável (`created_at`, `id`). A página `/evidence` aceita os mesmos parâmetros.
- Rate limit de login/cadastro usa Redis (`INCR` + `EXPIRE`, chave por IP+rota) quando acessível; se o Redis cair, cai para contador em memória por processo.
- Adaptadores CRM/e-mail respondem 501 (não implementados); entrada real hoje é CSV e upload.
- Execução de action registra a transição de estado; sem integração ligada, nenhuma chamada externa.
- Integração AIOS ↔ ARVO é flag-gated: `ARVO_AIOS_INTEGRATION_ENABLED=false` é o default do código; endpoints `/api/v1/integrations/aios/v1/*` exigem HMAC + nonce e o outbox só publica com a flag ligada.
- Control Center lê dados reais da org: `exposure` = soma de `finding.exposure_amount`; `verified` = soma de `financial_ledger` com `entry_type='VERIFIED'` (recuperação comprovada). Sem org, zeros — sem demo ou estimativa.
