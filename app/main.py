"""ARVO — FastAPI + Jinja2. Deployment: 178.105.181.38:9777. AI interpreta. Python calcula."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings

logging.basicConfig(level=logging.INFO if not settings.debug else logging.DEBUG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ARVO starting — %s @ %s (Supabase Studio %s)", settings.app_name, settings.app_url, settings.supabase_studio_url)
    # DB init lazy — don't fail startup if Supabase still booting
    try:
        from app.database.engine import init_db
        await init_db()
        logger.info("DB init ok")
    except Exception as e:
        logger.warning("DB init deferred: %s", e)
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
)

# Static — served at /static
_static = Path(__file__).parent.parent / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

# CORS
_origins = ["*"]
if settings.cors_origins:
    _origins = [o.strip() for o in settings.cors_origins.split(",")]
elif not settings.debug:
    _origins = [settings.app_url.rstrip("/")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
)

# Security headers
@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.scheme == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


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
    # DB check
    try:
        from app.database.engine import check_db
        checks["db"] = "ok" if await check_db() else "down"
        if checks["db"] != "ok":
            ok = False
    except Exception as e:
        checks["db"] = f"error: {e}"
        ok = False
    # Redis optional
    try:
        import redis.asyncio as aioredis
        if settings.redis_url:
            r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
            await r.ping()
            await r.aclose()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "not_configured"
    except Exception as e:
        checks["redis"] = f"fallback: {e}"
    checks["llm_provider"] = settings.llm_provider
    checks["supabase_studio"] = settings.supabase_studio_url
    code = 200 if ok else 503
    return JSONResponse(status_code=code, content={"status": "ready" if ok else "not_ready", "checks": checks})


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "app": settings.app_name, "port": 9777, "studio": settings.supabase_studio_url}


def run():
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=9777, reload=True)
