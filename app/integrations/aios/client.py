"""Cliente HTTP para o peer AIOS (Fase 1D). Assina requests via auth.sign_request."""

import httpx

from app.core.config import settings
from .auth import sign_request

PATH_PREFIX = "/api/integrations/arvo/v1"


def _headers(method: str, path: str, body: bytes | None = None) -> dict[str, str]:
    return sign_request(
        method, path, body, settings.aios_service_key_id, settings.aios_service_key
    )


async def ping() -> dict:
    """GET /health do peer. Usa http:// pois https 503 conhecido nos hosts sslip (GAPS §6)."""
    path = f"{PATH_PREFIX}/health"
    async with httpx.AsyncClient(base_url=settings.aios_base_url) as c:
        r = await c.get(path, headers=_headers("GET", path))
        r.raise_for_status()
        return r.json()
