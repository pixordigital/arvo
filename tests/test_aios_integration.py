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
