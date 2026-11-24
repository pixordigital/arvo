"""Fase 1 — AIOS integration config + auth self-check. Fase 2 — HMAC + nonce + routes."""
import sys
import uuid

import pytest
from app.integrations.aios.auth import _clear_nonces, sign_request, verify_request

PATH = "/api/integrations/arvo/v1/health"
ROUTE = "/api/v1/integrations/aios/v1/health"


def test_config_feature_flag_off_default():
    from app.core.config import settings
    assert settings.aios_integration_enabled is False
    assert settings.aios_service_key_id == ""
    assert settings.aios_service_key == ""


def test_sign_verify_roundtrip():
    kid, key = "kid-1", "secret-1"
    hdr = sign_request("GET", PATH, None, kid, key)
    assert verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], key)


def test_verify_rejects_wrong_key():
    kid, key = "kid-1", "secret-1"
    hdr = sign_request("GET", PATH, None, kid, key)
    assert not verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], "wrong")


def test_verify_rejects_stale_timestamp():
    kid, key = "kid-1", "secret-1"
    stale = str(int(sys.maxsize // (10**9) * 0.5))  # epoch antigo
    sig = sign_request("GET", PATH, None, kid, key)["X-Service-Signature"]
    assert not verify_request(kid, stale, "n", "GET", PATH, None, sig, key)


def test_verify_rejects_bad_body_tamper():
    kid, key = "kid-1", "secret-1"
    _clear_nonces()
    hdr = sign_request("POST", PATH, b'{"a":1}', kid, key)
    assert not verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "POST", PATH, b'{"a":2}', hdr["X-Service-Signature"], key)


def test_verify_rejects_replay():
    kid, key = "kid-1", "secret-1"
    _clear_nonces()
    hdr = sign_request("GET", PATH, None, kid, key)
    assert verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], key)
    assert not verify_request(kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], key)
    _clear_nonces()


def test_health_requires_auth_when_enabled():
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.main import app

    c = TestClient(app)
    orig_enabled, orig_id, orig_key = settings.aios_integration_enabled, settings.aios_service_key_id, settings.aios_service_key
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-1"
        settings.aios_service_key = "secret-1"
        _clear_nonces()
        assert c.get(ROUTE).status_code == 401
        hdr = sign_request("GET", ROUTE, None, "kid-1", "secret-1")
        assert c.get(ROUTE, headers=hdr).status_code == 200
        assert c.get(ROUTE, headers=hdr).status_code == 401  # replay
    finally:
        settings.aios_integration_enabled = orig_enabled
        settings.aios_service_key_id = orig_id
        settings.aios_service_key = orig_key
        _clear_nonces()


async def test_event_retry_uses_fresh_hmac_nonce(monkeypatch):
    import httpx
    from app.core.config import settings
    from app.integrations.aios.client import send_event

    nonces = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, path, **kwargs):
            request = httpx.Request(method, f"http://peer.test{path}")
            nonces.append(kwargs["headers"]["X-Service-Nonce"])
            if len(nonces) == 1:
                raise httpx.ConnectError("timeout", request=request)
            return httpx.Response(200, request=request, json={"status": "ok"})

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr("app.integrations.aios.client.asyncio.sleep", no_sleep)
    monkeypatch.setattr(settings, "aios_base_url", "http://peer.test")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid")
    monkeypatch.setattr(settings, "aios_service_key", "secret")
    await send_event("test.event", {}, "idem")
    assert len(nonces) == 2 and nonces[0] != nonces[1]


async def test_ping_retry_signs_fresh_nonce_without_body(monkeypatch):
    """GET retry must re-sign: same nonce would be rejected as replay."""
    import httpx
    from app.core.config import settings
    from app.integrations.aios import client

    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def request(self, method, path, **kwargs):
            request = httpx.Request(method, f"http://peer.test{path}")
            calls.append((path, kwargs))
            if len(calls) == 1:
                raise httpx.ConnectError("timeout", request=request)
            return httpx.Response(200, request=request, json={"status": "ok"})

    async def no_sleep(delay):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(client, "_CLIENT", None)
    monkeypatch.setattr("app.integrations.aios.client.asyncio.sleep", no_sleep)
    monkeypatch.setattr(settings, "aios_base_url", "http://peer.test")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid")
    monkeypatch.setattr(settings, "aios_service_key", "secret")
    assert await client.ping() == {"status": "ok"}
    assert len(calls) == 2
    assert calls[0][1]["headers"]["X-Service-Nonce"] != calls[1][1]["headers"]["X-Service-Nonce"]
    assert all(path == "/api/integrations/arvo/v1/health" for path, _ in calls)
    assert all("content" not in kw for _, kw in calls)


async def test_events_idempotency():
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding, FindingEvidence, IntegrationEvent, Organization
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org = f"org-idem-{uuid.uuid4().hex[:8]}"
    finding_id = f"finding-idem-{uuid.uuid4().hex[:8]}"
    idem_key = None
    c = TestClient(app)
    orig_enabled, orig_id, orig_key = settings.aios_integration_enabled, settings.aios_service_key_id, settings.aios_service_key
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-1"
        settings.aios_service_key = "secret-1"
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            session.add(Organization(id=org, name="Idem Org", slug=org))
            session.add(
                Finding(
                    id=finding_id, org_id=org, kind="LEAKAGE", status="DETECTED",
                    title="Idem finding", exposure_amount="100.00",
                )
            )
            await session.commit()
        path = "/api/v1/integrations/aios/v1/events"
        body = json.dumps(
            {"type": "agent.action.completed", "payload": {"org_id": org, "finding_id": finding_id}},
            separators=(",", ":"),
        ).encode()
        idem_key = uuid.uuid4().hex
        hdr = sign_request("POST", path, body, "kid-1", "secret-1", idempotency_key=idem_key)
        hdr["Idempotency-Key"] = idem_key
        hdr["Content-Type"] = "application/json"
        r = c.post(path, content=body, headers=hdr)
        assert r.status_code == 200 and r.json()["deduplicated"] is False
        hdr2 = sign_request("POST", path, body, "kid-1", "secret-1", idempotency_key=idem_key)
        hdr2["Idempotency-Key"] = idem_key
        hdr2["Content-Type"] = "application/json"
        r2 = c.post(path, content=body, headers=hdr2)
        assert r2.json()["deduplicated"] is True
    finally:
        settings.aios_integration_enabled = orig_enabled
        settings.aios_service_key_id = orig_id
        settings.aios_service_key = orig_key
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationEvent, idem_key)
            if row:
                await session.delete(row)
            await session.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            await session.execute(delete(Finding).where(Finding.id == finding_id))
            await session.execute(delete(Organization).where(Organization.id == org))
            await session.commit()


async def test_agent_execution_status_tracks_deal_audit_job(monkeypatch):
    """Execution GET follows DealAuditJob status, not the cached queued snapshot."""
    import json
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import (
        Account,
        DealAuditJob,
        IntegrationEvent,
        IntegrationNonce,
        Opportunity,
        Organization,
    )
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org_id = f"org-exec-{uuid.uuid4().hex[:8]}"
    account_id = f"acc-exec-{uuid.uuid4().hex[:8]}"
    opp_id = f"opp-exec-{uuid.uuid4().hex[:8]}"
    job_id = f"job-exec-{uuid.uuid4().hex}"
    nonces: list[str] = []
    exec_id = legacy_id = None

    async def fake_enqueue(opportunity_id, org_id, prompt_version="v1"):
        assert opportunity_id == opp_id
        return job_id

    monkeypatch.setattr("app.workers.runner.enqueue_audit", fake_enqueue)
    orig = (
        settings.aios_integration_enabled,
        settings.aios_service_key_id,
        settings.aios_service_key,
    )
    c = TestClient(app)
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-exec"
        settings.aios_service_key = "secret-exec"
        _clear_nonces()
        _clear_events()

        async with get_sessionmaker()() as session:
            session.add(Organization(id=org_id, name="Exec Org", slug=org_id))
            session.add(Account(id=account_id, org_id=org_id, name="Exec Account"))
            session.add(
                Opportunity(id=opp_id, org_id=org_id, account_id=account_id, name="Exec Opp")
            )
            session.add(
                DealAuditJob(
                    id=job_id,
                    org_id=org_id,
                    opportunity_id=opp_id,
                    idempotency_key=f"exec-test:{job_id}",
                    status="QUEUED",
                )
            )
            await session.commit()

        create_path = "/api/v1/integrations/aios/v1/agent-executions"
        body = json.dumps({"opportunity_id": opp_id, "org_id": org_id, "action": "audit"}, separators=(",", ":")).encode()
        hdr = sign_request("POST", create_path, body, "kid-exec", "secret-exec")
        nonces.append(hdr["X-Service-Nonce"])
        hdr["Content-Type"] = "application/json"
        r = c.post(create_path, content=body, headers=hdr)
        assert r.status_code == 200, r.text
        created = r.json()
        exec_id = created["id"]
        assert created["status"] == "queued"
        assert created["job_id"] == job_id

        def get_execution(target_id):
            path = f"/api/v1/integrations/aios/v1/executions/{target_id}"
            h = sign_request("GET", path, None, "kid-exec", "secret-exec")
            nonces.append(h["X-Service-Nonce"])
            return c.get(path, headers=h).json()

        assert get_execution(exec_id)["status"] == "queued"

        async with get_sessionmaker()() as session:
            job = await session.get(DealAuditJob, job_id)
            job.status = "RUNNING"
            await session.commit()
        assert get_execution(exec_id)["status"] == "running"

        async with get_sessionmaker()() as session:
            job = await session.get(DealAuditJob, job_id)
            job.status = "DONE"
            job.result = {"exposure": "42.00"}
            await session.commit()
        done = get_execution(exec_id)
        assert done["status"] == "completed"
        assert done["result"] == {"exposure": "42.00"}

        async with get_sessionmaker()() as session:
            job = await session.get(DealAuditJob, job_id)
            job.status = "FAILED"
            job.error = "boom"
            await session.commit()
        failed = get_execution(exec_id)
        assert failed["status"] == "failed"
        assert failed["error"] == "boom"

        legacy_id = f"legacy-{uuid.uuid4().hex}"
        async with get_sessionmaker()() as session:
            session.add(
                IntegrationEvent(
                    idempotency_key=legacy_id,
                    peer="aios",
                    type="agent.execution",
                    payload={},
                    response={"id": legacy_id, "status": "queued"},
                    expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
                )
            )
            await session.commit()
        assert get_execution(legacy_id)["status"] == "queued"
    finally:
        (
            settings.aios_integration_enabled,
            settings.aios_service_key_id,
            settings.aios_service_key,
        ) = orig
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            for key in (exec_id, legacy_id):
                if key:
                    row = await session.get(IntegrationEvent, key)
                    if row:
                        await session.delete(row)
            job = await session.get(DealAuditJob, job_id)
            if job:
                await session.delete(job)
            await session.execute(delete(Opportunity).where(Opportunity.org_id == org_id))
            await session.execute(delete(Account).where(Account.org_id == org_id))
            await session.execute(delete(Organization).where(Organization.id == org_id))
            for nonce in nonces:
                await session.execute(
                    delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce)
                )
            await session.commit()


async def test_ingest_event_rejects_unknown_type_before_claim():
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationEvent, IntegrationNonce
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    path = "/api/v1/integrations/aios/v1/events"
    body = json.dumps({"type": "unknown.event", "payload": {}}, separators=(",", ":")).encode()
    idem_key = uuid.uuid4().hex
    hdr = sign_request("POST", path, body, "kid-bad", "secret-bad", idempotency_key=idem_key)
    nonce = hdr["X-Service-Nonce"]
    hdr["Idempotency-Key"] = idem_key
    hdr["Content-Type"] = "application/json"

    orig = (
        settings.aios_integration_enabled,
        settings.aios_service_key_id,
        settings.aios_service_key,
    )
    c = TestClient(app)
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-bad"
        settings.aios_service_key = "secret-bad"
        _clear_nonces()
        _clear_events()
        r = c.post(path, content=body, headers=hdr)
        assert r.status_code == 400
        assert "Unsupported event type" in r.json()["detail"]
        async with get_sessionmaker()() as session:
            assert await session.get(IntegrationEvent, idem_key) is None
    finally:
        (
            settings.aios_integration_enabled,
            settings.aios_service_key_id,
            settings.aios_service_key,
        ) = orig
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            await session.execute(
                delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce)
            )
            await session.commit()


async def test_outbox_enqueues_in_existing_transaction():
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationOutbox
    from app.integrations.aios.publisher import enqueue_outbox

    async with get_sessionmaker()() as session:
        key = await enqueue_outbox(
            "commitment.at_risk",
            {"finding_id": "finding-1"},
            business_trace_id="trace-1",
            session=session,
        )
        assert key in session.new or await session.get(IntegrationOutbox, key) is not None
        await session.rollback()
    async with get_sessionmaker()() as session:
        assert await session.get(IntegrationOutbox, key) is None
        key = await enqueue_outbox(
            "commitment.at_risk",
            {"finding_id": "finding-1"},
            business_trace_id="trace-1",
            session=session,
        )
        await session.commit()
    async with get_sessionmaker()() as session:
        row = (await session.execute(select(IntegrationOutbox).where(IntegrationOutbox.idempotency_key == key))).scalar_one()
        assert row.status == "pending"
        await session.delete(row)
        await session.commit()


async def test_session_bound_enqueue_schedules_flush_after_commit(monkeypatch):
    """Session-bound outbox schedules flush only after caller commit, never before."""
    import asyncio

    from sqlalchemy import delete

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationOutbox
    from app.integrations.aios import publisher

    scheduled: list[str] = []
    outbox_id = rolled_back_id = None

    async def fake_schedule_flush(target_id):
        scheduled.append(target_id)

    monkeypatch.setattr(publisher, "schedule_flush", fake_schedule_flush)
    try:
        async with get_sessionmaker()() as session:
            outbox_id = await publisher.enqueue_outbox(
                "test.event", {"n": 1}, session=session
            )
            await asyncio.sleep(0)
            assert scheduled == []
            await session.commit()
            await asyncio.sleep(0)
            assert scheduled == [outbox_id]

        async with get_sessionmaker()() as session:
            rolled_back_id = await publisher.enqueue_outbox(
                "test.event", {"n": 2}, session=session
            )
            await session.rollback()
            await asyncio.sleep(0)
            assert scheduled == [outbox_id]
    finally:
        async with get_sessionmaker()() as session:
            await session.execute(
                delete(IntegrationOutbox).where(
                    IntegrationOutbox.id.in_(
                        [i for i in (outbox_id, rolled_back_id) if i]
                    )
                )
            )
            await session.commit()


async def test_outbox_uses_caller_idempotency_key():
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationOutbox
    from app.integrations.aios.publisher import enqueue_outbox

    async with get_sessionmaker()() as session:
        outbox_id = await enqueue_outbox(
            "action.verified",
            {"action_id": "action-1"},
            idempotency_key="verify:action-1",
            session=session,
        )
        row = await session.get(IntegrationOutbox, outbox_id)
        assert row.idempotency_key == "verify:action-1"
        assert row.id != "verify:action-1"
        await session.rollback()


async def test_outbox_single_flush_records_failed_attempts(monkeypatch):
    import uuid
    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationOutbox
    from app.integrations.aios.publisher import integration_outbox_flush

    async def fail_send(*args, **kwargs):
        raise RuntimeError("peer unavailable")

    monkeypatch.setattr("app.integrations.aios.client.send_event", fail_send)
    enabled = settings.aios_integration_enabled
    base_url = settings.aios_base_url
    settings.aios_integration_enabled = True
    settings.aios_base_url = "http://peer.test"
    async with get_sessionmaker()() as session:
        outbox_id = uuid.uuid4().hex
        row = IntegrationOutbox(
            id=outbox_id,
            idempotency_key=outbox_id,
            event_type="test.event",
            payload={"id": "1"},
        )
        session.add(row)
        await session.commit()
    try:
        for _ in range(5):
            with pytest.raises(RuntimeError, match="peer unavailable"):
                await integration_outbox_flush(None, outbox_id)
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationOutbox, outbox_id)
            assert row.attempts == 5
            assert row.status == "failed"
            await session.delete(row)
            await session.commit()
    finally:
        settings.aios_integration_enabled = enabled
        settings.aios_base_url = base_url


async def test_list_deals_client(monkeypatch):
    from app.core.config import settings
    from app.integrations.aios import client

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"org_id": "org-1", "deals": [{"id": "d1", "lead_name": "X"}], "count": 1}

    async def fake_request(method, path, **kw):
        assert method == "GET"
        assert kw["params"] == {"org_id": "org-1", "limit": 5}
        return FakeResp()

    monkeypatch.setattr(client, "_request_with_retry", fake_request)
    monkeypatch.setattr(settings, "aios_base_url", "http://aios.test")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-1")
    monkeypatch.setattr(settings, "aios_service_key", "secret-1")
    monkeypatch.setattr(settings, "aios_integration_enabled", True)

    out = await client.list_deals("org-1", limit=5)
    assert out["count"] == 1
    assert out["deals"][0]["id"] == "d1"

    # empty org -> no request
    out2 = await client.list_deals("")
    assert out2["count"] == 0


# ── Regression: outbox idempotency ──────────────────────────────────────────


async def test_enqueue_outbox_kwarg_not_silently_skipped():
    """Caller kwargs reach the persisted row on the self-managed-session path."""
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationOutbox
    from app.integrations.aios.publisher import enqueue_outbox

    key = f"idem:{uuid.uuid4().hex}"
    outbox_id = await enqueue_outbox("test.event", {"n": 1}, idempotency_key=key)
    try:
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationOutbox, outbox_id)
            assert row.idempotency_key == key
            assert row.id != key
    finally:
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationOutbox, outbox_id)
            if row:
                await session.delete(row)
                await session.commit()

    # unknown kwarg must raise, never be swallowed by a **kwargs sink
    with pytest.raises(TypeError):
        await enqueue_outbox("test.event", {}, bogus_kwarg="x")


async def test_outbox_duplicate_idempotency_key_rejected(monkeypatch, tmp_path):
    """Same caller key twice cannot create two outbox rows (unique constraint).

    Fresh DB from current metadata: local arvo.db predates the unique index.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    import app.database.engine as db
    from app.core.config import settings
    from app.database.engine import get_sessionmaker, init_db
    from app.database.models import IntegrationOutbox
    from app.integrations.aios.publisher import enqueue_outbox

    monkeypatch.setattr(
        settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}"
    )
    db._engines.clear()
    db._sessions.clear()
    db._default_engine = None
    db._default_session = None
    await init_db()

    key = f"dup:{uuid.uuid4().hex}"
    async with get_sessionmaker()() as session:
        await enqueue_outbox("test.event", {"n": 1}, idempotency_key=key, session=session)
        await session.commit()
    async with get_sessionmaker()() as session:
        try:
            await enqueue_outbox(
                "test.event", {"n": 2}, idempotency_key=key, session=session
            )
            await session.commit()
        except IntegrityError:
            await session.rollback()
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(IntegrationOutbox).where(IntegrationOutbox.idempotency_key == key)
            )
        ).scalars().all()
        assert len(rows) == 1


# ── Regression: cross-replica replay ────────────────────────────────────────


async def test_cross_replica_replay_rejected_via_db_nonce():
    """Cleared in-memory cache (other replica) still cannot replay same nonce."""
    from sqlalchemy import delete

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce
    from app.integrations.aios.routes import _db_persist_nonce

    hdr = sign_request("GET", ROUTE, None, "kid-replica", "secret-replica")
    nonce = hdr["X-Service-Nonce"]
    try:
        assert verify_request(
            "kid-replica", hdr["X-Service-Timestamp"], nonce, "GET", ROUTE, None,
            hdr["X-Service-Signature"], "secret-replica",
        )
        assert await _db_persist_nonce(nonce) is True

        # simulate a second replica: empty in-memory nonce cache, shared DB
        _clear_nonces()
        assert verify_request(
            "kid-replica", hdr["X-Service-Timestamp"], nonce, "GET", ROUTE, None,
            hdr["X-Service-Signature"], "secret-replica",
        )
        assert await _db_persist_nonce(nonce) is False
    finally:
        _clear_nonces()
        async with get_sessionmaker()() as session:
            await session.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await session.commit()


# ── Regression: full deal loop ─────────────────────────────────────────────


async def test_deal_event_to_finding_to_verified_deal_update_loop(monkeypatch):
    """deal.created -> finding -> execute -> verify -> AIOS update -> deal.stage_changed."""
    import json
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from sqlalchemy import delete, select

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import (
        Action,
        FinancialEvent,
        FinancialLedger,
        Finding,
        FindingEvidence,
        IntegrationEvent,
        IntegrationNonce,
        IntegrationOutbox,
        Opportunity,
        Organization,
    )
    from app.integrations.aios import publisher
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org = f"org-loop-{uuid.uuid4().hex[:8]}"
    deal_id = f"deal-loop-{uuid.uuid4().hex[:8]}"
    idem_created = uuid.uuid4().hex
    idem_stage = uuid.uuid4().hex
    finding_id = None
    action_id = None
    nonces: list[str] = []
    captured: list[tuple[str, dict, dict]] = []

    async def fake_enqueue(event_type, payload, **kw):
        captured.append((event_type, payload, kw))
        return f"outbox-{len(captured)}"

    orig = (
        settings.aios_integration_enabled,
        settings.aios_service_key_id,
        settings.aios_service_key,
    )
    c = TestClient(app)
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-loop"
        settings.aios_service_key = "secret-loop"
        _clear_nonces()
        _clear_events()
        monkeypatch.setattr(publisher, "enqueue_outbox", fake_enqueue)

        def post_event(type_, payload, key):
            body = json.dumps(
                {"type": type_, "payload": payload}, separators=(",", ":")
            ).encode()
            hdr = sign_request("POST", "/api/v1/integrations/aios/v1/events", body, "kid-loop", "secret-loop", idempotency_key=key)
            nonces.append(hdr["X-Service-Nonce"])
            hdr["Idempotency-Key"] = key
            hdr["Content-Type"] = "application/json"
            return c.post("/api/v1/integrations/aios/v1/events", content=body, headers=hdr)

        async with get_sessionmaker()() as session:
            session.add(Organization(id=org, name="Loop Org", slug=org))
            await session.commit()

        # 1. AIOS tells ARVO a deal was created; ARVO derives a finding.
        r = post_event(
            "deal.created",
            {
                "org_id": org, "id": deal_id, "deal_id": deal_id,
                "amount": "1000.00", "currency": "BRL", "stage": "NEGOTIATION",
                "lead_name": "Loop Lead",
            },
            idem_created,
        )
        assert r.status_code == 200
        async with get_sessionmaker()() as session:
            finding = (
                await session.execute(select(Finding).where(Finding.org_id == org))
            ).scalars().first()
        assert finding is not None
        assert (finding.provenance or {}).get("source_deal_id") == deal_id
        finding_id = finding.id

        # 2. Action lifecycle: evidence + executed action tied to that finding.
        action_id = f"action-loop-{uuid.uuid4().hex[:8]}"
        async with get_sessionmaker()() as session:
            f = await session.get(Finding, finding_id)
            f.evidence_pack = {"source": "deal.created", "deal_id": deal_id}
            f.exposure_amount = "1000.00"
            session.add(
                Action(
                    id=action_id, org_id=org, finding_id=finding_id,
                    title="Recover deal leakage", status="EXECUTED",
                )
            )
            await session.commit()

        from app.api.actions import verify_action

        ctx = {
            "membership": SimpleNamespace(org_id=org, role="OWNER"),
            "user": SimpleNamespace(id="user-loop"),
        }
        verified = await verify_action(action_id, {"verified_amount": "100.00"}, ctx)
        assert verified["status"] == "VERIFIED"

        # 3. Verified recovery published back to AIOS carries deal_id for update.
        updates = [p for (t, p, _) in captured if t == "action.verified"]
        assert updates and updates[0].get("deal_id") == deal_id

        # 4. AIOS echoes the stage change; ARVO accepts it, closing the loop.
        r2 = post_event(
            "deal.stage_changed",
            {"org_id": org, "id": deal_id, "deal_id": deal_id, "stage": "CLOSED_WON"},
            idem_stage,
        )
        assert r2.status_code == 200
    finally:
        (
            settings.aios_integration_enabled,
            settings.aios_service_key_id,
            settings.aios_service_key,
        ) = orig
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            for key in (idem_created, idem_stage):
                row = await session.get(IntegrationEvent, key)
                if row:
                    await session.delete(row)
            if finding_id:
                await session.execute(
                    delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id)
                )
            if action_id:
                await session.execute(
                    delete(IntegrationOutbox).where(
                        IntegrationOutbox.business_trace_id == action_id
                    )
                )
            for model in (Action, Finding, Opportunity, FinancialLedger, FinancialEvent):
                await session.execute(delete(model).where(model.org_id == org))
            await session.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await session.execute(
                    delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce)
                )
            await session.commit()


# ── Regression: prod readiness + list_deals full path ──────────────────────


async def test_sqlite_rejected_in_prod_readiness(monkeypatch):
    import json

    from app.core.config import settings
    from app.main import health_ready

    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///./arvo.db")
    resp = await health_ready()
    assert resp.status_code == 503
    assert json.loads(resp.body)["status"] == "not_ready"


async def test_list_deals_uses_full_versioned_path(monkeypatch):
    from app.integrations.aios import client

    seen: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"org_id": "org-1", "deals": [], "count": 0}

    async def fake_request(method, path, **kw):
        seen["path"] = path
        seen["params"] = kw.get("params")
        seen["prebuilt_headers"] = "headers" in kw
        return FakeResp()

    monkeypatch.setattr(client, "_request_with_retry", fake_request)
    await client.list_deals("org-1", limit=7)
    assert seen["path"] == "/api/integrations/arvo/v1/deals"
    assert seen["params"] == {"org_id": "org-1", "limit": 7}
    assert seen["prebuilt_headers"] is False


# ── Regression: business_trace_id in outbound signed body ───────────────────


async def test_outbox_business_trace_id_in_signed_body(monkeypatch):
    """_deliver forwards row.business_trace_id; send_event puts it top-level and HMAC covers exact bytes."""
    import json
    from types import SimpleNamespace

    from app.core.config import settings
    from app.integrations.aios import client
    from app.integrations.aios.auth import _clear_nonces, verify_request
    from app.integrations.aios.publisher import _deliver

    trace = "trace-abc123_DEF-9"
    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok"}

    class FakeClient:
        async def request(self, method, path, **kw):
            captured["method"] = method
            captured["path"] = path
            captured["content"] = kw["content"]
            captured["headers"] = kw["headers"]
            return FakeResp()

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(client, "_get_client", fake_get_client)
    monkeypatch.setattr(settings, "aios_base_url", "http://peer.test")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-trace")
    monkeypatch.setattr(settings, "aios_service_key", "secret-trace")
    _clear_nonces()

    row = SimpleNamespace(
        event_type="action.verified",
        payload={"action_id": "action-1"},
        idempotency_key="verify:action-1",
        business_trace_id=trace,
        attempts=1,
        id="outbox-trace-test",
        status="processing",
    )
    assert await _deliver(row, raise_errors=True) is True

    body = json.loads(captured["content"])
    assert body["business_trace_id"] == trace
    hdr = captured["headers"]
    assert verify_request(
        "kid-trace",
        hdr["X-Service-Timestamp"],
        hdr["X-Service-Nonce"],
        captured["method"],
        captured["path"],
        captured["content"],
        hdr["X-Service-Signature"],
        "secret-trace",
        idempotency_key=row.idempotency_key,
    )
    _clear_nonces()


async def test_send_event_omits_trace_when_none(monkeypatch):
    """No business_trace_id -> no top-level field; HMAC still signs exact bytes."""
    import json

    from app.core.config import settings
    from app.integrations.aios import client
    from app.integrations.aios.auth import _clear_nonces, verify_request

    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok"}

    class FakeClient:
        async def request(self, method, path, **kw):
            captured["method"] = method
            captured["path"] = path
            captured["content"] = kw["content"]
            captured["headers"] = kw["headers"]
            return FakeResp()

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(client, "_get_client", fake_get_client)
    monkeypatch.setattr(settings, "aios_base_url", "http://peer.test")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-none")
    monkeypatch.setattr(settings, "aios_service_key", "secret-none")
    _clear_nonces()

    await client.send_event("test.event", {"a": 1}, "idem-none")

    body = json.loads(captured["content"])
    assert "business_trace_id" not in body
    hdr = captured["headers"]
    assert verify_request(
        "kid-none",
        hdr["X-Service-Timestamp"],
        hdr["X-Service-Nonce"],
        captured["method"],
        captured["path"],
        captured["content"],
        hdr["X-Service-Signature"],
        "secret-none",
        idempotency_key="idem-none",
    )
    _clear_nonces()


# ── Phase 4: recalculation on agent.action.completed ────────────────────────


async def _post_aios_event(c, body: dict, key: str, nonces: list, kid="kid-recalc", secret="secret-recalc"):
    import json

    path = "/api/v1/integrations/aios/v1/events"
    raw = json.dumps(body, separators=(",", ":")).encode()
    hdr = sign_request("POST", path, raw, kid, secret, idempotency_key=key)
    nonces.append(hdr["X-Service-Nonce"])
    hdr["Idempotency-Key"] = key
    hdr["Content-Type"] = "application/json"
    return c.post(path, content=raw, headers=hdr)


async def test_agent_action_completed_recalculates_once(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, func, select

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import (
        FinancialEvent,
        FinancialLedger,
        Finding,
        FindingEvidence,
        IntegrationEvent,
        IntegrationNonce,
        Organization,
    )
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org = f"org-recalc-{uuid.uuid4().hex[:8]}"
    finding_id = f"finding-recalc-{uuid.uuid4().hex[:8]}"
    keys = [uuid.uuid4().hex, uuid.uuid4().hex]
    nonces: list[str] = []
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-recalc")
    monkeypatch.setattr(settings, "aios_service_key", "secret-recalc")
    _clear_nonces()
    _clear_events()
    c = TestClient(app)
    try:
        async with get_sessionmaker()() as session:
            session.add(Organization(id=org, name="Recalc Org", slug=org))
            session.add(
                Finding(
                    id=finding_id, org_id=org, kind="LEAKAGE", status="DETECTED",
                    title="Recalc finding", exposure_amount="1000.00",
                )
            )
            await session.commit()

        payload = {"org_id": org, "finding_id": finding_id, "verified_amount": "250.00"}
        first = await _post_aios_event(c, {"type": "agent.action.completed", "payload": payload}, keys[0], nonces)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["recalculated"] is True
        assert body["verified_amount"] == "250.00"

        replay = await _post_aios_event(c, {"type": "agent.action.completed", "payload": payload}, keys[0], nonces)
        assert replay.json()["deduplicated"] is True

        # Different idempotency key: handler runs again but must not double-count.
        again = await _post_aios_event(c, {"type": "agent.action.completed", "payload": payload}, keys[1], nonces)
        assert again.json()["verified_amount"] == "250.00"

        async with get_sessionmaker()() as session:
            count = (
                await session.execute(
                    select(func.count(FinancialEvent.id)).where(FinancialEvent.org_id == org)
                )
            ).scalar()
            ledger = (
                await session.execute(select(FinancialLedger).where(FinancialLedger.org_id == org))
            ).scalars().all()
            finding = await session.get(Finding, finding_id)
        assert count == 1
        assert len(ledger) == 1 and ledger[0].amount == "250.00"
        assert finding.exposure_amount == "1000.00"  # untouched by verified_amount
        assert (finding.provenance or {}).get("verified_amount") == "250.00"
    finally:
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            for key in keys:
                row = await session.get(IntegrationEvent, key)
                if row:
                    await session.delete(row)
            await session.execute(delete(FinancialLedger).where(FinancialLedger.org_id == org))
            await session.execute(delete(FinancialEvent).where(FinancialEvent.org_id == org))
            await session.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            await session.execute(delete(Finding).where(Finding.org_id == org))
            await session.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await session.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await session.commit()


@pytest.mark.parametrize("failed", [True, False])
async def test_agent_action_completed_failed_status_gates_financial_evidence(monkeypatch, failed):
    """status:failed keeps evidence but never creates recovered money or mutates exposure."""
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, func, select

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import (
        FinancialEvent,
        FinancialLedger,
        Finding,
        FindingEvidence,
        IntegrationEvent,
        IntegrationNonce,
        Organization,
    )
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org = f"org-failed-{uuid.uuid4().hex[:8]}"
    finding_id = f"finding-failed-{uuid.uuid4().hex[:8]}"
    key = uuid.uuid4().hex
    nonces: list[str] = []
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-recalc")
    monkeypatch.setattr(settings, "aios_service_key", "secret-recalc")
    _clear_nonces()
    _clear_events()
    c = TestClient(app)
    try:
        async with get_sessionmaker()() as s:
            s.add(Organization(id=org, name="Failed Org", slug=org))
            s.add(
                Finding(
                    id=finding_id, org_id=org, kind="LEAKAGE", status="DETECTED",
                    title="Failed finding", exposure_amount="500.00",
                )
            )
            await s.commit()

        payload = {
            "org_id": org, "finding_id": finding_id,
            "verified_amount": "250.00", "exposure_amount": "777.00",
        }
        if failed:
            payload["status"] = "failed"
        r = await _post_aios_event(
            c, {"type": "agent.action.completed", "payload": payload}, key, nonces
        )
        assert r.status_code == 200, r.text
        body = r.json()
        if failed:
            assert body["status"] == "failed" and body["recalculated"] is False
            assert "verified_amount" not in body and "exposure_amount" not in body
        else:
            assert body["status"] == "processed" and body["recalculated"] is True
            assert body["verified_amount"] == "250.00"

        async with get_sessionmaker()() as s:
            finding = await s.get(Finding, finding_id)
            evidence = (
                await s.execute(select(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            ).scalars().one()
            fe = (await s.execute(select(func.count(FinancialEvent.id)).where(FinancialEvent.org_id == org))).scalar()
            fl = (await s.execute(select(func.count(FinancialLedger.id)).where(FinancialLedger.org_id == org))).scalar()
        assert finding.exposure_amount == "500.00"  # never overwritten
        assert evidence.content["payload"]["verified_amount"] == "250.00"
        if failed:
            assert evidence.content["payload"]["status"] == "failed"
            assert fe == 0 and fl == 0
        else:
            assert fe == 1 and fl == 1
    finally:
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as s:
            row = await s.get(IntegrationEvent, key)
            if row:
                await s.delete(row)
            await s.execute(delete(FinancialLedger).where(FinancialLedger.org_id == org))
            await s.execute(delete(FinancialEvent).where(FinancialEvent.org_id == org))
            await s.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            await s.execute(delete(Finding).where(Finding.id == finding_id))
            await s.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await s.commit()


async def test_agent_action_completed_exposure_amount_updates_real_finding(monkeypatch):
    """AIOS echo of ARVO's exposure must recalc the finding, not arrive empty."""
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, func, select

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import (
        FinancialEvent,
        FinancialLedger,
        Finding,
        FindingEvidence,
        IntegrationEvent,
        IntegrationNonce,
        Organization,
    )
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org = f"org-exposure-{uuid.uuid4().hex[:8]}"
    finding_id = f"finding-exposure-{uuid.uuid4().hex[:8]}"
    key = uuid.uuid4().hex
    nonces: list[str] = []
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-recalc")
    monkeypatch.setattr(settings, "aios_service_key", "secret-recalc")
    _clear_nonces()
    _clear_events()
    c = TestClient(app)
    try:
        async with get_sessionmaker()() as session:
            session.add(Organization(id=org, name="Exposure Org", slug=org))
            session.add(
                Finding(
                    id=finding_id, org_id=org, kind="LEAKAGE", status="DETECTED",
                    title="Exposure finding", exposure_amount="500.00", currency="BRL",
                )
            )
            await session.commit()

        payload = {
            "org_id": org,
            "finding_id": finding_id,
            "exposure_amount": "1375.50",
            "currency": "BRL",
        }
        r = await _post_aios_event(
            c, {"type": "agent.action.completed", "payload": payload}, key, nonces
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["recalculated"] is True
        assert body["exposure_amount"] == "1375.50"
        assert body["evidence_id"]

        async with get_sessionmaker()() as session:
            finding = await session.get(Finding, finding_id)
            evidence = (
                await session.execute(
                    select(FindingEvidence).where(FindingEvidence.finding_id == finding_id)
                )
            ).scalars().one()
            fe_count = (
                await session.execute(
                    select(func.count(FinancialEvent.id)).where(FinancialEvent.org_id == org)
                )
            ).scalar()
            fl_count = (
                await session.execute(
                    select(func.count(FinancialLedger.id)).where(FinancialLedger.org_id == org)
                )
            ).scalar()
        assert finding.exposure_amount == "1375.50"  # exposure branch, not a recovery event
        assert (finding.provenance or {}).get("exposure_amount") == "1375.50"
        assert evidence.content["source"] == "agent.action.completed"
        assert evidence.content["payload"]["exposure_amount"] == "1375.50"
        assert evidence.content["payload"]["currency"] == "BRL"
        assert fe_count == 0 and fl_count == 0
    finally:
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationEvent, key)
            if row:
                await session.delete(row)
            await session.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            await session.execute(delete(Finding).where(Finding.org_id == org))
            await session.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await session.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await session.commit()


async def test_agent_action_completed_without_amount_is_noop(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, func, select

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import (
        FinancialEvent,
        FinancialLedger,
        Finding,
        FindingEvidence,
        IntegrationEvent,
        IntegrationNonce,
        Organization,
    )
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org = f"org-noamt-{uuid.uuid4().hex[:8]}"
    finding_id = f"finding-noamt-{uuid.uuid4().hex[:8]}"
    key = uuid.uuid4().hex
    nonces: list[str] = []
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-noamt")
    monkeypatch.setattr(settings, "aios_service_key", "secret-noamt")
    _clear_nonces()
    _clear_events()
    c = TestClient(app)
    try:
        async with get_sessionmaker()() as session:
            session.add(Organization(id=org, name="NoAmt Org", slug=org))
            session.add(
                Finding(
                    id=finding_id, org_id=org, kind="LEAKAGE", status="DETECTED",
                    title="No amount finding", exposure_amount="500.00",
                )
            )
            await session.commit()

        r = await _post_aios_event(
            c,
            {"type": "agent.action.completed", "payload": {"org_id": org, "finding_id": finding_id}},
            key,
            nonces,
            kid="kid-noamt",
            secret="secret-noamt",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["recalculated"] is False
        assert "verified_amount" not in body and "exposure_amount" not in body

        async with get_sessionmaker()() as session:
            fe = (
                await session.execute(
                    select(func.count(FinancialEvent.id)).where(FinancialEvent.org_id == org)
                )
            ).scalar()
            fl = (
                await session.execute(
                    select(func.count(FinancialLedger.id)).where(FinancialLedger.org_id == org)
                )
            ).scalar()
            finding = await session.get(Finding, finding_id)
        assert fe == 0 and fl == 0
        assert finding.exposure_amount == "500.00"
    finally:
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationEvent, key)
            if row:
                await session.delete(row)
            await session.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            await session.execute(delete(Finding).where(Finding.org_id == org))
            await session.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await session.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await session.commit()


@pytest.mark.parametrize("mode", ["no_finding_id", "finding_not_found"])
async def test_unhandled_agent_action_is_retryable(monkeypatch, mode):
    """handled:false -> 422, reservation released, retry with valid payload works."""
    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding, FindingEvidence, IntegrationEvent, IntegrationNonce, Organization
    from app.integrations.aios import routes
    from app.integrations.aios.events import AiosEvent
    from app.main import app

    org = f"org-drop-{uuid.uuid4().hex[:8]}"
    finding_id = f"finding-drop-{uuid.uuid4().hex[:8]}"
    key = f"drop-{uuid.uuid4().hex}"
    nonces: list[str] = []
    routes._clear_events()
    _clear_nonces()
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-recalc")
    monkeypatch.setattr(settings, "aios_service_key", "secret-recalc")
    c = TestClient(app)
    async with get_sessionmaker()() as s:
        s.add(Organization(id=org, name="Drop Org", slug=org))
        s.add(
            Finding(
                id=finding_id, org_id=org, kind="LEAKAGE", status="DETECTED",
                title="Drop finding", exposure_amount="100.00",
            )
        )
        await s.commit()
    try:
        bad_payload = {"org_id": org}
        if mode == "finding_not_found":
            bad_payload["finding_id"] = f"missing-{uuid.uuid4().hex}"
        wire = await _post_aios_event(
            c, {"type": "agent.action.completed", "payload": bad_payload}, key, nonces
        )
        assert wire.status_code == 422
        assert routes._get_idempotent(key) is None
        async with get_sessionmaker()() as s:
            row = await s.get(IntegrationEvent, key)
            assert row is None or row.response.get("status") != "processed"

        good = AiosEvent(
            type="agent.action.completed",
            payload={"org_id": org, "finding_id": finding_id},
        )
        retried = await routes.ingest_event(good, key)
        assert retried["status"] == "processed" and retried["deduplicated"] is False
        assert routes._get_idempotent(key)["status"] == "processed"
    finally:
        routes._clear_events()
        _clear_nonces()
        async with get_sessionmaker()() as s:
            row = await s.get(IntegrationEvent, key)
            if row:
                await s.delete(row)
            await s.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            await s.execute(delete(Finding).where(Finding.id == finding_id))
            await s.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await s.commit()


# ── Phase 2: signed service routes map peer org to opportunity org ──────────


async def test_agent_execution_rejects_tenant_mismatch(monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import Account, IntegrationNonce, Opportunity, Organization
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    org_a = f"org-tenant-a-{uuid.uuid4().hex[:8]}"
    org_b = f"org-tenant-b-{uuid.uuid4().hex[:8]}"
    account_id = f"acc-tenant-{uuid.uuid4().hex[:8]}"
    opp_id = f"opp-tenant-{uuid.uuid4().hex[:8]}"
    nonces: list[str] = []
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-tenant")
    monkeypatch.setattr(settings, "aios_service_key", "secret-tenant")
    monkeypatch.setattr(settings, "aios_allowed_orgs", "")
    _clear_nonces()
    _clear_events()
    c = TestClient(app)
    path = "/api/v1/integrations/aios/v1/agent-executions"
    try:
        async with get_sessionmaker()() as session:
            session.add(Organization(id=org_a, name="Tenant A", slug=org_a))
            session.add(Organization(id=org_b, name="Tenant B", slug=org_b))
            session.add(Account(id=account_id, org_id=org_a, name="Tenant Account"))
            session.add(Opportunity(id=opp_id, org_id=org_a, account_id=account_id, name="Tenant Opp"))
            await session.commit()

        def post(body: dict):
            raw = json.dumps(body, separators=(",", ":")).encode()
            hdr = sign_request("POST", path, raw, "kid-tenant", "secret-tenant")
            nonces.append(hdr["X-Service-Nonce"])
            hdr["Content-Type"] = "application/json"
            return c.post(path, content=raw, headers=hdr)

        # Peer claims org B but opportunity belongs to org A.
        r = post({"opportunity_id": opp_id, "org_id": org_b, "action": "audit"})
        assert r.status_code == 403, r.text

        # Missing peer org ref is rejected before any queue work.
        r2 = post({"opportunity_id": opp_id, "action": "audit"})
        assert r2.status_code == 400, r2.text
    finally:
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            await session.execute(delete(Opportunity).where(Opportunity.org_id == org_a))
            await session.execute(delete(Account).where(Account.org_id == org_a))
            for org in (org_a, org_b):
                await session.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await session.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await session.commit()


async def test_ingest_event_rejects_invalid_envelope(monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from sqlalchemy import delete

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    path = "/api/v1/integrations/aios/v1/events"
    nonces: list[str] = []
    idem_key = uuid.uuid4().hex
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-envbad")
    monkeypatch.setattr(settings, "aios_service_key", "secret-envbad")
    _clear_nonces()
    _clear_events()
    c = TestClient(app)
    try:
        for bad in (
            {"type": "deal.created", "payload": {}, "event_version": "x" * 17},
            {"type": "BAD TYPE!", "payload": {}},
        ):
            raw = json.dumps(bad, separators=(",", ":")).encode()
            hdr = sign_request("POST", path, raw, "kid-envbad", "secret-envbad", idempotency_key=idem_key)
            nonces.append(hdr["X-Service-Nonce"])
            hdr["Idempotency-Key"] = idem_key
            hdr["Content-Type"] = "application/json"
            assert c.post(path, content=raw, headers=hdr).status_code == 422
    finally:
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as session:
            for nonce in nonces:
                await session.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await session.commit()


# ── RLS org context ────────────────────────────────────────────────────────


async def test_set_org_context_sqlite_is_noop():
    from types import SimpleNamespace

    from app.integrations.aios.routes import _set_org_context

    class SqliteSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        async def execute(self, *args, **kwargs):
            raise AssertionError("set_config must not run on SQLite")

    await _set_org_context(SqliteSession(), "org-sqlite")


async def test_set_org_context_postgres_failure_fails_closed():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.integrations.aios.routes import _set_org_context

    calls = []

    class PostgresSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, stmt, params=None):
            calls.append((str(stmt), params))
            raise RuntimeError("permission denied for function set_config")

    with pytest.raises(HTTPException) as exc:
        await _set_org_context(PostgresSession(), "org-pg")
    assert exc.value.status_code == 503
    assert "set_config('app.current_org_id'" in calls[0][0]
    assert calls[0][1] == {"org": "org-pg"}


async def test_rls_context_applied_to_all_agent_execution_sessions(monkeypatch):
    """Every DB session in create_agent_execution/get_execution sets org context."""
    from fastapi import HTTPException
    from sqlalchemy import delete, select

    from app.database.engine import get_sessionmaker
    from app.database.models import (
        Account,
        IntegrationEvent,
        Opportunity,
        Organization,
    )
    from app.integrations.aios import routes
    from app.workers import runner

    org = f"org-rls-{uuid.uuid4().hex[:8]}"
    account_id = f"acc-rls-{uuid.uuid4().hex[:8]}"
    opp_id = f"opp-rls-{uuid.uuid4().hex[:8]}"
    job_id = f"job-rls-{uuid.uuid4().hex}"
    calls: list[str | None] = []

    async def record_context(session, org_id):
        calls.append(org_id)

    async def enqueue_ok(opportunity_id, org_id, prompt_version="v1"):
        return job_id

    async def enqueue_unavailable(opportunity_id, org_id, prompt_version="v1"):
        return None

    monkeypatch.setattr(routes, "_set_org_context", record_context)
    monkeypatch.setattr(runner, "enqueue_audit", enqueue_ok)
    routes._clear_events()
    try:
        async with get_sessionmaker()() as session:
            session.add(Organization(id=org, name="RLS Org", slug=org))
            session.add(Account(id=account_id, org_id=org, name="RLS Account"))
            session.add(
                Opportunity(id=opp_id, org_id=org, account_id=account_id, name="RLS Opp")
            )
            await session.commit()

        body = routes.AgentExecutionRequest(opportunity_id=opp_id, org_id=org, action="audit")
        created = await routes.create_agent_execution(body)
        assert created["org_id"] == org
        assert calls == [org, org, org]  # resolve, persist, response update

        calls.clear()
        fetched = await routes.get_execution(created["id"])
        assert calls == [org]  # job status read scoped from cached org id
        assert fetched["status"] == "queued"

        calls.clear()
        monkeypatch.setattr(runner, "enqueue_audit", enqueue_unavailable)
        with pytest.raises(HTTPException) as exc:
            await routes.create_agent_execution(body)
        assert exc.value.status_code == 503
        assert calls == [org, org, org]  # resolve, persist, rollback delete
    finally:
        routes._clear_events()
        async with get_sessionmaker()() as session:
            rows = (
                await session.execute(
                    select(IntegrationEvent).where(
                        IntegrationEvent.peer == "aios",
                        IntegrationEvent.type == "agent.execution",
                    )
                )
            ).scalars().all()
            for row in rows:
                if (row.payload or {}).get("opportunity_id") == opp_id:
                    await session.delete(row)
            await session.execute(delete(Opportunity).where(Opportunity.org_id == org))
            await session.execute(delete(Account).where(Account.org_id == org))
            await session.execute(delete(Organization).where(Organization.id == org))
            await session.commit()


async def test_get_execution_legacy_cache_without_org_fails_closed(monkeypatch):
    """Legacy cache with job_id but no org_id -> 503; no DB session, no state change."""
    import app.database.engine as db_engine
    from fastapi import HTTPException

    from app.integrations.aios import routes

    def no_db():
        raise AssertionError("DB session opened without RLS org id")

    monkeypatch.setattr(db_engine, "get_sessionmaker", no_db)
    routes._clear_events()
    legacy = {"id": "exec-legacy-no-org", "status": "queued", "job_id": "job-legacy-no-org"}
    routes._store_idempotent(legacy["id"], legacy)
    try:
        with pytest.raises(HTTPException) as exc:
            await routes.get_execution(legacy["id"])
        assert exc.value.status_code == 503
        assert "RLS org context unavailable" in exc.value.detail
        assert routes._get_idempotent(legacy["id"]) == legacy
    finally:
        routes._clear_events()


async def test_agent_action_completed_missing_org_fails_closed(monkeypatch):
    """Missing/empty org_id -> 503 before any DB session or write."""
    import app.database.engine as db_engine
    from fastapi import HTTPException

    from app.integrations.aios import routes

    def no_db():
        raise AssertionError("DB session opened before org check")

    monkeypatch.setattr(db_engine, "get_sessionmaker", no_db)
    for payload in (
        {"finding_id": "finding-fake", "verified_amount": "10.00"},
        {"finding_id": "finding-fake", "org_id": "", "verified_amount": "10.00"},
    ):
        with pytest.raises(HTTPException) as exc:
            await routes._handle_agent_action_completed(payload, "trace-fake")
        assert exc.value.status_code == 503
        assert "RLS org context" in exc.value.detail


# ── SSO ticket auto-provisioning ────────────────────────────────────────────

SSO_PATH = "/api/v1/integrations/aios/v1/sso-ticket"


def _signed_sso_body(sub, email, org_id, role="VIEWER", nonce=None):
    """Mint the same signed JWT ticket AIOS sends for SSO exchange."""
    import time

    import jwt

    nonce = nonce or f"nonce-{uuid.uuid4().hex}"
    claims = {
        "iss": "aios", "aud": "arvo", "iat": int(time.time()),
        "exp": int(time.time()) + 60, "jti": nonce,
        "sub": sub, "email": email, "org_id": org_id, "role": role,
    }
    return {
        "ticket": jwt.encode(claims, "sso-secret", algorithm="HS256"),
        "nonce": nonce,
        "key_id": "kid-sso",
    }


def _sso_client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app.core.config import settings

    async def no_redis():
        return None

    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key", "sso-secret")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-sso")
    monkeypatch.setattr(settings, "aios_allowed_orgs", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1000)
    monkeypatch.setattr(main_module, "_redis_client", no_redis)
    return TestClient(main_module.app)


async def test_sso_auto_provisions_new_user_and_membership(monkeypatch):
    from sqlalchemy import delete, select

    from app.core.security import decode_token, verify_password
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce, Membership, Organization, User

    tag = uuid.uuid4().hex[:10]
    org_id = f"org-{tag}"
    sub = str(uuid.uuid4())
    email = f"New-{tag}@Example.COM"
    body = _signed_sso_body(sub, email, org_id, role="MANAGER")
    async with get_sessionmaker()() as s:
        s.add(Organization(id=org_id, name=f"SSO {tag}", slug=f"sso-{tag}"))
        await s.commit()
    try:
        r = _sso_client(monkeypatch).post(SSO_PATH, json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert (data["user_id"], data["org_id"], data["role"]) == (sub, org_id, "MANAGER")
        assert decode_token(data["access_token"])["sub"] == sub
        async with get_sessionmaker()() as s:
            user = await s.get(User, sub)
            assert user is not None and user.is_active is True
            assert user.email == email.strip().lower()
            assert user.hashed_password and not verify_password("password123", user.hashed_password)
            membership = (
                await s.execute(
                    select(Membership).where(
                        Membership.user_id == sub, Membership.org_id == org_id
                    )
                )
            ).scalar_one()
            assert membership.role == "MANAGER" and membership.is_active is True
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Membership).where(Membership.org_id == org_id))
            await s.execute(delete(User).where(User.id == sub))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == body["nonce"]))
            await s.commit()


async def test_sso_email_collision_does_not_create_user(monkeypatch):
    from sqlalchemy import delete, select

    from app.core.security import hash_password
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce, Membership, Organization, User

    tag = uuid.uuid4().hex[:10]
    org_id = f"org-{tag}"
    existing_id = str(uuid.uuid4())
    sub = str(uuid.uuid4())
    email = f"collision-{tag}@example.com"
    body = _signed_sso_body(sub, f"  {email.upper()}  ", org_id)
    async with get_sessionmaker()() as s:
        s.add(Organization(id=org_id, name=f"SSO {tag}", slug=f"sso-{tag}"))
        s.add(User(id=existing_id, email=email, hashed_password=hash_password("secret123")))
        await s.commit()
    try:
        r = _sso_client(monkeypatch).post(SSO_PATH, json=body)
        assert r.status_code == 401, r.text
        async with get_sessionmaker()() as s:
            assert await s.get(User, sub) is None
            rows = (
                await s.execute(select(Membership).where(Membership.user_id == sub))
            ).scalars().all()
            assert rows == []
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Membership).where(Membership.org_id == org_id))
            await s.execute(delete(User).where(User.id.in_([existing_id, sub])))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == body["nonce"]))
            await s.commit()


async def test_sso_inactive_membership_stays_forbidden(monkeypatch):
    from sqlalchemy import delete, select

    from app.core.security import hash_password
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce, Membership, Organization, User

    tag = uuid.uuid4().hex[:10]
    org_id = f"org-{tag}"
    sub = str(uuid.uuid4())
    body = _signed_sso_body(sub, f"revoked-{tag}@example.com", org_id, role="ADMIN")
    async with get_sessionmaker()() as s:
        s.add(Organization(id=org_id, name=f"SSO {tag}", slug=f"sso-{tag}"))
        s.add(User(id=sub, email=f"revoked-{tag}@example.com", hashed_password=hash_password("secret123")))
        s.add(Membership(org_id=org_id, user_id=sub, role="VIEWER", is_active=False))
        await s.commit()
    try:
        r = _sso_client(monkeypatch).post(SSO_PATH, json=body)
        assert r.status_code == 403, r.text
        async with get_sessionmaker()() as s:
            membership = (
                await s.execute(
                    select(Membership).where(
                        Membership.user_id == sub, Membership.org_id == org_id
                    )
                )
            ).scalar_one()
            assert membership.is_active is False and membership.role == "VIEWER"
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Membership).where(Membership.org_id == org_id))
            await s.execute(delete(User).where(User.id == sub))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == body["nonce"]))
            await s.commit()


async def test_sso_first_login_race_single_user_and_membership(monkeypatch):
    """Stale first-login read must not 500; winner user/membership is reused."""
    from contextlib import asynccontextmanager

    from sqlalchemy import delete, func, select

    import app.database.engine as db_engine
    import app.integrations.aios.routes as routes
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce, Membership, Organization, User

    tag = uuid.uuid4().hex[:10]
    org_id = f"org-race-{tag}"
    sub = str(uuid.uuid4())
    email = f"race-{tag}@example.com"
    nonces = [f"nonce-race-{uuid.uuid4().hex}" for _ in range(2)]
    bodies = [_signed_sso_body(sub, email, org_id, nonce=n) for n in nonces]
    async with get_sessionmaker()() as s:
        s.add(Organization(id=org_id, name=f"Race {tag}", slug=f"race-{tag}"))
        await s.commit()

    state = {"stale": False}
    real_sessionmaker = db_engine.get_sessionmaker

    class StaleSession:
        def __init__(self, session):
            self._session = session
            self._stale = state["stale"]

        def __getattr__(self, name):
            return getattr(self._session, name)

        async def get(self, model, key, *args, **kwargs):
            if self._stale and model is User:
                return None
            return await self._session.get(model, key, *args, **kwargs)

        async def rollback(self):
            self._stale = False
            await self._session.rollback()

    @asynccontextmanager
    async def racing_sessionmaker():
        async with real_sessionmaker()() as session:
            yield StaleSession(session)

    monkeypatch.setattr(db_engine, "get_sessionmaker", lambda: racing_sessionmaker)
    context_calls = []
    real_set_org_context = routes._set_org_context

    async def recording_set_org_context(session, org_id):
        context_calls.append(org_id)
        await real_set_org_context(session, org_id)

    monkeypatch.setattr(routes, "_set_org_context", recording_set_org_context)
    client = _sso_client(monkeypatch)
    try:
        first = client.post(SSO_PATH, json=bodies[0])
        assert first.status_code == 200, first.text
        context_calls.clear()
        state["stale"] = True
        second = client.post(SSO_PATH, json=bodies[1])
        state["stale"] = False
        assert second.status_code == 200, second.text
        assert context_calls == [org_id, org_id]  # initial + re-applied after rollback
        async with real_sessionmaker()() as s:
            users = (await s.execute(select(func.count(User.id)).where(User.id == sub))).scalar()
            memberships = (
                await s.execute(select(func.count(Membership.id)).where(Membership.user_id == sub))
            ).scalar()
        assert users == 1 and memberships == 1
    finally:
        state["stale"] = False
        async with real_sessionmaker()() as s:
            await s.execute(delete(Membership).where(Membership.user_id == sub))
            await s.execute(delete(User).where(User.id == sub))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            for nonce in nonces:
                await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await s.commit()


async def test_sso_ticket_nonce_must_match_signed_jti(monkeypatch):
    """Body nonce cannot override signed jti; jti is the only replay key."""
    from sqlalchemy import delete

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce, Membership, Organization, User

    tag = uuid.uuid4().hex[:10]
    org_id = f"org-ssojti-{tag}"
    sub = str(uuid.uuid4())
    email = f"ssojti-{tag}@example.com"
    signed = _signed_sso_body(sub, email, org_id)
    jti = signed["nonce"]
    fresh = f"fresh-{uuid.uuid4().hex}"
    body = {**signed, "nonce": fresh}
    async with get_sessionmaker()() as s:
        s.add(Organization(id=org_id, name=f"SSO JTI {tag}", slug=f"ssojti-{tag}"))
        await s.commit()
    try:
        client = _sso_client(monkeypatch)
        mismatch = client.post(SSO_PATH, json=body)
        assert mismatch.status_code == 401, mismatch.text
        async with get_sessionmaker()() as s:
            assert await s.get(User, sub) is None
            assert await s.get(IntegrationNonce, jti) is None
            assert await s.get(IntegrationNonce, fresh) is None

        # correct nonce consumes jti and succeeds; replay of same ticket is 401
        ok = client.post(SSO_PATH, json=signed)
        assert ok.status_code == 200, ok.text
        replay = client.post(SSO_PATH, json=signed)
        assert replay.status_code == 401
        assert "Replay" in replay.json()["detail"]
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Membership).where(Membership.org_id == org_id))
            await s.execute(delete(User).where(User.id == sub))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            for nonce in (jti, fresh):
                await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await s.commit()


async def test_sso_ticket_without_jti_rejected(monkeypatch):
    """Missing signed jti -> 401, no nonce persisted."""
    import time

    import jwt
    from sqlalchemy import delete

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce

    tag = uuid.uuid4().hex[:10]
    sub = str(uuid.uuid4())
    nonce = f"nonce-nojti-{tag}"
    claims = {
        "iss": "aios", "aud": "arvo", "iat": int(time.time()),
        "exp": int(time.time()) + 60, "sub": sub,
        "email": f"nojti-{tag}@example.com", "org_id": f"org-nojti-{tag}",
    }
    body = {
        "ticket": jwt.encode(claims, "sso-secret", algorithm="HS256"),
        "nonce": nonce,
        "key_id": "kid-sso",
    }
    try:
        r = _sso_client(monkeypatch).post(SSO_PATH, json=body)
        assert r.status_code == 401, r.text
        async with get_sessionmaker()() as s:
            assert await s.get(IntegrationNonce, nonce) is None
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await s.commit()


async def test_sso_ticket_skew_within_leeway(monkeypatch):
    """SSO decode allows 30s skew; beyond leeway rejected; replay binding intact."""
    import time

    import jwt
    from sqlalchemy import delete

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce, Membership, Organization, User

    tag = uuid.uuid4().hex[:10]
    org_id = f"org-skew-{tag}"
    sub = str(uuid.uuid4())
    email = f"skew-{tag}@example.com"
    now = int(time.time())
    jti = f"nonce-skew-{tag}"
    future_jti = f"nonce-future-{tag}"

    def mint(iat, exp, nonce):
        claims = {
            "iss": "aios", "aud": "arvo", "iat": iat, "exp": exp, "jti": nonce,
            "sub": sub, "email": email, "org_id": org_id,
        }
        return {
            "ticket": jwt.encode(claims, "sso-secret", algorithm="HS256"),
            "nonce": nonce,
            "key_id": "kid-sso",
        }

    async with get_sessionmaker()() as s:
        s.add(Organization(id=org_id, name=f"Skew Org {tag}", slug=f"skew-{tag}"))
        await s.commit()
    try:
        client = _sso_client(monkeypatch)
        skewed = mint(now + 10, now + 70, jti)
        ok = client.post(SSO_PATH, json=skewed)
        assert ok.status_code == 200, ok.text
        replay = client.post(SSO_PATH, json=skewed)
        assert replay.status_code == 401
        assert "Replay" in replay.json()["detail"]

        beyond = client.post(SSO_PATH, json=mint(now + 60, now + 120, future_jti))
        assert beyond.status_code == 401
        async with get_sessionmaker()() as s:
            assert await s.get(IntegrationNonce, future_jti) is None
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Membership).where(Membership.org_id == org_id))
            await s.execute(delete(User).where(User.id == sub))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            for nonce in (jti, future_jti):
                await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await s.commit()


def test_signature_covers_query_and_idempotency_key():
    """Canonical includes normalized query and idem key; swaps invalidate HMAC."""
    from app.integrations.aios.auth import _normalize_query

    kid, key = "kid-q", "secret-q"
    assert _normalize_query("") == ""
    assert _normalize_query("b=2&a=1&empty=") == "a=1&b=2&empty="
    _clear_nonces()
    query = "b=2&a=1&empty="
    hdr = sign_request("GET", PATH, None, kid, key, query=query, idempotency_key="idem-q")
    common = (kid, hdr["X-Service-Timestamp"], hdr["X-Service-Nonce"], "GET", PATH, None, hdr["X-Service-Signature"], key)
    assert not verify_request(*common, query="b=3&a=1&empty=", idempotency_key="idem-q")
    assert not verify_request(*common, query=query, idempotency_key="idem-other")
    assert verify_request(*common, query=query, idempotency_key="idem-q")

    _clear_nonces()
    normalized = sign_request("GET", PATH, None, kid, key, query="a=1&b=2&empty=", idempotency_key="idem-q")
    assert verify_request(
        kid, normalized["X-Service-Timestamp"], normalized["X-Service-Nonce"], "GET", PATH, None,
        normalized["X-Service-Signature"], key, query=query, idempotency_key="idem-q",
    )
    _clear_nonces()


async def test_hmac_rejects_query_and_idempotency_key_tampering(monkeypatch):
    """Endpoint verification binds query string and Idempotency-Key."""
    import json

    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationEvent
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-tamper")
    monkeypatch.setattr(settings, "aios_service_key", "secret-tamper")
    _clear_nonces()
    _clear_events()
    idem_key = f"idem-tamper-{uuid.uuid4().hex}"
    try:
        health = "/api/v1/integrations/aios/v1/health"
        events = "/api/v1/integrations/aios/v1/events"
        raw = json.dumps({"type": "agent.action.completed", "payload": {}}, separators=(",", ":")).encode()
        c = TestClient(app)

        good = sign_request("GET", health, None, "kid-tamper", "secret-tamper", query="scope=a")
        assert c.get(f"{health}?scope=a", headers=good).status_code == 200

        swapped = sign_request("GET", health, None, "kid-tamper", "secret-tamper", query="scope=a")
        assert c.get(f"{health}?scope=b", headers=swapped).status_code == 401

        bad_key = sign_request("POST", events, raw, "kid-tamper", "secret-tamper", idempotency_key=idem_key)
        bad_key["Idempotency-Key"] = "idem-swapped"
        bad_key["Content-Type"] = "application/json"
        assert c.post(events, content=raw, headers=bad_key).status_code == 401

        good_key = sign_request("POST", events, raw, "kid-tamper", "secret-tamper", idempotency_key=idem_key)
        good_key["Idempotency-Key"] = idem_key
        good_key["Content-Type"] = "application/json"
        # Valid HMAC reaches handler; empty payload is unhandled -> 422, not 401.
        assert c.post(events, content=raw, headers=good_key).status_code == 422
    finally:
        _clear_nonces()
        _clear_events()
        async with get_sessionmaker()() as s:
            row = await s.get(IntegrationEvent, idem_key)
            if row:
                await s.delete(row)
                await s.commit()


def test_hmac_key_rotation_map_accepts_secondary_and_primary(monkeypatch):
    """Map secures rotation: secondary kid, rotated primary secret and legacy primary all verify."""
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-primary")
    monkeypatch.setattr(settings, "aios_service_key", "secret-primary")
    monkeypatch.setattr(settings, "aios_service_keys", "kid-secondary:secret-secondary, kid-primary:secret-rotated")
    _clear_nonces()
    c = TestClient(app)
    try:
        def call(kid: str, secret: str) -> int:
            return c.get(ROUTE, headers=sign_request("GET", ROUTE, None, kid, secret)).status_code

        assert call("kid-secondary", "secret-secondary") == 200
        assert call("kid-primary", "secret-rotated") == 200
        assert call("kid-primary", "secret-primary") == 200
        assert call("kid-primary", "secret-wrong") == 401
    finally:
        _clear_nonces()


async def test_completion_failure_marker_survives_past_60s(monkeypatch):
    """Side effect commits its own done marker; completion-write failure still dedups past 60s."""
    import uuid
    from datetime import datetime as real_datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import delete, select

    from app.core.config import settings
    from app.database.engine import get_sessionmaker
    from app.database.models import (
        Finding,
        FindingEvidence,
        IntegrationEvent,
        IntegrationNonce,
        Organization,
    )
    from app.integrations.aios import routes
    from app.main import app

    org = f"org-replay-{uuid.uuid4().hex[:8]}"
    finding_id = f"finding-replay-{uuid.uuid4().hex[:8]}"
    key = f"replay-{uuid.uuid4().hex}"
    nonces: list[str] = []
    handler_calls: list[str] = []
    clock = [0]

    class ShiftedDatetime:
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(seconds=clock[0])

    real_handler = routes._handle_agent_action_completed

    async def counted_handler(payload, trace_id, event_key=None, response=None):
        handler_calls.append(str(payload.get("finding_id")))
        return await real_handler(payload, trace_id, event_key, response)

    async def fail_complete(event_key, response):
        raise RuntimeError("completion write down")

    monkeypatch.setattr(routes, "_handle_agent_action_completed", counted_handler)
    monkeypatch.setattr(routes, "_db_complete_event", fail_complete)
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-recalc")
    monkeypatch.setattr(settings, "aios_service_key", "secret-recalc")
    _clear_nonces()
    routes._clear_events()

    async with get_sessionmaker()() as s:
        s.add(Organization(id=org, name="Replay Org", slug=org))
        s.add(
            Finding(
                id=finding_id, org_id=org, kind="LEAKAGE", status="DETECTED",
                title="Replay finding", exposure_amount="100.00",
            )
        )
        await s.commit()

    payload = {"org_id": org, "finding_id": finding_id}
    event = {"type": "agent.action.completed", "payload": payload}
    c = TestClient(app, raise_server_exceptions=False)
    try:
        first = await _post_aios_event(c, event, key, nonces)
        assert first.status_code >= 500
        assert handler_calls == [finding_id]

        async with get_sessionmaker()() as s:
            row = await s.get(IntegrationEvent, key)
            assert row.response["status"] == "processed"
            assert row.response["handled"] == "agent.action.completed"
            assert row.expires_at > real_datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60)

        # No sleeps: move the route clock past the old 60s claim window.
        clock[0] = 61
        monkeypatch.setattr(routes, "datetime", ShiftedDatetime)
        routes._clear_events()  # dedup must come from DB marker, not in-memory cache
        retry = await _post_aios_event(c, event, key, nonces)
        assert retry.status_code == 200 and retry.json()["deduplicated"] is True
        assert retry.json()["handled"] == "agent.action.completed"
        assert handler_calls == [finding_id]

        async with get_sessionmaker()() as s:
            evidence = (
                await s.execute(select(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            ).scalars().all()
        assert len(evidence) == 1
    finally:
        _clear_nonces()
        routes._clear_events()
        async with get_sessionmaker()() as s:
            row = await s.get(IntegrationEvent, key)
            if row:
                await s.delete(row)
            await s.execute(delete(FindingEvidence).where(FindingEvidence.finding_id == finding_id))
            await s.execute(delete(Finding).where(Finding.id == finding_id))
            await s.execute(delete(Organization).where(Organization.id == org))
            for nonce in nonces:
                await s.execute(delete(IntegrationNonce).where(IntegrationNonce.nonce == nonce))
            await s.commit()
