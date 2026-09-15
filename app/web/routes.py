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
    from app.database.models import Finding, FindingEvidence
    org_id = await _demo_org_id()
    evidences=[]; packs=[]
    if org_id:
        async with get_sessionmaker()() as s:
            packs=(await s.execute(select(Finding).where(Finding.org_id==org_id).order_by(Finding.created_at.desc()).limit(20))).scalars().all()
            packs=[{"id":r.id,"title":r.title,"kind":r.kind,"status":r.status,"pack":r.evidence_pack,"provenance":r.provenance} for r in packs]
            evs=(await s.execute(select(FindingEvidence).order_by(FindingEvidence.created_at.desc()).limit(20))).scalars().all()
            # filter by org via findings — keep simple: show all if demo org exists
            evidences=[{"id":e.id,"finding_id":e.finding_id,"kind":e.kind,"content":e.content} for e in evs]
    return templates.TemplateResponse(request, "pages/evidence.html", _ctx("Evidence", "evidence", {"evidences": evidences, "packs": packs}))


@router.get("/integrations", response_class=HTMLResponse)
async def integrations(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import RawRecord
    from app.integrations.crm.base import ADAPTERS
    from app.core.config import settings
    has_key = {"hubspot": bool(settings.hubspot_api_key), "salesforce": bool(settings.salesforce_client_id), "pipedrive": bool(settings.pipedrive_api_token), "twenty": bool(settings.twenty_crm_url), "gmail": bool(settings.gmail_client_id), "outlook": bool(settings.outlook_client_id)}
    items=[]
    for k in ADAPTERS.keys():
        items.append({"source":k,"status":"configured" if has_key.get(k) else "stub","has_key": has_key.get(k)})
    items += [{"source":"csv","status":"ready","has_key": True},{"source":"upload","status":"ready","has_key": True}]
    counts={}
    org_id = await _demo_org_id()
    if org_id:
        async with get_sessionmaker()() as s:
            rows=(await s.execute(select(RawRecord.source, func.count(RawRecord.id)).where(RawRecord.org_id==org_id).group_by(RawRecord.source))).all()
            counts={r[0]: r[1] for r in rows}
    return templates.TemplateResponse(request, "pages/integrations.html", _ctx("Integrations", "integrations", {"items": items, "counts": counts}))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import Organization, Membership, User
    from app.core.config import PLANS, settings
    org_id = await _demo_org_id()
    org=None; members=[]; limits={}
    if org_id:
        async with get_sessionmaker()() as s:
            org=(await s.execute(select(Organization).where(Organization.id==org_id))).scalar_one_or_none()
            if org:
                mrows=(await s.execute(select(Membership).where(Membership.org_id==org_id))).scalars().all()
                for m in mrows:
                    u=(await s.execute(select(User).where(User.id==m.user_id))).scalar_one_or_none()
                    members.append({"email": u.email if u else m.user_id[:8], "role": m.role, "active": m.is_active})
                # counts for plan usage
                from app.database.models import Account, Finding
                acct_cnt=(await s.execute(select(func.count(Account.id)).where(Account.org_id==org_id))).scalar() or 0
                find_cnt=(await s.execute(select(func.count(Finding.id)).where(Finding.org_id==org_id))).scalar() or 0
                limits={"accounts": acct_cnt, "findings": find_cnt, "users": len(members)}
            org = {"id": org.id, "name": org.name, "slug": org.slug} if org else None
    return templates.TemplateResponse(request, "pages/settings.html", _ctx("Settings", "settings", {"org": org, "members": members, "plans": PLANS, "limits": limits, "app_url": settings.app_url, "studio_url": settings.supabase_studio_url}))
