"""Canonical AIOS event envelope: defaults, validation, wire round-trip."""
import json

import pytest
from pydantic import ValidationError

from app.integrations.aios.events import AiosEvent


def test_envelope_defaults_and_backward_compatible_parse():
    env = AiosEvent(type="agent.action.completed", payload={"a": 1})
    assert env.event_version == "1"
    assert env.model_dump(exclude_none=True) == {
        "type": "agent.action.completed",
        "payload": {"a": 1},
        "event_version": "1",
    }
    # Older peers omit event_version entirely.
    assert AiosEvent(**{"type": "deal.created", "payload": {}}).event_version == "1"


def test_envelope_rejects_invalid_type_and_version():
    with pytest.raises(ValidationError):
        AiosEvent(type="BAD TYPE!", payload={})
    with pytest.raises(ValidationError):
        AiosEvent(type="deal.created", payload={}, event_version="x" * 17)
    with pytest.raises(ValidationError):
        AiosEvent(type="deal.created", payload={}, event_version="bad/version")


def test_envelope_rejects_oversized_payload():
    with pytest.raises(ValidationError):
        AiosEvent(type="deal.created", payload={f"k{i}": i for i in range(101)})


async def test_send_event_emits_canonical_envelope(monkeypatch):
    import httpx

    from app.core.config import settings
    from app.integrations.aios import client
    from app.integrations.aios.auth import _clear_nonces

    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok"}

    class FakeClient:
        async def request(self, method, path, **kw):
            captured["content"] = kw["content"]
            return FakeResp()

    async def fake_get_client():
        return FakeClient()

    monkeypatch.setattr(client, "_get_client", fake_get_client)
    monkeypatch.setattr(settings, "aios_base_url", "http://peer.test")
    monkeypatch.setattr(settings, "aios_service_key_id", "kid-env")
    monkeypatch.setattr(settings, "aios_service_key", "secret-env")
    _clear_nonces()

    await client.send_event("action.verified", {"action_id": "a-1"}, "idem-env", event_version="2")
    body = json.loads(captured["content"])
    assert body == {
        "type": "action.verified",
        "payload": {"action_id": "a-1"},
        "event_version": "2",
    }
    # Round-trip: peer-parsed body validates against the same model.
    assert AiosEvent(**body).event_version == "2"
    _clear_nonces()


async def test_enqueue_outbox_propagates_event_version():
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationOutbox
    from app.integrations.aios.publisher import enqueue_outbox

    async with get_sessionmaker()() as session:
        outbox_id = await enqueue_outbox(
            "action.verified",
            {"action_id": "a-1"},
            event_version="3",
            session=session,
        )
        await session.commit()
    try:
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationOutbox, outbox_id)
            assert row.payload == {"action_id": "a-1", "event_version": "3"}
            delivered = AiosEvent(
                type=row.event_type,
                payload={k: v for k, v in row.payload.items() if k != "event_version"},
                business_trace_id=row.business_trace_id,
                event_version=row.payload["event_version"],
            )
            assert delivered.event_version == "3"
    finally:
        async with get_sessionmaker()() as session:
            row = await session.get(IntegrationOutbox, outbox_id)
            if row:
                await session.delete(row)
                await session.commit()
