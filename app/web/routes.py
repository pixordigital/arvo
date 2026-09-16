"""ARVO Web — Jinja2 routes. Spec §231-242. Shared base.html, auth-aware nav, CSRF on POST."""

from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


def _brl(value) -> str:
    """Format 19500.00 -> R$ 19.500,00 (pt-BR). Never invents, only formats."""
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
    except Exception:
        return "—"
    s = f"{n:,.2f}"  # 19,500.00
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


_PT_MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _today_pt() -> str:
    from datetime import date
    d = date.today()
    return f"{d.day:02d} {_PT_MONTHS[d.month - 1]} {d.year}"


templates.env.filters["brl"] = _brl

# Navigation spec §242 — sidebar items with icons
NAV = [
    ("Home", "/home", "home", '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    ("Control Center", "/", "control-center", '<path d="M3 12l2-2m0 0l7-7 7 7M5 10v10a3 3 0 003 3h5a3 3 0 003-3v-10"/>'),
    ("Findings", "/findings", "findings", '<path d="M9 11l3 3L22 4"/>'),
    ("Deals", "/deals", "deals", '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>'),
    ("Accounts", "/accounts", "accounts", '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>'),
    ("Financial Impact", "/financial-impact", "financial-impact", '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>'),
    ("Actions", "/actions", "actions", '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>'),
    ("Evidence", "/evidence", "evidence", '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>'),
    ("Integrations", "/integrations", "integrations", '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>'),
    ("Settings", "/settings", "settings", '<path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>'),
]

# Bottom nav (5 items) — mobile fixed navigation
BOTTOM_NAV = [
    ("Findings", "/findings", "findings", '<path d="M9 11l3 3L22 4"/>'),
    ("Deals", "/deals", "deals", '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>'),
    ("Financial", "/financial-impact", "financial-impact", '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>'),
    ("Actions", "/actions", "actions", '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>'),
    ("Evidence", "/evidence", "evidence", '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>'),
]


def _ctx(title: str, active: str, extra: dict | None = None, request: Request | None = None) -> dict:
    from app.core.config import settings
    theme = "dark"
    if request:
        theme = request.cookies.get("arvo-theme", "dark")
    base = {
        "title": title,
        "active": active,
        "nav": NAV,
        "bottom_nav": BOTTOM_NAV,
        "theme": theme,
        "today": _today_pt(),
        "app_name": settings.app_name,
        "studio_url": settings.supabase_studio_url,
        "app_url": settings.app_url,
    }
    if extra:
        base.update(extra)
    return base


async def _demo_org_id():
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Organization
    async with get_sessionmaker()() as s:
        org = (await s.execute(select(Organization).where(Organization.slug == "arvo-demo"))).scalar_one_or_none()
        return org.id if org else None


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import Account, Opportunity, Finding, FinancialLedger
    from app.core.config import PLANS
    org_id = await _demo_org_id()
    stats = {"accounts": 0, "opps": 0, "findings": 0, "exposure": "0.00", "verified": "0.00"}
    if org_id:
        async with get_sessionmaker()() as s:
            stats["accounts"] = (await s.execute(select(func.count(Account.id)).where(Account.org_id == org_id))).scalar()
            stats["opps"] = (await s.execute(select(func.count(Opportunity.id)).where(Opportunity.org_id == org_id))).scalar()
            stats["findings"] = (await s.execute(select(func.count(Finding.id)).where(Finding.org_id == org_id))).scalar()
            ex = (await s.execute(select(func.coalesce(func.sum(Finding.exposure_amount), 0)).where(Finding.org_id == org_id))).scalar()
            stats["exposure"] = f"{float(ex or 0):.2f}"
            ver = (await s.execute(select(func.coalesce(func.sum(FinancialLedger.amount), 0)).where(FinancialLedger.org_id == org_id, FinancialLedger.entry_type == "VERIFIED"))).scalar()
            stats["verified"] = f"{float(ver or 0):.2f}"
    return templates.TemplateResponse(request, "pages/home.html", _ctx("Home", "home", {"plans": PLANS, "stats": stats}, request))


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    from app.core.config import PLANS
    return templates.TemplateResponse(request, "pages/pricing.html", _ctx("Pricing", "pricing", {"plans": PLANS}, request))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "pages/login.html", _ctx("Login", "login", {}, request))


@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    from app.database.engine import get_sessionmaker
    from sqlalchemy import select
    from app.database.models import User, Membership
    from app.core.security import verify_password, create_token
    async with get_sessionmaker()() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user or not verify_password(password, user.hashed_password):
            return templates.TemplateResponse(request, "pages/login.html", _ctx("Login", "login", {"error": "Credenciais inválidas"}, request), status_code=401)
        mem = (await s.execute(select(Membership).where(Membership.user_id == user.id))).scalars().first()
        if not mem:
            return templates.TemplateResponse(request, "pages/login.html", _ctx("Login", "login", {"error": "Sem organização"}, request), status_code=403)
        tok = create_token(user.id, mem.org_id, mem.role)
        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie("arvo_token", tok, httponly=True, samesite="lax", max_age=3600)
        return resp


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "pages/register.html", _ctx("Register", "register", {}, request))


@router.post("/register", response_class=HTMLResponse)
async def register_post(request: Request, email: str = Form(...), password: str = Form(...), org_name: str = Form(...), org_slug: str = Form(...)):
    from app.database.engine import get_sessionmaker
    from sqlalchemy import select
    from app.database.models import User, Organization, Membership
    from app.core.security import hash_password, create_token
    async with get_sessionmaker()() as s:
        if (await s.execute(select(User).where(User.email == email))).scalar_one_or_none():
            return templates.TemplateResponse(request, "pages/register.html", _ctx("Register", "register", {"error": "Email já existe"}, request), status_code=400)
        if (await s.execute(select(Organization).where(Organization.slug == org_slug))).scalar_one_or_none():
            return templates.TemplateResponse(request, "pages/register.html", _ctx("Register", "register", {"error": "Slug já existe"}, request), status_code=400)
        org = Organization(name=org_name, slug=org_slug)
        s.add(org); await s.flush()
        user = User(email=email, hashed_password=hash_password(password))
        s.add(user); await s.flush()
        mem = Membership(org_id=org.id, user_id=user.id, role="OWNER")
        s.add(mem); await s.commit()
        tok = create_token(user.id, org.id, "OWNER")
        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie("arvo_token", tok, httponly=True, samesite="lax", max_age=3600)
        return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("arvo_token")
    return resp


@router.get("/", response_class=HTMLResponse)
async def control_center(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import Account, Opportunity, Finding, FinancialLedger
    org_id = await _demo_org_id()
    stats = {"accounts": 0, "opps": 0, "findings": 0, "critical": 0, "exposure": "0.00", "verified": "0.00"}
    if org_id:
        async with get_sessionmaker()() as s:
            stats["accounts"] = (await s.execute(select(func.count(Account.id)).where(Account.org_id == org_id))).scalar()
            stats["opps"] = (await s.execute(select(func.count(Opportunity.id)).where(Opportunity.org_id == org_id))).scalar()
            stats["findings"] = (await s.execute(select(func.count(Finding.id)).where(Finding.org_id == org_id))).scalar()
            stats["critical"] = (await s.execute(select(func.count(Finding.id)).where(Finding.org_id == org_id, Finding.severity == "critical"))).scalar() or 0
            ex = (await s.execute(select(func.coalesce(func.sum(Finding.exposure_amount), 0)).where(Finding.org_id == org_id))).scalar()
            stats["exposure"] = f"{float(ex or 0):.2f}"
            ver = (await s.execute(select(func.coalesce(func.sum(FinancialLedger.amount), 0)).where(FinancialLedger.org_id == org_id, FinancialLedger.entry_type == "VERIFIED"))).scalar()
            stats["verified"] = f"{float(ver or 0):.2f}"
    ex_f = float(stats["exposure"]); ver_f = float(stats["verified"])
    stats["leak_pct_verified"] = int(round(ver_f / ex_f * 100)) if ex_f > 0 else 0
    stats["leak_pct_open"] = 100 - stats["leak_pct_verified"] if ex_f > 0 else 0
    return templates.TemplateResponse(request, "pages/control_center.html", _ctx("Control Center", "control-center", {"stats": stats}, request))


@router.get("/findings", response_class=HTMLResponse)
async def findings(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding
    org_id = await _demo_org_id()
    rows = []
    if org_id:
        async with get_sessionmaker()() as s:
            rows = (await s.execute(select(Finding).where(Finding.org_id == org_id).order_by(Finding.created_at.desc()))).scalars().all()
            rows = [{"id": r.id, "title": r.title, "kind": r.kind, "status": r.status, "severity": r.severity, "exposure": r.exposure_amount, "currency": r.currency} for r in rows]
    return templates.TemplateResponse(request, "pages/findings.html", _ctx("Findings", "findings", {"findings": rows}, request))


@router.get("/deals", response_class=HTMLResponse)
async def deals(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Opportunity
    org_id = await _demo_org_id()
    rows = []
    if org_id:
        async with get_sessionmaker()() as s:
            rows = (await s.execute(select(Opportunity).where(Opportunity.org_id == org_id).order_by(Opportunity.created_at.desc()))).scalars().all()
            rows = [{"id": r.id, "name": r.name, "stage": r.stage, "amount": r.amount, "currency": r.currency} for r in rows]
    return templates.TemplateResponse(request, "pages/deals.html", _ctx("Deals", "deals", {"deals": rows}, request))


@router.get("/accounts", response_class=HTMLResponse)
async def accounts(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Account
    org_id = await _demo_org_id()
    type_filter = request.query_params.get("type")
    if type_filter not in ("PROSPECT", "CUSTOMER"):
        type_filter = None
    rows = []
    if org_id:
        async with get_sessionmaker()() as s:
            q = select(Account).where(Account.org_id == org_id)
            if type_filter:
                q = q.where(Account.type == type_filter)
            rows = (await s.execute(q.order_by(Account.created_at.desc()))).scalars().all()
            rows = [{"id": r.id, "name": r.name, "domain": r.domain, "type": r.type} for r in rows]
    return templates.TemplateResponse(request, "pages/accounts.html", _ctx("Accounts", "accounts", {"accounts": rows, "type_filter": type_filter}, request))


@router.get("/financial-impact", response_class=HTMLResponse)
async def financial_impact(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding, FinancialLedger
    org_id = await _demo_org_id()
    data = {"exposure": "0.00", "verified": "0.00", "count": 0}
    if org_id:
        async with get_sessionmaker()() as s:
            data["count"] = (await s.execute(select(func.count(Finding.id)).where(Finding.org_id == org_id))).scalar()
            data["exposure"] = f"{float((await s.execute(select(func.coalesce(func.sum(Finding.exposure_amount), 0)).where(Finding.org_id == org_id))).scalar() or 0):.2f}"
            data["verified"] = f"{float((await s.execute(select(func.coalesce(func.sum(FinancialLedger.amount), 0)).where(FinancialLedger.org_id == org_id, FinancialLedger.entry_type == 'VERIFIED'))).scalar() or 0):.2f}"
            data["debit"] = f"{float((await s.execute(select(func.coalesce(func.sum(FinancialLedger.amount), 0)).where(FinancialLedger.org_id == org_id, FinancialLedger.entry_type == 'DEBIT'))).scalar() or 0):.2f}"
    return templates.TemplateResponse(request, "pages/financial_impact.html", _ctx("Financial Impact", "financial-impact", {"impact": data}, request))


@router.get("/actions", response_class=HTMLResponse)
async def actions(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Action
    org_id = await _demo_org_id()
    rows = []
    if org_id:
        async with get_sessionmaker()() as s:
            rows = (await s.execute(select(Action).where(Action.org_id == org_id).order_by(Action.created_at.desc()))).scalars().all()
            rows = [{"id": r.id, "title": r.title, "status": r.status, "verified_amount": r.verified_amount} for r in rows]
    return templates.TemplateResponse(request, "pages/actions.html", _ctx("Actions", "actions", {"actions": rows}, request))


@router.get("/evidence", response_class=HTMLResponse)
async def evidence(request: Request):
    from sqlalchemy import select
    from app.database.engine import get_sessionmaker
    from app.database.models import Finding, FindingEvidence
    org_id = await _demo_org_id()
    evidences = []
    packs = []
    if org_id:
        async with get_sessionmaker()() as s:
            packs = (await s.execute(select(Finding).where(Finding.org_id == org_id).order_by(Finding.created_at.desc()).limit(20))).scalars().all()
            packs = [{"id": r.id, "title": r.title, "kind": r.kind, "status": r.status, "pack": r.evidence_pack, "provenance": r.provenance} for r in packs]
            evs = (await s.execute(select(FindingEvidence).order_by(FindingEvidence.created_at.desc()).limit(20))).scalars().all()
            evidences = [{"id": e.id, "finding_id": e.finding_id, "kind": e.kind, "content": e.content} for e in evs]
    return templates.TemplateResponse(request, "pages/evidence.html", _ctx("Evidence", "evidence", {"evidences": evidences, "packs": packs}, request))


@router.get("/integrations", response_class=HTMLResponse)
async def integrations(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import RawRecord
    from app.integrations.crm.base import ADAPTERS
    from app.core.config import settings
    has_key = {"hubspot": bool(settings.hubspot_api_key), "salesforce": bool(settings.salesforce_client_id), "pipedrive": bool(settings.pipedrive_api_token), "twenty": bool(settings.twenty_crm_url), "gmail": bool(settings.gmail_client_id), "outlook": bool(settings.outlook_client_id)}
    items = []
    for k in ADAPTERS.keys():
        items.append({"source": k, "status": "configured" if has_key.get(k) else "stub", "has_key": has_key.get(k)})
    items += [{"source": "csv", "status": "ready", "has_key": True}, {"source": "upload", "status": "ready", "has_key": True}]
    counts = {}
    org_id = await _demo_org_id()
    if org_id:
        async with get_sessionmaker()() as s:
            rows = (await s.execute(select(RawRecord.source, func.count(RawRecord.id)).where(RawRecord.org_id == org_id).group_by(RawRecord.source))).all()
            counts = {r[0]: r[1] for r in rows}
    return templates.TemplateResponse(request, "pages/integrations.html", _ctx("Integrations", "integrations", {"items": items, "counts": counts}, request))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    from sqlalchemy import select, func
    from app.database.engine import get_sessionmaker
    from app.database.models import Organization, Membership, User
    from app.core.config import PLANS, settings
    org_id = await _demo_org_id()
    org = None
    members = []
    limits = {}
    if org_id:
        async with get_sessionmaker()() as s:
            org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
            if org:
                mrows = (await s.execute(select(Membership).where(Membership.org_id == org_id))).scalars().all()
                for m in mrows:
                    u = (await s.execute(select(User).where(User.id == m.user_id))).scalar_one_or_none()
                    members.append({"email": u.email if u else m.user_id[:8], "role": m.role, "active": m.is_active})
                from app.database.models import Account, Finding
                acct_cnt = (await s.execute(select(func.count(Account.id)).where(Account.org_id == org_id))).scalar() or 0
                find_cnt = (await s.execute(select(func.count(Finding.id)).where(Finding.org_id == org_id))).scalar() or 0
                limits = {"accounts": acct_cnt, "findings": find_cnt, "users": len(members)}
                org = {"id": org.id, "name": org.name, "slug": org.slug} if org else None
    return templates.TemplateResponse(request, "pages/settings.html", _ctx("Settings", "settings", {"org": org, "members": members, "plans": PLANS, "limits": limits, "app_url": settings.app_url, "studio_url": settings.supabase_studio_url}, request))