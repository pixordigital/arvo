"""Web POST /findings publishes commitment.at_risk with API parity."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.services.financial import idempotency_key


@pytest.fixture
async def web_finding_org(monkeypatch, tmp_path):
    import app.database.engine as db
    import app.web.routes as web_routes
    from app.database.engine import get_sessionmaker, init_db
    from app.database.models import Organization

    monkeypatch.setattr(
        settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'web_finding.db'}"
    )
    db._engines.clear()
    db._sessions.clear()
    db._default_engine = None
    db._default_session = None

    org_id = f"webf-{uuid.uuid4().hex}"

    async def fake_require_org(request):
        return {"org_id": org_id, "user_id": "web-user", "role": "OWNER"}

    monkeypatch.setattr(web_routes, "_require_org", fake_require_org)
    await init_db()
    async with get_sessionmaker()() as s:
        s.add(Organization(id=org_id, name=f"Web {org_id}", slug=org_id))
        await s.commit()
    return org_id


def _post_finding(client, title):
    return client.post(
        "/findings",
        data={
            "title": title,
            "kind": "LEAKAGE",
            "severity": "HIGH",
            "exposure_amount": "1000",
            "description": "web form",
        },
    )


async def _load_finding(org_id, title):
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding

    async with get_sessionmaker()() as s:
        return (
            await s.scalars(
                select(Finding).where(Finding.org_id == org_id, Finding.title == title)
            )
        ).one_or_none()


async def test_web_finding_enqueues_commitment_at_risk_in_same_transaction(
    web_finding_org, monkeypatch
):
    from app.database.engine import get_sessionmaker
    from app.database.models import FinancialEvent, Finding
    from app.integrations.aios import publisher

    calls = []

    async def fake_enqueue(event_type, payload, **kwargs):
        session = kwargs["session"]
        finding = await session.get(Finding, payload["finding_id"])
        calls.append(
            {
                "event_type": event_type,
                "payload": dict(payload),
                "business_trace_id": kwargs["business_trace_id"],
                "idempotency_key": kwargs["idempotency_key"],
                "session": session,
                "in_transaction": session.in_transaction(),
                "finding_in_session": finding is not None,
                "financial_event_pending": any(
                    isinstance(obj, FinancialEvent) for obj in session.new
                ),
            }
        )
        return "outbox-id"

    monkeypatch.setattr(publisher, "enqueue_outbox", fake_enqueue)
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    title = f"Web finding {uuid.uuid4().hex}"
    client = TestClient(app, follow_redirects=False)

    r = _post_finding(client, title)
    assert r.status_code == 302 and r.headers["location"] == "/findings"

    assert len(calls) == 1
    call = calls[0]
    assert call["event_type"] == "commitment.at_risk"
    assert call["in_transaction"] is True
    assert call["finding_in_session"] is True
    assert call["financial_event_pending"] is True

    f = await _load_finding(web_finding_org, title)
    assert f is not None
    assert call["payload"] == {
        "org_id": web_finding_org,
        "finding_id": f.id,
        "kind": "LEAKAGE",
        "title": title,
        "severity": "HIGH",
        "exposure_amount": "1000.00",
        "currency": "BRL",
        "confidence": "MEDIUM",
    }
    assert call["business_trace_id"] == f.id
    assert call["idempotency_key"] == f.id

    async with get_sessionmaker()() as s:
        event = await s.scalars(
            select(FinancialEvent).where(
                FinancialEvent.idempotency_key == idempotency_key(web_finding_org, "finding", f.id)
            )
        )
        assert len(event.all()) == 1


async def test_web_finding_with_flag_off_does_not_enqueue(web_finding_org, monkeypatch):
    from app.integrations.aios import publisher

    calls = []

    async def fake_enqueue(*args, **kwargs):
        calls.append((args, kwargs))
        return "outbox-id"

    monkeypatch.setattr(publisher, "enqueue_outbox", fake_enqueue)
    monkeypatch.setattr(settings, "aios_integration_enabled", False)
    title = f"Web finding off {uuid.uuid4().hex}"
    client = TestClient(app, follow_redirects=False)

    r = _post_finding(client, title)
    assert r.status_code == 302 and r.headers["location"] == "/findings"
    assert calls == []
    assert await _load_finding(web_finding_org, title) is not None


async def test_web_finding_enqueue_failure_rolls_back(web_finding_org, monkeypatch):
    from app.integrations.aios import publisher

    async def failing_enqueue(*args, **kwargs):
        raise RuntimeError("enqueue-down")

    monkeypatch.setattr(publisher, "enqueue_outbox", failing_enqueue)
    monkeypatch.setattr(settings, "aios_integration_enabled", True)
    title = f"Web finding fail {uuid.uuid4().hex}"
    client = TestClient(app, follow_redirects=False)

    with pytest.raises(RuntimeError, match="enqueue-down"):
        _post_finding(client, title)

    assert await _load_finding(web_finding_org, title) is None
