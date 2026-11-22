"""Rotas de integração AIOS ↔ ARVO (Fase 1C/2 + payload + persist). HMAC + in-memory + DB."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from . import auth as _auth
from .auth import _forget_nonce, verify_request
from .events import AiosEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/aios/v1", tags=["aios-integration"])

# Cache-only; IntegrationEvent/IntegrationNonce DB is authority for cross-replica replay.
_events: dict[str, tuple[float, dict]] = {}
_events_lock = threading.Lock()
_EVENTS_TTL = 86400
_PROCESSING_TTL = 60
_MAX_EVENTS = 1000
_MAX_NONCES = 1000
_MAX_NONCE_LEN = 64
_ALLOWED_EVENT_TYPES = frozenset({"agent.action.completed", "deal.created", "deal.stage_changed"})


def _prune_nonce_cache() -> None:
    """Bound auth's in-memory replay cache; auth.py self-expires but has no size cap."""
    with _auth._lock:
        while len(_auth._seen_nonces) > _MAX_NONCES:
            _auth._seen_nonces.pop(next(iter(_auth._seen_nonces)))


def _allowed_aios_orgs() -> set[str]:
    return {v.strip() for v in (settings.aios_allowed_orgs or "").split(",") if v.strip()}


def _org_allowed(org, peer_org) -> bool:
    """Allowlist matches resolved org id/slug or the raw signed org value."""
    allowed = _allowed_aios_orgs()
    return not allowed or bool({str(peer_org), org.id, org.slug} & allowed)


async def cleanup_expired_nonces() -> int:
    """Cron-callable GC for expired nonces (kept off the per-request path)."""
    from sqlalchemy import delete

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationNonce

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
    async with get_sessionmaker()() as s:
        result = await s.execute(delete(IntegrationNonce).where(IntegrationNonce.expires_at < cutoff))
        await s.commit()
        return result.rowcount or 0


async def _db_persist_nonce(nonce: str) -> bool | None:
    """Insert nonce; False means duplicate, None means persistence unavailable."""
    try:
        from app.database.engine import get_sessionmaker
        from app.database.models import IntegrationNonce

        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=600)
        async with get_sessionmaker()() as s:
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


class AgentExecutionRequest(BaseModel):
    finding_id: str | None = None
    opportunity_id: str | None = None
    action: str | None = None
    org_id: str | None = None
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
        now = time.time()
        for k, (exp, _) in list(_events.items()):
            if exp < now:
                del _events[k]
        while len(_events) >= _MAX_EVENTS:
            _events.pop(next(iter(_events)))
        _events[key] = (now + _EVENTS_TTL, resp)


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
    if len(nonce) > _MAX_NONCE_LEN:
        raise HTTPException(401, "Invalid nonce")
    if kid != settings.aios_service_key_id:
        raise HTTPException(401, "Unknown service key id")
    body = await request.body()
    _prune_nonce_cache()
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

    from sqlalchemy import select

    from app.database.engine import get_sessionmaker
    from app.database.models import IntegrationEvent, Opportunity, Organization

    peer_org = body.org_id or body.payload.get("org_id")
    if not peer_org:
        raise HTTPException(400, "org_id required for agent execution")
    async with get_sessionmaker()() as session:
        opportunity = await session.get(Opportunity, body.opportunity_id)
        if not opportunity:
            raise HTTPException(404, "opportunity not found")
        org = await session.get(Organization, str(peer_org))
        if org is None:
            org = (await session.execute(select(Organization).where(Organization.slug == str(peer_org)))).scalar_one_or_none()
        if org is None:
            raise HTTPException(404, "org not found")
        if opportunity.org_id != org.id:
            raise HTTPException(403, "opportunity org mismatch")
        if not _org_allowed(org, peer_org):
            raise HTTPException(403, "AIOS org not allowed")

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

    response["job_id"] = job_id
    async with get_sessionmaker()() as session:
        stored = await session.get(IntegrationEvent, exec_id)
        if stored:
            stored.response = response
            await session.commit()

    _store_idempotent(exec_id, response)
    return {**response, "event_version": "1"}


@router.get("/executions/{exec_id}", dependencies=[Depends(_require_auth)])
async def get_execution(exec_id: str):
    cached = _get_idempotent(exec_id)
    if cached is None:
        cached = await _db_get_event(exec_id)
        if cached is not None:
            _store_idempotent(exec_id, cached)
    if cached is None:
        raise HTTPException(404, "Execution not found")

    job_id = cached.get("job_id")
    if not job_id:
        return cached

    from app.database.engine import get_sessionmaker
    from app.database.models import DealAuditJob

    async with get_sessionmaker()() as session:
        job = await session.get(DealAuditJob, job_id)
        if job is None:
            return cached
        status = {
            "QUEUED": "queued",
            "RUNNING": "running",
            "DONE": "completed",
            "FAILED": "failed",
        }.get(job.status, cached.get("status", "queued"))
        return {**cached, "status": status, "result": job.result, "error": job.error}


async def _handle_agent_action_completed(payload: dict, trace_id: str | None) -> dict:
    """Phase 4 slice: agent.action.completed → associa evidence ao finding, recalcula mínimo."""
    finding_id = payload.get("finding_id") or payload.get("opportunity_id") or payload.get("id")
    if not finding_id:
        return {"handled": False, "reason": "no finding_id", "recalculated": False}
    try:
        from sqlalchemy import select

        from app.database.engine import get_sessionmaker
        from app.database.models import Finding, FindingEvidence, FinancialEvent, FinancialLedger
        from app.services.financial import D, idempotency_key, money

        amount_field = next(
            (f for f in ("verified_amount", "recovery_amount", "exposure_amount") if f in payload),
            None,
        )
        amount = None
        if amount_field is not None and isinstance(payload[amount_field], str):
            try:
                parsed = D(payload[amount_field])
                if parsed.is_finite() and parsed > 0:
                    amount = money(parsed)
            except Exception:
                amount = None

        async with get_sessionmaker()() as s:
            finding = await s.get(Finding, str(finding_id))
            if not finding:
                return {"handled": False, "reason": "finding not found", "recalculated": False}
            if str(payload.get("org_id") or "") != str(finding.org_id):
                raise HTTPException(403, "org_id mismatch for agent.action.completed")
            result = {
                "handled": "agent.action.completed",
                "finding_id": str(finding_id),
                "business_trace_id": trace_id,
                "recalculated": amount is not None,
            }
            prov = dict(finding.provenance) if isinstance(finding.provenance, dict) else {}
            if trace_id:
                prov["business_trace_id"] = trace_id
            if amount is not None:
                if amount_field == "exposure_amount":
                    finding.exposure_amount = amount
                else:
                    key = idempotency_key(finding.org_id, "aios_action_completed", finding.id)[:100]
                    existing = (
                        await s.execute(
                            select(FinancialEvent).where(
                                FinancialEvent.org_id == finding.org_id,
                                FinancialEvent.idempotency_key == key,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        fe = FinancialEvent(
                            org_id=finding.org_id,
                            kind="RECOVERED",
                            amount=amount,
                            currency="BRL",
                            idempotency_key=key,
                            provenance={"source": "aios", "event": "agent.action.completed", "business_trace_id": trace_id},
                        )
                        s.add(fe)
                        await s.flush()
                        s.add(
                            FinancialLedger(
                                org_id=finding.org_id,
                                event_id=fe.id,
                                entry_type="VERIFIED",
                                amount=amount,
                                currency="BRL",
                                description=f"AIOS recalculation for finding {finding.id}",
                            )
                        )
                prov[amount_field] = amount
                result[amount_field] = amount
            if trace_id or amount is not None:
                finding.provenance = prov
            ev = FindingEvidence(finding_id=finding.id, kind="EVIDENCE", content={"business_trace_id": trace_id, "payload": payload, "source": "agent.action.completed"})
            s.add(ev)
            await s.commit()
            result["evidence_id"] = ev.id
            return result
    except HTTPException:
        raise
    except Exception:
        logger.exception("agent.action.completed persistence failed")
        raise


async def _handle_deal_event(payload: dict, trace_id: str | None, event_type: str) -> dict:
    """Deterministic deal risk rule (Python/Decimal, no LLM): deal value = exposure."""
    from sqlalchemy import select

    from app.database.engine import get_sessionmaker
    from app.database.models import Finding, FindingEvidence, FinancialEvent, Organization
    from app.services.deal_audit import materiality
    from app.services.financial import D, money

    peer_org = payload.get("org_id")
    deal_id = str(payload.get("deal_id") or payload.get("id") or "")
    if not peer_org or not deal_id:
        raise HTTPException(400, "org_id and deal_id required for deal events")
    rule = "deal_created" if event_type == "deal.created" else "deal_stage_changed"
    stage = str(payload.get("new_stage") or payload.get("stage") or "")
    key = f"aios:deal:{deal_id[:40]}:{rule}:{stage[:24]}"[:100]
    prov = {
        "source": "aios",
        "source_org_id": str(peer_org),
        "source_deal_id": deal_id,
        "business_trace_id": trace_id,
        "rule": rule,
        "stage": stage,
    }

    async with get_sessionmaker()() as s:
        org = await s.get(Organization, str(peer_org))
        if org is None:
            org = (await s.execute(select(Organization).where(Organization.slug == str(peer_org)))).scalar_one_or_none()
        if org is None:
            raise HTTPException(403, "Unknown AIOS org (not provisioned in ARVO)")
        if not _org_allowed(org, peer_org):
            raise HTTPException(403, "AIOS org not allowed")
        existing = (
            await s.execute(select(Finding).where(Finding.org_id == org.id, Finding.idempotency_key == key))
        ).scalar_one_or_none()
        if existing is not None:
            return {
                "handled": rule,
                "finding_id": existing.id,
                "exposure_amount": existing.exposure_amount,
                "finding_deduplicated": True,
                "business_trace_id": trace_id,
            }
        amount = D(payload.get("value") or payload.get("amount") or "0")
        if amount < 0:
            amount = D("0")
        exposure = money(amount)
        severity, _bucket = materiality(exposure)
        finding = Finding(
            org_id=org.id,
            kind="DEAL_RISK",
            status="DETECTED",
            severity=severity,
            title=f"AIOS deal {deal_id[:64]} ({rule})",
            description=f"Deterministic {rule} rule: stage={stage[:64]}, value={exposure}. No LLM.",
            exposure_amount=exposure,
            currency="BRL",
            confidence=severity,
            provenance=prov,
            idempotency_key=key,
            evidence_pack={"source": "aios", "rule": rule, "stage": stage, "payload": payload},
        )
        s.add(finding)
        await s.flush()
        s.add(
            FindingEvidence(
                finding_id=finding.id,
                kind="EVIDENCE",
                content={"source": "aios", "rule": rule, "stage": stage, "payload": payload, "business_trace_id": trace_id},
            )
        )
        fe = FinancialEvent(
            org_id=org.id,
            kind="RISK_EXPOSURE",
            amount=exposure,
            currency="BRL",
            idempotency_key=key,
            attribution_level=2,
            confidence=severity,
            provenance=prov,
        )
        s.add(fe)
        await s.commit()
        return {
            "handled": rule,
            "finding_id": finding.id,
            "financial_event_id": fe.id,
            "exposure_amount": exposure,
            "finding_deduplicated": False,
            "business_trace_id": trace_id,
        }


class SsoTicketIn(BaseModel):
    ticket: str | None = None
    nonce: str | None = None
    key_id: str | None = None


@router.post("/sso-ticket")
async def consume_sso_ticket(request: Request, body: SsoTicketIn | None = None):
    """Consume a short-lived AIOS-signed ticket; role/identity come from local membership."""
    import jwt
    from sqlalchemy import select

    from app.core.security import create_token
    from app.database.engine import get_sessionmaker
    from app.database.models import ALL_ROLES, Membership, Organization, User

    _require_enabled()
    if not settings.aios_service_key:
        raise HTTPException(503, "AIOS integration not configured")
    ticket = (body.ticket if body else None) or request.headers.get("x-sso-ticket")
    if not ticket:
        raise HTTPException(400, "ticket required")
    try:
        claims = jwt.decode(
            ticket, settings.aios_service_key, algorithms=["HS256"], options={"verify_aud": False}
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid SSO ticket")
    if claims.get("iss") != "aios" or claims.get("aud") != "arvo":
        raise HTTPException(401, "Invalid SSO ticket issuer/audience")
    if "exp" not in claims:
        raise HTTPException(401, "SSO ticket missing expiration")
    kid = (body.key_id if body else None) or request.headers.get("x-service-key-id") or claims.get("key_id") or claims.get("kid")
    if not kid or kid != settings.aios_service_key_id:
        raise HTTPException(401, "Unknown service key id")
    nonce = (body.nonce if body else None) or claims.get("jti") or request.headers.get("x-service-nonce")
    if not nonce or len(str(nonce)) > _MAX_NONCE_LEN:
        raise HTTPException(401, "SSO ticket nonce required")
    persisted = await _db_persist_nonce(str(nonce))
    if persisted is False:
        raise HTTPException(401, "Replay nonce (DB)")
    if persisted is None:
        raise HTTPException(503, "Replay protection unavailable")
    sub, peer_org, ticket_role = claims.get("sub"), claims.get("org_id"), claims.get("role")
    if not sub or not peer_org:
        raise HTTPException(400, "SSO ticket missing identity claims")
    if ticket_role is not None and ticket_role not in ALL_ROLES:
        raise HTTPException(400, "Invalid role")
    async with get_sessionmaker()() as s:
        org = await s.get(Organization, str(peer_org))
        if org is None:
            org = (await s.execute(select(Organization).where(Organization.slug == str(peer_org)))).scalar_one_or_none()
        if org is None:
            raise HTTPException(403, "Unknown AIOS org (not provisioned in ARVO)")
        if not _org_allowed(org, peer_org):
            raise HTTPException(403, "AIOS org not allowed")
        # sub is identity. email may only discover the same subject, never rebind it.
        user = await s.get(User, str(sub))
        if user is None and claims.get("email"):
            discovered = (await s.execute(select(User).where(User.email == str(claims["email"]).strip().lower()))).scalar_one_or_none()
            if discovered is not None and discovered.id != str(sub):
                raise HTTPException(401, "SSO identity mismatch")
            user = discovered
        if user is None or not user.is_active:
            raise HTTPException(401, "Unknown or inactive SSO subject")
        membership = (
            await s.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.org_id == org.id,
                    Membership.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(403, "No active ARVO membership for SSO subject")
        role = membership.role
        if ticket_role is not None and ticket_role != role:
            raise HTTPException(403, "SSO role mismatch")
        user_id, org_id = user.id, org.id
    token = create_token(user_id, org_id, role)
    return {"access_token": token, "token_type": "bearer", "org_id": org_id, "user_id": user_id, "role": role}


@router.post("/outbox/flush", dependencies=[Depends(_require_auth)])
async def flush_outbox_endpoint(outbox_id: str | None = None):
    """HTTP trigger for outbox flush (replaces reliance on Coolify cron)."""
    from app.integrations.aios.publisher import flush_outbox, integration_outbox_flush

    if outbox_id:
        return await integration_outbox_flush(None, outbox_id=outbox_id, batch=20)
    return await flush_outbox(batch=20)


@router.post("/events", dependencies=[Depends(_require_auth)])
async def ingest_event(
    body: AiosEvent,
    idempotency_key: str = Header(alias="Idempotency-Key"),
):
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(400, "Invalid Idempotency-Key")
    if body.type not in _ALLOWED_EVENT_TYPES:
        raise HTTPException(400, f"Unsupported event type: {body.type}")
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
        elif body.type in ("deal.created", "deal.stage_changed"):
            extra_resp = await _handle_deal_event(body.payload, trace_id, body.type)
            side_effect_started = True
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
