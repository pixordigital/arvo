"""Cliente HTTP para o peer AIOS.

HMAC + timeout + backoff.
"""

import asyncio
import json
import uuid

import httpx

from app.core.config import settings

from .auth import sign_request
from .events import AiosEvent

PATH_PREFIX = "/api/integrations/arvo/v1"
_TIMEOUT = 5.0
_RETRIES = 3

_CLIENT: httpx.AsyncClient | None = None
_CLIENT_BASE: str | None = None


def _headers(method: str, path: str, body: bytes | None = None) -> dict[str, str]:
    return sign_request(
        method, path, body, settings.aios_service_key_id, settings.aios_service_key
    )


async def _get_client() -> httpx.AsyncClient:
    global _CLIENT, _CLIENT_BASE
    base = settings.aios_base_url
    if _CLIENT is None or _CLIENT_BASE != base:
        old = _CLIENT
        _CLIENT = httpx.AsyncClient(base_url=base, timeout=_TIMEOUT)
        _CLIENT_BASE = base
        if old is not None:
            await old.aclose()
    return _CLIENT


async def _request_with_retry(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
    **kw,
) -> httpx.Response:
    if not settings.aios_base_url:
        raise RuntimeError("AIOS integration not configured (aios_base_url)")
    c = await _get_client()
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            headers = _headers(method, path, body)
            headers.update(extra_headers or {})
            request_kw = dict(kw)
            request_kw["headers"] = headers
            if body is not None:
                request_kw["content"] = body
            r = await c.request(method, path, **request_kw)
            if r.status_code >= 500 or r.status_code == 429:
                raise httpx.HTTPStatusError(
                    f"retryable {r.status_code}", request=r.request, response=r
                )
            return r
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt < _RETRIES - 1:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            raise
    raise last_exc  # type: ignore


async def ping() -> dict:
    """GET /health do peer. Usa http:// pois sslip https 503 (GAPS §6). Retry 3×."""
    path = f"{PATH_PREFIX}/health"
    r = await _request_with_retry("GET", path)
    r.raise_for_status()
    return r.json()


async def send_event(
    event_type: str,
    payload: dict,
    idempotency_key: str | None = None,
    business_trace_id: str | None = None,
    event_version: str = "1",
) -> dict:
    envelope = AiosEvent(
        type=event_type,
        payload={k: v for k, v in payload.items() if k != "event_version"},
        business_trace_id=business_trace_id,
        event_version=payload.get("event_version") or event_version,
    )
    body = json.dumps(envelope.model_dump(exclude_none=True), separators=(",", ":")).encode()
    path = f"{PATH_PREFIX}/events"
    key = idempotency_key or str(uuid.uuid4())

    r = await _request_with_retry(
        "POST", path,
        body=body,
        extra_headers={"Idempotency-Key": key, "Content-Type": "application/json"},
    )
    r.raise_for_status()
    return r.json()


post_event = send_event


async def list_deals(org_id: str, limit: int = 100) -> dict:
    """GET /deals from peer AIOS (CRM single source of truth)."""
    if not org_id:
        return {"org_id": None, "deals": [], "count": 0}
    path = f"{PATH_PREFIX}/deals"
    r = await _request_with_retry(
        "GET", path, params={"org_id": org_id, "limit": int(limit)}
    )
    r.raise_for_status()
    return r.json()
