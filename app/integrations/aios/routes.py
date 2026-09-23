"""Rotas de integração AIOS ↔ ARVO (Fase 1C/2 + payload + persist). HMAC + in-memory + DB."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from .auth import _forget_nonce, verify_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/aios/v1", tags=["aios-integration"])

# in-memory (fast) + DB persist (survive restart) — Fase 1-persist
_events: dict[str, tuple[float, dict]] = {}
_events_lock = threading.Lock()
_EVENTS_TTL = 86400
_PROCESSING_TTL = 60


async def _db_persist_nonce(nonce: str) -> bool | None:
    """Insert nonce; False means duplicate, None means persistence unavailable."""
    try:
        from sqlalchemy import delete

        from app.database.engine import get_sessionmaker
        from app.database.models import IntegrationNonce

        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=600)
        async with get_sessionmaker()() as s:
            await s.execute(delete(IntegrationNonce).where(IntegrationNonce.expires_at < datetime.now(timezone.utc).replace(tzinfo=None)))
            s.add(IntegrationNonce(nonce=nonce, peer="aios", expires_at=expires))
            await s.commit()
            return True
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg or "integrity" in msg:
            try:
                await s.rollback()  # type: ignore
            except Exception:
                pass
            return False
        logger.error("nonce persistence unavailable: %s", e)
        return None


async def _db_get_event(key: str) -> dict | None:
    try:
        from app.database.engine import get_sessionmaker
        from app.database.models import IntegrationEvent

        async with get_sessionmaker()() as s:
            obj = await s.get(IntegrationEvent, key)
            if obj and obj.expires_at > datetime.now(timezone.utc).replace(tzinfo=None):
                return obj.response
            if obj and obj.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
                await s.delete(obj)
                await s.commit()
    except Exception as e:
        logger.debug("event DB get fallback: %s", e)
    return None


async def _db_claim_event(key: str, type_: str, payload: dict) -> tuple[bool, dict | None]:
    from sqlalchemy.exc import IntegrityError

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationEvent

    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=_PROCESSING_TTL)
    async with get_sessionmaker()() as db:
        existing = await db.get(IntegrationEvent, key)
        if existing and existing.expires_at > datetime.now(timezone.utc).replace(tzinfo=None):
            return False, existing.response
        if existing:
            await db.delete(existing)
            await db.flush()
        db.add(
            IntegrationEvent(
                idempotency_key=key,
                peer="aios",
                type=type_,
                payload=payload,
                response={"status": "processing"},
                expires_at=expires,
            )
        )
        try:
            await db.commit()
            return True, None
        except IntegrityError:
            await db.rollback()
            existing = await db.get(IntegrationEvent, key)
            return False, existing.response if existing else None


async def _db_complete_event(key: str, response: dict) -> None:
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationEvent

    async with get_sessionmaker()() as db:
        row = await db.get(IntegrationEvent, key)
        if row:
            row.response = response
            row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=_EVENTS_TTL)
            await db.commit()


async def _db_release_event(key: str) -> None:
    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationEvent

    async with get_sessionmaker()() as db:
        row = await db.get(IntegrationEvent, key)
        if row and row.response.get("status") == "processing":
            await db.delete(row)
            await db.commit()


class AiosEvent(BaseModel):
    type: str = Field(..., max_length=64, pattern=r"^[a-z0-9_.-]+$")
    payload: dict = Field(default_factory=dict)
    occurred_at: str | None = None
    business_trace_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    event_version: str | None = Field(default="1", max_length=16)


class AgentExecutionRequest(BaseModel):
    finding_id: str | None = None
    opportunity_id: str | None = None
    action: str | None = None
    payload: dict = Field(default_factory=dict)
    business_trace_id: str | None = Field(default=None, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


def _get_idempotent(key: str) -> dict | None:
    now = time.time()
    with _events_lock:
        for k, (exp, _) in list(_events.items()):
            if exp < now:
                del _events[k]
        if key in _events:
            return _events[key][1]
    return None


def _store_idempotent(key: str, resp: dict) -> None:
    with _events_lock:
        _events[key] = (time.time() + _EVENTS_TTL, resp)


def _clear_events() -> None:
    with _events_lock:
        _events.clear()


def _require_enabled() -> None:
    if not settings.aios_integration_enabled:
        raise HTTPException(404, "AIOS integration disabled")


async def _require_auth(request: Request) -> None:
    _require_enabled()
    if not settings.aios_service_key or not settings.aios_service_key_id:
        raise HTTPException(503, "AIOS integration not configured")
    kid = request.headers.get("x-service-key-id")
    ts = request.headers.get("x-service-timestamp")
    nonce = request.headers.get("x-service-nonce")
    sig = request.headers.get("x-service-signature")
    if not all([kid, ts, nonce, sig]):
        raise HTTPException(401, "Missing service signature")
    if kid != settings.aios_service_key_id:
        raise HTTPException(401, "Unknown service key id")
    body = await request.body()
    if not verify_request(kid, ts, nonce, request.method, request.url.path, body or None, sig, settings.aios_service_key):
        raise HTTPException(401, "Invalid service signature")
    persisted = await _db_persist_nonce(nonce)
    if persisted is False:
        raise HTTPException(401, "Replay nonce (DB)")
    if persisted is None:
        _forget_nonce(nonce)
        raise HTTPException(503, "Replay protection unavailable")


@router.get("/health", dependencies=[Depends(_require_auth)])
async def health():
    return {"status": "ok", "service": "arvo", "peer": "aios"}


@router.post("/health", dependencies=[Depends(_require_auth)])
async def health_probe():
    return await health()


@router.post("/agent-executions", dependencies=[Depends(_require_auth)])
async def create_agent_execution(body: AgentExecutionRequest):
    """Persist and enqueue ARVO audit work for AIOS."""
    import uuid

    if not body.opportunity_id:
        raise HTTPException(400, "opportunity_id required")

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationEvent, Opportunity

    async with get_sessionmaker()() as session:
        opportunity = await session.get(Opportunity, body.opportunity_id)
    if not opportunity:
        raise HTTPException(404, "opportunity not found")

    exec_id = str(uuid.uuid4())
    trace_id = (
        body.business_trace_id
        or body.payload.get("business_trace_id")
        or str(uuid.uuid4())
    )
    response = {
        "id": exec_id,
        "status": "queued",
        "business_trace_id": trace_id,
    }
    payload = {
        "finding_id": body.finding_id,
        "opportunity_id": body.opportunity_id,
        "action": body.action,
        "payload": body.payload,
        "business_trace_id": trace_id,
    }
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=_EVENTS_TTL
    )
    try:
        async with get_sessionmaker()() as session:
            session.add(
                IntegrationEvent(
                    idempotency_key=exec_id,
                    peer="aios",
                    type="agent.execution",
                    payload=payload,
                    response=response,
                    expires_at=expires,
                )
            )
            await session.commit()
    except Exception as error:
        logger.error("agent-execution persistence failed: %s", error)
        raise HTTPException(503, "Execution persistence unavailable") from error

    from app.workers.runner import enqueue_audit

    job_id = await enqueue_audit(body.opportunity_id, opportunity.org_id)
    if job_id is None:
        async with get_sessionmaker()() as session:
            stored = await session.get(IntegrationEvent, exec_id)
            if stored:
                await session.delete(stored)
                await session.commit()
        raise HTTPException(503, "Audit queue unavailable")

    _store_idempotent(exec_id, response)
    return {**response, "event_version": "1"}


@router.get("/executions/{exec_id}", dependencies=[Depends(_require_auth)])
async def get_execution(exec_id: str):
    cached = _get_idempotent(exec_id)
    if cached is not None:
        return cached
    db_cached = await _db_get_event(exec_id)
    if db_cached is not None:
        _store_idempotent(exec_id, db_cached)
        return db_cached
    # also try direct IntegrationEvent lookup
    try:
        from app.database.engine import get_sessionmaker
        from app.database.models import IntegrationEvent

        async with get_sessionmaker()() as s:
            obj = await s.get(IntegrationEvent, exec_id)
            if obj:
                return obj.response
    except Exception as e:
        logger.debug("get_execution fallback: %s", e)
    raise HTTPException(404, "Execution not found")


async def _handle_agent_action_completed(payload: dict, trace_id: str | None) -> dict:
    """Phase 4 slice: agent.action.completed → associa evidence ao finding, recalcula mínimo."""
    finding_id = payload.get("finding_id") or payload.get("opportunity_id") or payload.get("id")
    if not finding_id:
        return {"handled": False, "reason": "no finding_id"}
    try:
        from app.database.engine import get_sessionmaker
        from app.database.models import Finding, FindingEvidence

        async with get_sessionmaker()() as s:
            finding = await s.get(Finding, str(finding_id))
            if not finding:
                return {"handled": False, "reason": "finding not found"}
            # Phase 5: persist trace in provenance
            if trace_id and isinstance(finding.provenance, dict):
                prov = dict(finding.provenance)
                prov["business_trace_id"] = trace_id
                finding.provenance = prov
            ev = FindingEvidence(finding_id=finding.id, kind="EVIDENCE", content={"business_trace_id": trace_id, "payload": payload, "source": "agent.action.completed"})
            s.add(ev)
            await s.commit()
            return {"handled": "agent.action.completed", "finding_id": str(finding_id), "business_trace_id": trace_id, "evidence_id": ev.id}
    except Exception:
        logger.exception("agent.action.completed persistence failed")
        raise


@router.post("/events", dependencies=[Depends(_require_auth)])
async def ingest_event(
    body: AiosEvent,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(400, "Invalid Idempotency-Key")
    cached = _get_idempotent(idempotency_key)
    if cached is not None:
        return {**cached, "deduplicated": True}
    db_cached = await _db_get_event(idempotency_key)
    if db_cached is not None:
        if db_cached.get("status") == "processing":
            raise HTTPException(503, "Event processing in progress")
        _store_idempotent(idempotency_key, db_cached)
        return {**db_cached, "deduplicated": True}
    trace_id = body.business_trace_id or body.payload.get("business_trace_id")
    event_payload = {**body.payload, "business_trace_id": trace_id} if trace_id else body.payload
    claimed, existing = await _db_claim_event(idempotency_key, body.type, event_payload)
    if not claimed:
        if existing is None:
            raise HTTPException(503, "Idempotency reservation unavailable")
        if existing.get("status") == "processing":
            raise HTTPException(503, "Event processing in progress")
        _store_idempotent(idempotency_key, existing)
        return {**existing, "deduplicated": True}
    side_effect_started = False
    try:
        extra_resp: dict = {}
        if body.type == "agent.action.completed":
            extra_resp = await _handle_agent_action_completed(body.payload, trace_id)
            side_effect_started = bool(extra_resp.get("handled"))
        resp = {
            "status": "processed",
            "idempotency_key": idempotency_key,
            "type": body.type,
            "business_trace_id": trace_id,
            "event_version": body.event_version or "1",
            "deduplicated": False,
            **extra_resp,
        }
        stripped = {k: v for k, v in resp.items() if k != "deduplicated"}
        await _db_complete_event(idempotency_key, stripped)
        _store_idempotent(idempotency_key, stripped)
        return resp
    except Exception:
        if not side_effect_started:
            await _db_release_event(idempotency_key)
        raise
