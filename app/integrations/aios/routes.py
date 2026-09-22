"""Rotas de integração AIOS ↔ ARVO (Fase 1C/2 + Fase payload). Feature-gated + HMAC + idempotency."""

import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from .auth import verify_request

router = APIRouter(prefix="/integrations/aios/v1", tags=["aios-integration"])

# ponytail: in-memory idempotency, TTL 24h; upgrade para DB em Fase 1-persist
_events: dict[str, tuple[float, dict]] = {}
_events_lock = threading.Lock()
_EVENTS_TTL = 86400


class AiosEvent(BaseModel):
    type: str = Field(..., max_length=64, pattern=r"^[a-z0-9_.-]+$")
    payload: dict = Field(default_factory=dict)
    occurred_at: str | None = None


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


@router.get("/health", dependencies=[Depends(_require_auth)])
async def health():
    return {"status": "ok", "service": "arvo", "peer": "aios"}


@router.post("/health", dependencies=[Depends(_require_auth)])
async def health_probe():
    return await health()


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
    resp = {"status": "processed", "idempotency_key": idempotency_key, "type": body.type, "deduplicated": False}
    _store_idempotent(idempotency_key, {k: v for k, v in resp.items() if k != "deduplicated"})
    return resp
