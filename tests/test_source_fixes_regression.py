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
