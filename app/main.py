"""ARVO — FastAPI + Jinja2. Deployment: 178.105.181.38:9777. AI interpreta. Python calcula."""

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.core.config import settings

logging.basicConfig(level=logging.INFO if not settings.debug else logging.DEBUG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ARVO starting — %s @ %s (Supabase Studio %s)", settings.app_name, settings.app_url, settings.supabase_studio_url)
    if not settings.debug and settings.jwt_secret.startswith("change-me"):
        raise RuntimeError("ARVO_JWT_SECRET must be set to a random value (openssl rand -hex 32)")
    yield
    logger.info("ARVO shutting down")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="ARVO — AI Revenue Value Orchestrator. Find the leak. Protect the value. Control the revenue.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    root_path=os.getenv("ARVO_ROOT_PATH", ""),
)

# Static — served at /static
_static = Path(__file__).parent.parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

# CORS
if settings.cors_origins:
    _origins = [o.strip() for o in settings.cors_origins.split(",")]
else:
    _origins = [settings.app_url.rstrip("/")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
)

_RATE: dict[str, list[float]] = {}
_REDIS = None


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown"))


async def _redis_client():
    """Lazy Redis client. None means Redis unusable — fall back to in-process counters."""
    global _REDIS
    if _REDIS is None:
        try:
            from redis.asyncio import from_url

            client = from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            await client.ping()
            _REDIS = client
        except Exception as e:
            logger.warning("Rate limit: Redis unavailable, using per-process counters (%s)", e)
            _REDIS = False
    return _REDIS or None


async def _hit_count(key: str, window: int) -> int:
    """Increment a 60s hit counter. Redis keeps it global across workers when reachable."""
    client = await _redis_client()
    if client is not None:
        try:
            n = await client.incr(key)
            if n == 1:
                await client.expire(key, window)
            return int(n)
        except Exception as e:
            logger.warning("Rate limit: Redis increment failed, using memory (%s)", e)
    import time as _time

    now = _time.time()
    hits = [t for t in _RATE.get(key, []) if now - t < window]
    hits.append(now)
    _RATE[key] = hits
    return len(hits)


@app.middleware("http")
async def abuse_guard(request: Request, call_next):
    """Rate-limit auth writes and reject cross-origin state changes (CSRF defence)."""

    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        allowed = {settings.app_url.rstrip("/")}
        if settings.cors_origins:
            allowed |= {o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()}
        if origin and origin.rstrip("/") not in allowed:
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected"})

        auth_write = request.url.path.startswith("/api/v1/auth/") or request.url.path in ("/login", "/register")
        sso_ticket = request.url.path == "/api/v1/integrations/aios/v1/sso-ticket"
        if auth_write or sso_ticket:
            key = f"arvo:ratelimit:{_client_ip(request)}:{request.url.path}"
            if await _hit_count(key, 60) > settings.rate_limit_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests; retry in a minute"},
                    headers={"Retry-After": "60"},
                )
    return await call_next(request)


# Security headers
@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
        "script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
        "font-src 'self'; connect-src 'self'"
    )
    if request.url.scheme == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.middleware("http")
async def root_path_rewrite(request: Request, call_next):
    prefix = request.scope.get("root_path", "")
    resp = await call_next(request)
    if not prefix:
        return resp
    location = resp.headers.get("location")
    if location and location.startswith("/") and not location.startswith(prefix + "/"):
        resp.headers["location"] = prefix + location
    if not resp.headers.get("content-type", "").startswith("text/html"):
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    body = re.sub(rb'(href|action|src)="/', lambda m: m.group(1) + b'="' + prefix.encode() + b'/', body)
    headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
    return Response(content=body, status_code=resp.status_code, headers=headers, media_type="text/html")


# API router
from app.api.router import api_router  # noqa: E402
app.include_router(api_router)

# Web (Jinja2) — must be after /api to avoid shadowing
from app.web.routes import router as web_router  # noqa: E402
app.include_router(web_router)


@app.get("/health/live")
async def health_live():
    return {"status": "live", "app": settings.app_name}


@app.get("/health/ready")
async def health_ready():
    checks: dict = {}
    ok = True
    if not settings.debug and settings.is_sqlite:
        checks["db"] = "down"
        ok = False
    else:
        try:
            from app.database.engine import check_db
            checks["db"] = "ok" if await check_db() else "down"
            if checks["db"] != "ok":
                ok = False
        except Exception:
            checks["db"] = "down"
            ok = False
    try:
        client = await _redis_client()
        if client is not None:
            await client.ping()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "down"
            ok = False
    except Exception:
        checks["redis"] = "down"
        ok = False
    code = 200 if ok else 503
    return JSONResponse(status_code=code, content={"status": "ready" if ok else "not_ready", "checks": checks})


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "app": settings.app_name, "port": 9777}


def run():
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=9777, reload=True)
