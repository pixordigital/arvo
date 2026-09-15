"""ARVO Web — Jinja2 routes. Spec §231-242. Shared base.html, auth-aware nav, CSRF on POST."""

from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# Navigation spec §242
NAV = [
    ("Control Center", "/", "control-center"),
    ("Findings", "/findings", "findings"),
    ("Deals", "/deals", "deals"),
    ("Accounts", "/accounts", "accounts"),
    ("Financial Impact", "/financial-impact", "financial-impact"),
    ("Actions", "/actions", "actions"),
    ("Evidence", "/evidence", "evidence"),
    ("Integrations", "/integrations", "integrations"),
    ("Settings", "/settings", "settings"),
]


def _ctx(title: str, active: str) -> dict:
    from app.core.config import settings
    return {
        "title": title,
        "active": active,
        "nav": NAV,
        "app_name": settings.app_name,
        "studio_url": settings.supabase_studio_url,
        "app_url": settings.app_url,
    }


@router.get("/", response_class=HTMLResponse)
async def control_center(request: Request):
    return templates.TemplateResponse(request, "pages/control_center.html", _ctx("Control Center", "control-center"))


@router.get("/findings", response_class=HTMLResponse)
async def findings(request: Request):
    return templates.TemplateResponse(request, "pages/findings.html", _ctx("Findings", "findings"))


@router.get("/deals", response_class=HTMLResponse)
async def deals(request: Request):
    return templates.TemplateResponse(request, "pages/deals.html", _ctx("Deals", "deals"))


@router.get("/accounts", response_class=HTMLResponse)
async def accounts(request: Request):
    return templates.TemplateResponse(request, "pages/accounts.html", _ctx("Accounts", "accounts"))


@router.get("/financial-impact", response_class=HTMLResponse)
async def financial_impact(request: Request):
    return templates.TemplateResponse(request, "pages/financial_impact.html", _ctx("Financial Impact", "financial-impact"))


@router.get("/actions", response_class=HTMLResponse)
async def actions(request: Request):
    return templates.TemplateResponse(request, "pages/actions.html", _ctx("Actions", "actions"))


@router.get("/evidence", response_class=HTMLResponse)
async def evidence(request: Request):
    return templates.TemplateResponse(request, "pages/evidence.html", _ctx("Evidence", "evidence"))


@router.get("/integrations", response_class=HTMLResponse)
async def integrations(request: Request):
    return templates.TemplateResponse(request, "pages/integrations.html", _ctx("Integrations", "integrations"))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "pages/settings.html", _ctx("Settings", "settings"))
