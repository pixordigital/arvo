"""Regressions for publisher/health/SSO/worker source fixes. No network."""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest


# ── 1. enqueue_outbox(session=...) must not commit nor schedule a flush ──────


async def test_enqueue_outbox_with_session_no_commit_no_schedule(monkeypatch):
    from app.integrations.aios import publisher

    calls = []

    class FakeSession:
        def add(self, row):
            calls.append(("add", row))

        async def flush(self):
            calls.append(("flush", None))

        async def commit(self):
            calls.append(("commit", None))

    def forbidden_schedule(*args, **kwargs):
        raise AssertionError("schedule_flush must not be called when session is supplied")

    monkeypatch.setattr(publisher, "schedule_flush", forbidden_schedule)

    key = await publisher.enqueue_outbox("test.event", {"n": 1}, session=FakeSession())

    assert [name for name, _ in calls] == ["add", "flush"]
    assert calls[0][1].idempotency_key == key


# ── 2. schedule_flush gated by ARVO_USE_ARQ_WORKER ───────────────────────────


async def test_schedule_flush_noop_when_worker_flag_off(monkeypatch):
    import arq

    from app.integrations.aios import publisher

    def forbidden_pool(*args, **kwargs):
        raise AssertionError("create_pool reached while worker flag off")

    monkeypatch.setattr(arq, "create_pool", forbidden_pool)
    monkeypatch.delenv("ARVO_USE_ARQ_WORKER", raising=False)
    await publisher.schedule_flush("oid-off")
    monkeypatch.setenv("ARVO_USE_ARQ_WORKER", "false")
    await publisher.schedule_flush("oid-off")


async def test_schedule_flush_enqueues_when_worker_flag_on(monkeypatch):
    import arq

    from app.integrations.aios import publisher

    calls = []

    class FakePool:
        async def enqueue_job(self, name, outbox_id):
            calls.append((name, outbox_id))

        async def close(self):
            calls.append(("close", None))

    async def fake_create_pool(settings):
        calls.append(("create_pool", settings))
        return FakePool()

    monkeypatch.setenv("ARVO_USE_ARQ_WORKER", "true")
    monkeypatch.setattr(arq, "create_pool", fake_create_pool)
    monkeypatch.setattr("app.workers.runner._redis_settings", lambda: "REDIS_SENTINEL")

    await publisher.schedule_flush("oid-on")

    assert calls == [
        ("create_pool", "REDIS_SENTINEL"),
        ("integration_outbox_flush_job", "oid-on"),
        ("close", None),
    ]


# ── 3. flush_outbox reports exhausted + marks row failed at 5 attempts ──────


async def test_flush_outbox_reports_exhausted_after_five_attempts(monkeypatch, tmp_path):
    import app.database.engine as db
    from app.core.config import settings
    from app.database.engine import get_sessionmaker, init_db
    from app.database.models import IntegrationOutbox
    from app.integrations.aios import publisher

    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'flush.db'}")
    db._engines.clear()
    db._sessions.clear()
    db._default_engine = None
    db._default_session = None
    await init_db()
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_base_url", "http://peer.test")

    async def fail_send(*args, **kwargs):
        raise RuntimeError("peer unavailable")

    monkeypatch.setattr("app.integrations.aios.client.send_event", fail_send)

    outbox_id = f"flush-{uuid.uuid4().hex}"
    async with get_sessionmaker()() as s:
        s.add(IntegrationOutbox(
            id=outbox_id, idempotency_key=outbox_id,
            event_type="test.event", payload={"n": 1},
        ))
        await s.commit()

    results = [await publisher.flush_outbox(batch=20) for _ in range(5)]

    assert [r["exhausted"] for r in results] == [0, 0, 0, 0, 1]
    assert results[-1] == {"sent": 0, "failed": 1, "exhausted": 1}
    async with get_sessionmaker()() as s:
        row = await s.get(IntegrationOutbox, outbox_id)
        assert row.status == "failed"
        assert row.attempts == 5


# ── 4. /health/ready swallows dependency exception text ────────────────────


async def test_health_ready_does_not_leak_dependency_errors(monkeypatch):
    import app.main as main_module
    from app.core.config import settings

    async def boom_db():
        raise RuntimeError("DB_LEAK_SENTINEL_supabase_studio")

    async def boom_redis():
        raise RuntimeError("REDIS_LEAK_SENTINEL")

    monkeypatch.setattr(settings, "debug", True)  # force check_db path (skip sqlite short-circuit)
    monkeypatch.setattr("app.database.engine.check_db", boom_db)
    monkeypatch.setattr(main_module, "_redis_client", boom_redis)

    resp = await main_module.health_ready()
    body = resp.body.decode()

    assert resp.status_code == 503
    assert "DB_LEAK_SENTINEL" not in body
    assert "REDIS_LEAK_SENTINEL" not in body
    assert "supabase_studio" not in body
    assert json.loads(body)["checks"] == {"db": "down", "redis": "down"}


# ── 5. nonce GC + strict SSO claim checks ───────────────────────────────────


async def test_cleanup_expired_nonces_removes_only_expired(monkeypatch, tmp_path):
    import app.database.engine as db
    from app.core.config import settings
    from app.database.engine import get_sessionmaker, init_db
    from app.database.models import IntegrationNonce
    from app.integrations.aios.routes import cleanup_expired_nonces

    assert callable(cleanup_expired_nonces)

    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'nonce.db'}")
    db._engines.clear()
    db._sessions.clear()
    db._default_engine = None
    db._default_session = None
    await init_db()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expired, fresh = f"expired-{uuid.uuid4().hex}", f"fresh-{uuid.uuid4().hex}"
    async with get_sessionmaker()() as s:
        s.add(IntegrationNonce(nonce=expired, peer="aios", expires_at=now - timedelta(seconds=1)))
        s.add(IntegrationNonce(nonce=fresh, peer="aios", expires_at=now + timedelta(seconds=600)))
        await s.commit()

    assert await cleanup_expired_nonces() == 1
    async with get_sessionmaker()() as s:
        assert await s.get(IntegrationNonce, expired) is None
        assert await s.get(IntegrationNonce, fresh) is not None


@pytest.mark.parametrize("mutate", [
    "no_iss", "bad_iss", "no_aud", "bad_aud", "no_exp", "no_kid", "bad_kid",
])
def test_sso_rejects_strict_claims_and_unknown_key_id(monkeypatch, mutate):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.core.config import settings

    async def no_redis():
        return None

    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key", "sso-secret")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-sso")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1000)
    monkeypatch.setattr(main_module, "_redis_client", no_redis)

    claims = {
        "iss": "aios", "aud": "arvo", "sub": "user-sso", "org_id": "org-sso",
        "jti": f"nonce-{uuid.uuid4().hex}", "key_id": "kid-sso",
        "exp": int(time.time()) + 600,
    }
    if mutate == "no_iss":
        claims.pop("iss")
    elif mutate == "bad_iss":
        claims["iss"] = "not-aios"
    elif mutate == "no_aud":
        claims.pop("aud")
    elif mutate == "bad_aud":
        claims["aud"] = "not-arvo"
    elif mutate == "no_exp":
        claims.pop("exp")
    elif mutate == "no_kid":
        claims.pop("key_id")

    body = {"ticket": jwt.encode(claims, "sso-secret", algorithm="HS256")}
    if mutate == "bad_kid":
        body["key_id"] = "unknown-kid"

    c = TestClient(main_module.app)
    r = c.post("/api/v1/integrations/aios/v1/sso-ticket", json=body)
    assert r.status_code == 401, r.text


# ── 6. reconcile job registration ───────────────────────────────────────────


def test_integration_reconcile_registered_in_worker_functions():
    from app.workers.runner import WorkerSettings, integration_reconcile

    assert integration_reconcile in WorkerSettings.functions


# ── 7. nonce GC cron registration ────────────────────────────────────────────


async def test_cleanup_expired_nonces_cron_registered_and_callable(monkeypatch):
    import app.integrations.aios.routes as routes
    from app.workers.runner import WorkerSettings, cleanup_expired_nonces

    jobs = [j for j in WorkerSettings.cron_jobs if j.coroutine is cleanup_expired_nonces]
    assert len(jobs) == 1
    assert callable(jobs[0].coroutine)
    assert jobs[0].minute == {0, 30}
    assert jobs[0].second == 0

    called = []

    async def fake_gc():
        called.append(True)
        return 0

    monkeypatch.setattr(routes, "cleanup_expired_nonces", fake_gc)
    assert await cleanup_expired_nonces({}) == 0
    assert called == [True]


# ── 8. signed /events handlers set RLS org context before org-row access ────


async def test_signed_event_handler_sets_rls_org_context(monkeypatch):
    import app.database.engine as db
    import app.integrations.aios.routes as routes

    org_id = "org-signed-rls"
    executed = []

    class StopHandler(Exception):
        pass

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, stmt, params=None):
            executed.append((str(stmt), params))
            return None

        async def get(self, *args):
            raise StopHandler()

    monkeypatch.setattr(db, "get_sessionmaker", lambda: FakeSession)

    with pytest.raises(StopHandler):
        await routes._handle_deal_event(
            {"org_id": org_id, "deal_id": "deal-rls", "value": "100.00"},
            "trace-rls",
            "deal.created",
        )

    assert any(
        "set_config('app.current_org_id'" in sql and params == {"org": org_id}
        for sql, params in executed
    ), executed


# ── 9. GET /opportunities/{oid} follows AIOS source-of-truth flag ───────────


async def test_opp_detail_follows_aios_source_of_truth(monkeypatch, tmp_path):
    import app.database.engine as db
    import app.integrations.aios.client as aios_client
    from app.api import opportunities as opps
    from app.core.config import settings
    from app.database.engine import get_sessionmaker, init_db
    from app.database.models import Account, Opportunity
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'opp_detail.db'}")
    db._engines.clear()
    db._sessions.clear()
    db._default_engine = None
    db._default_session = None
    await init_db()

    org_id = "org-opp-detail"
    async with get_sessionmaker()() as s:
        acc = Account(org_id=org_id, name="Local Detail Co")
        s.add(acc)
        await s.flush()
        local = Opportunity(
            org_id=org_id, account_id=acc.id, name="Local Only Deal",
            stage="PROSPECTING", amount="10", currency="BRL",
        )
        s.add(local)
        await s.commit()
        await s.refresh(local)
        local_id, acc_id = local.id, acc.id

    from types import SimpleNamespace

    ctx = {
        "membership": SimpleNamespace(org_id=org_id),
        "user": SimpleNamespace(id="user-opp-detail"),
    }

    calls = []

    async def fake_list_deals(*, org_id, limit=100):
        calls.append((org_id, limit))
        return {
            "org_id": org_id,
            "count": 2,
            "deals": [
                {"id": "aios-deal-9", "lead_name": "AIOS Deal Detail",
                 "stage": "PROPOSAL", "value": "77.70", "currency": "BRL"},
                {"id": None, "lead_name": "Malformed Deal",
                 "stage": "PROSPECTING", "value": "1", "currency": "BRL"},
            ],
        }

    monkeypatch.setattr(aios_client, "list_deals", fake_list_deals)

    # Flag off: local row served, peer untouched.
    monkeypatch.setattr(settings, "aios_integration_enabled", False)
    off = await opps.get_opp(local_id, ctx)
    assert off["name"] == "Local Only Deal"
    assert off["account_id"] == acc_id
    assert calls == []

    # Flag on: peer row in list shape; no account_id; malformed id skipped.
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    on = await opps.get_opp("aios-deal-9", ctx)
    assert on == {
        "id": "aios-deal-9", "name": "AIOS Deal Detail", "stage": "PROPOSAL",
        "amount": "77.70", "currency": "BRL",
    }
    assert "account_id" not in on
    assert calls and all(c[0] == org_id for c in calls)
    assert [r["id"] for r in await opps._shared_deals(org_id, 10)] == ["aios-deal-9"]

    # Local id exists in DB but absent from peer -> 404, no local fallback.
    with pytest.raises(HTTPException) as ei:
        await opps.get_opp(local_id, ctx)
    assert ei.value.status_code == 404


# ── 10. web deals page skips malformed peer ids ─────────────────────────────


async def test_web_deals_skips_malformed_peer_ids(monkeypatch):
    from fastapi.testclient import TestClient

    import app.integrations.aios.client as aios_client
    import app.main as main_module
    import app.web.routes as web_routes
    from app.core.config import settings

    org_id = f"web-deals-{uuid.uuid4().hex}"

    async def fake_require_org(request):
        return {"org_id": org_id, "user_id": "web-deals-user", "role": "OWNER"}

    async def fake_list_deals(*, org_id, limit=100):
        return {
            "org_id": org_id,
            "count": 4,
            "deals": [
                {"id": "aios-web-ok", "lead_name": "Web Deal OK",
                 "stage": "PROPOSAL", "value": "10", "currency": "BRL"},
                {"id": None, "lead_name": "Malformed Null Deal",
                 "stage": "PROPOSAL", "value": "1", "currency": "BRL"},
                {"id": 42, "lead_name": "Malformed Int Deal",
                 "stage": "PROPOSAL", "value": "1", "currency": "BRL"},
                {"id": "", "lead_name": "Malformed Empty Deal",
                 "stage": "PROPOSAL", "value": "1", "currency": "BRL"},
            ],
        }

    monkeypatch.setattr(web_routes, "_require_org", fake_require_org)
    monkeypatch.setattr(aios_client, "list_deals", fake_list_deals)
    monkeypatch.setattr(settings, "aios_integration_enabled", True)

    client = TestClient(main_module.app, raise_server_exceptions=False)
    r = client.get("/deals")

    assert r.status_code == 200, r.text
    assert "Web Deal OK" in r.text
    assert "aios-web" in r.text
    assert "Malformed" not in r.text
