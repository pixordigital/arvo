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


def _ctx(title: str, active: str, extra: dict | None = None) -> dict:
    from app.core.config import settings
    base = {
        "title": title,
        "active": active,
        "nav": NAV,
        "app_name": settings.app_name,
        "studio_url": settings.supabase_studio_url,
        "app_url": settings.app_url,
    }
    if extra: base.update(extra)
    return base

async def _demo_org_id():
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Organization
    async with get_sessionmaker()() as s:
        org = (await s.execute(select(Organization).where(Organization.slug=="arvo-demo"))).scalar_one_or_none()
        return org.id if org else None

@router.get("/", response_class=HTMLResponse)
async def control_center(request: Request):
    # Summary for demo org
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import Account, Opportunity, Finding, FinancialLedger
    org_id = await _demo_org_id()
    stats={"accounts":0,"opps":0,"findings":0,"exposure":"0.00","verified":"0.00"}
    if org_id:
        async with get_sessionmaker()() as s:
            stats["accounts"]=(await s.execute(select(func.count(Account.id)).where(Account.org_id==org_id))).scalar()
            stats["opps"]=(await s.execute(select(func.count(Opportunity.id)).where(Opportunity.org_id==org_id))).scalar()
            stats["findings"]=(await s.execute(select(func.count(Finding.id)).where(Finding.org_id==org_id))).scalar()
            ex=(await s.execute(select(func.coalesce(func.sum(Finding.exposure_amount),0)).where(Finding.org_id==org_id))).scalar()
            stats["exposure"]=f"{float(ex or 0):.2f}"
            ver=(await s.execute(select(func.coalesce(func.sum(FinancialLedger.amount),0)).where(FinancialLedger.org_id==org_id, FinancialLedger.entry_type=="VERIFIED"))).scalar()
            stats["verified"]=f"{float(ver or 0):.2f}"
    return templates.TemplateResponse(request, "pages/control_center.html", _ctx("Control Center", "control-center", {"stats": stats}))


@router.get("/findings", response_class=HTMLResponse)
async def findings(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding
    org_id = await _demo_org_id()
    rows=[]
    if org_id:
        async with get_sessionmaker()() as s:
            rows=(await s.execute(select(Finding).where(Finding.org_id==org_id).order_by(Finding.created_at.desc()))).scalars().all()
            rows=[{"id":r.id,"title":r.title,"kind":r.kind,"status":r.status,"severity":r.severity,"exposure":r.exposure_amount,"currency":r.currency} for r in rows]
    return templates.TemplateResponse(request, "pages/findings.html", _ctx("Findings", "findings", {"findings": rows}))


@router.get("/deals", response_class=HTMLResponse)
async def deals(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Opportunity
    org_id = await _demo_org_id()
    rows=[]
    if org_id:
        async with get_sessionmaker()() as s:
            rows=(await s.execute(select(Opportunity).where(Opportunity.org_id==org_id).order_by(Opportunity.created_at.desc()))).scalars().all()
            rows=[{"id":r.id,"name":r.name,"stage":r.stage,"amount":r.amount,"currency":r.currency} for r in rows]
    return templates.TemplateResponse(request, "pages/deals.html", _ctx("Deals", "deals", {"deals": rows}))


@router.get("/accounts", response_class=HTMLResponse)
async def accounts(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Account
    org_id = await _demo_org_id()
    rows=[]
    if org_id:
        async with get_sessionmaker()() as s:
            rows=(await s.execute(select(Account).where(Account.org_id==org_id).order_by(Account.created_at.desc()))).scalars().all()
            rows=[{"id":r.id,"name":r.name,"domain":r.domain,"type":r.type} for r in rows]
    return templates.TemplateResponse(request, "pages/accounts.html", _ctx("Accounts", "accounts", {"accounts": rows}))


@router.get("/financial-impact", response_class=HTMLResponse)
async def financial_impact(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding, FinancialLedger
    org_id = await _demo_org_id()
    data={"exposure":"0.00","verified":"0.00","count":0}
    if org_id:
        async with get_sessionmaker()() as s:
            data["count"]=(await s.execute(select(func.count(Finding.id)).where(Finding.org_id==org_id))).scalar()
            data["exposure"]=f"{float((await s.execute(select(func.coalesce(func.sum(Finding.exposure_amount),0)).where(Finding.org_id==org_id))).scalar() or 0):.2f}"
            data["verified"]=f"{float((await s.execute(select(func.coalesce(func.sum(FinancialLedger.amount),0)).where(FinancialLedger.org_id==org_id, FinancialLedger.entry_type=='VERIFIED'))).scalar() or 0):.2f}"
            data["debit"]=f"{float((await s.execute(select(func.coalesce(func.sum(FinancialLedger.amount),0)).where(FinancialLedger.org_id==org_id, FinancialLedger.entry_type=='DEBIT'))).scalar() or 0):.2f}"
    return templates.TemplateResponse(request, "pages/financial_impact.html", _ctx("Financial Impact", "financial-impact", {"impact": data}))


@router.get("/actions", response_class=HTMLResponse)
async def actions(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Action
    org_id = await _demo_org_id()
    rows=[]
    if org_id:
        async with get_sessionmaker()() as s:
            rows=(await s.execute(select(Action).where(Action.org_id==org_id).order_by(Action.created_at.desc()))).scalars().all()
            rows=[{"id":r.id,"title":r.title,"status":r.status,"verified_amount":r.verified_amount} for r in rows]
    return templates.TemplateResponse(request, "pages/actions.html", _ctx("Actions", "actions", {"actions": rows}))


@router.get("/evidence", response_class=HTMLResponse)
async def evidence(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding
    org_id = await _demo_org_id()
    rows=[]
    if org_id:
        async with get_sessionmaker()() as s:
            rows=(await s.execute(select(Finding).where(Finding.org_id==org_id).order_by(Finding.created_at.desc()).limit(10))).scalars().all()
            rows=[{"id":r.id,"title":r.title,"pack":r.evidence_pack} for r in rows]
    return templates.TemplateResponse(request, "pages/evidence.html", _ctx("Evidence", "evidence", {"evidences": rows}))


@router.get("/integrations", response_class=HTMLResponse)
async def integrations(request: Request):
    from app.integrations.crm.base import ADAPTERS
    items=[{"source":k,"status":"stub"} for k in ADAPTERS.keys()] + [{"source":"csv","status":"ready"},{"source":"upload","status":"ready"}]
    return templates.TemplateResponse(request, "pages/integrations.html", _ctx("Integrations", "integrations", {"items": items}))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "pages/settings.html", _ctx("Settings", "settings"))
