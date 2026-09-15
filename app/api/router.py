"""ARVO API — /api/v1/* . Programmatic access. Web routes render Jinja2."""

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")

@api_router.get("/health")
async def api_health():
    return {"status": "ok", "version": "0.1.0"}

from app.api.auth import router as auth_router
api_router.include_router(auth_router)

# Placeholder routers — filled in later phases
# from app.api import organizations, accounts, opportunities, findings, evidence, recommendations, actions, ledger, integrations, copilot, deal_audit
# api_router.include_router(organizations.router, prefix="/organizations", tags=["organizations"])
# api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
