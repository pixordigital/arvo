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


def test_events_idempotency():
    import json
    from fastapi.testclient import TestClient
    from app.core.config import settings
    from app.integrations.aios.routes import _clear_events
    from app.main import app

    c = TestClient(app)
    orig_enabled, orig_id, orig_key = settings.aios_integration_enabled, settings.aios_service_key_id, settings.aios_service_key
    try:
        settings.aios_integration_enabled = True
        settings.aios_service_key_id = "kid-1"
        settings.aios_service_key = "secret-1"
        _clear_nonces()
        _clear_events()
        path = "/api/v1/integrations/aios/v1/events"
        body = json.dumps({"type": "test.event", "payload": {"a": 1}}, separators=(",", ":")).encode()
        hdr = sign_request("POST", path, body, "kid-1", "secret-1")
        idem_key = uuid.uuid4().hex
        hdr["Idempotency-Key"] = idem_key
        hdr["Content-Type"] = "application/json"
        r = c.post(path, content=body, headers=hdr)
        assert r.status_code == 200 and r.json()["deduplicated"] is False
        hdr2 = sign_request("POST", path, body, "kid-1", "secret-1")
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
    settings.aios_base_url = "http://aios.test"
    settings.aios_service_key_id = "kid-1"
    settings.aios_service_key = "secret-1"
    settings.aios_integration_enabled = True

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
            hdr = sign_request("POST", "/api/v1/integrations/aios/v1/events", body, "kid-loop", "secret-loop")
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
        return FakeResp()

    real_headers = client._headers

    def spy_headers(method, path, body=None):
        seen["sign_path"] = path
        return real_headers(method, path, body)

    monkeypatch.setattr(client, "_request_with_retry", fake_request)
    monkeypatch.setattr(client, "_headers", spy_headers)
    await client.list_deals("org-1", limit=7)
    assert seen["path"] == "/api/integrations/arvo/v1/deals"
    assert seen["params"] == {"org_id": "org-1", "limit": 7}
    assert seen["sign_path"] == "/api/integrations/arvo/v1/deals"


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
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok"}

    async def fake_request(method, path, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["content"] = kw["content"]
        captured["headers"] = kw["headers_factory"]()
        return FakeResp()

    monkeypatch.setattr(client, "_request_with_retry", fake_request)
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
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok"}

    async def fake_request(method, path, **kw):
        captured["method"] = method
        captured["path"] = path
        captured["content"] = kw["content"]
        captured["headers"] = kw["headers_factory"]()
        return FakeResp()

    monkeypatch.setattr(client, "_request_with_retry", fake_request)
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
    )
    _clear_nonces()
