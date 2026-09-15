"""ARVO API — /api/v1/* . Programmatic access. Web routes render Jinja2."""

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")

# Health already at root; add alias
@api_router.get("/health")
async def api_health():
    return {"status": "ok", "version": "0.1.0"}

# Placeholder routers — filled in later phases
# from app.api import organizations, accounts, opportunities, findings, evidence, recommendations, actions, ledger, integrations, copilot, deal_audit
# api_router.include_router(organizations.router, prefix="/organizations", tags=["organizations"])
# api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
