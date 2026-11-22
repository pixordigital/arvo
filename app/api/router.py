"""ARVO API — /api/v1/* . Programmatic access. Web routes render Jinja2."""

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")

from app.api.auth import router as auth_router
from app.api.accounts import router as accounts_router
from app.api.opportunities import router as opps_router
from app.api.ingest import router as ingest_router
from app.api.financial import router as financial_router
from app.api.deal_audit import router as audit_router
from app.api.integrations import router as integ_router
from app.api.actions import router as actions_router
from app.api.gdpr import router as gdpr_router
api_router.include_router(auth_router)
api_router.include_router(accounts_router)
api_router.include_router(opps_router)
api_router.include_router(ingest_router)
api_router.include_router(financial_router, prefix="")
api_router.include_router(audit_router)
api_router.include_router(integ_router)
api_router.include_router(actions_router, prefix="")
api_router.include_router(gdpr_router)
from app.integrations.aios.routes import router as aios_router
api_router.include_router(aios_router)
