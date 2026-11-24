"""Focused reliability check: failed immediate flush stays visible and pending."""

import logging
import sys
import types

import pytest

from app.integrations.aios import publisher
from app.integrations.aios.publisher import enqueue_outbox, schedule_flush


class FakeSession:
    def __init__(self, existing=None):
        self.rows = []
        self.sync_session = None
        self.existing = existing or {}
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        self.flushes += 1

    async def scalar(self, stmt):
        key = stmt.compile().params.get("idempotency_key_1")
        for row in self.rows:
            if row.idempotency_key == key:
                return row
        return self.existing.get(key)


class FakeSessionMaker:
    def __init__(self, session):
        self.session = session
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.session


@pytest.fixture
def listen_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        publisher.event, "listen", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    return calls


class BrokenPool:
    def __init__(self):
        self.enqueued = []
        self.closed = False

    async def enqueue_job(self, name, outbox_id):
        self.enqueued.append((name, outbox_id))
        raise RuntimeError("redis down")

    async def close(self):
        self.closed = True


async def test_schedule_flush_warns_when_enqueue_fails_and_row_stays_pending(
    monkeypatch, caplog
):
    monkeypatch.setenv("ARVO_USE_ARQ_WORKER", "true")
    pool = BrokenPool()

    async def fake_create_pool(_settings):
        return pool

    fake_arq = types.ModuleType("arq")
    fake_arq.create_pool = fake_create_pool
    fake_runner = types.ModuleType("app.workers.runner")
    fake_runner._redis_settings = lambda: object()
    monkeypatch.setitem(sys.modules, "arq", fake_arq)
    monkeypatch.setitem(sys.modules, "app.workers.runner", fake_runner)

    session = FakeSession()
    outbox_id = await enqueue_outbox("test.event", {"n": 1}, session=session)
    row = session.rows[0]
    assert row.status == "pending"

    with caplog.at_level(logging.WARNING, logger=publisher.logger.name):
        result = await schedule_flush(outbox_id)

    assert result is None
    assert row.status == "pending"
    assert pool.enqueued == [("integration_outbox_flush_job", outbox_id)]
    assert pool.closed

    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and record.name == publisher.logger.name
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert outbox_id[:8] in message
    assert "immediate flush unavailable" in message
    assert (
        "fallback requires arq worker cron OR external scheduler/manual flush; "
        "row remains pending"
    ) in message


async def test_duplicate_key_session_bound_returns_existing_without_second_insert(
    listen_calls,
):
    session = FakeSession()
    session.sync_session = object()

    first_id = await enqueue_outbox(
        "test.event", {"n": 1}, idempotency_key="dup", session=session
    )
    second_id = await enqueue_outbox(
        "test.event", {"n": 2}, idempotency_key="dup", session=session
    )

    assert second_id == first_id
    assert len(session.rows) == 1
    assert session.flushes == 1
    assert len(listen_calls) == 1


async def test_duplicate_key_self_managed_session_returns_existing(monkeypatch):
    existing = types.SimpleNamespace(id="existing-outbox-id")
    session = FakeSession(existing={"dup-self": existing})
    maker = FakeSessionMaker(session)
    monkeypatch.setattr(publisher, "get_sessionmaker", lambda: maker)
    scheduled = []

    async def forbidden_schedule(outbox_id):
        scheduled.append(outbox_id)

    monkeypatch.setattr(publisher, "schedule_flush", forbidden_schedule)

    result = await enqueue_outbox(
        "test.event", {"n": 1}, idempotency_key="dup-self"
    )

    assert result == existing.id
    assert session.rows == []
    assert session.flushes == 0
    assert maker.calls == 1
    assert scheduled == []


async def test_different_keys_still_insert(listen_calls):
    session = FakeSession(existing={"key-one": types.SimpleNamespace(id="existing-one")})
    session.sync_session = object()

    first_id = await enqueue_outbox(
        "test.event", {"n": 1}, idempotency_key="key-one", session=session
    )
    second_id = await enqueue_outbox(
        "test.event", {"n": 2}, idempotency_key="key-two", session=session
    )

    assert first_id == "existing-one"
    assert second_id != first_id
    assert [row.idempotency_key for row in session.rows] == ["key-two"]
    assert session.flushes == 1
    assert len(listen_calls) == 1


async def test_auto_generated_key_still_inserts(listen_calls):
    session = FakeSession()
    session.sync_session = object()

    outbox_id = await enqueue_outbox("test.event", {"n": 1}, session=session)

    assert len(session.rows) == 1
    assert session.rows[0].id == outbox_id
    assert session.rows[0].idempotency_key == outbox_id
    assert session.flushes == 1
    assert len(listen_calls) == 1
