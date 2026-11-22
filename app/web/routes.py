"""ARVO Web — Jinja2 routes. Every data page is scoped to the authenticated org."""

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Numeric, String, case, cast, func, select

from app.core.config import PLANS, settings
from app.core.limits import check_org_limits, plan_for_org, plan_limits
from app.core.security import create_token, decode_token, verify_password
from app.database.engine import get_sessionmaker
from app.database.models import (
    Account, Action, Finding, FindingEvidence, FinancialEvent, FinancialLedger,
    Membership, OPP_STAGES, Opportunity, Organization, RawRecord, User, has_capability,
)
from app.integrations.crm.base import ADAPTERS
from app.services.action_service import transition_action
from app.services.auth_service import add_org_member, create_org_with_owner
from app.services.financial import D, idempotency_key, money

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
    ("Deals", "/deals", "deals", '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>'),
    ("Accounts", "/accounts", "accounts", '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>'),
    ("Financial Impact", "/financial-impact", "financial-impact", '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>'),
    ("Actions", "/actions", "actions", '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>'),
    ("Evidence", "/evidence", "evidence", '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>'),
    ("Integrations", "/integrations", "integrations", '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>'),
    ("Settings", "/settings", "settings", '<path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>'),
]

# Bottom nav (5 items) — mobile fixed navigation
BOTTOM_NAV = [
    ("Findings", "/findings", "findings", '<path d="M9 11l3 3L22 4"/>'),
    ("Deals", "/deals", "deals", '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>'),
    ("Financial", "/financial-impact", "financial-impact", '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>'),
    ("Actions", "/actions", "actions", '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>'),
    ("Evidence", "/evidence", "evidence", '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>'),
]


def _ctx(title: str, active: str, extra: dict | None = None, request: Request | None = None) -> dict:
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


def _login_redirect() -> HTTPException:
    return HTTPException(status_code=307, headers={"Location": "/login"})


async def _optional_org(request: Request) -> dict | None:
    """Resolve the active org from the auth cookie; None when not authenticated."""
    tok = request.cookies.get("arvo_token") or request.cookies.get("aios_token")
    if not tok:
        return None
    try:
        claims = decode_token(tok)
    except HTTPException:
        return None
    async with get_sessionmaker()() as s:
        user = await s.get(User, claims.get("sub"))
        if not user or not user.is_active:
            return None
        mem = (
            await s.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.org_id == claims.get("org_id"),
                    Membership.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
    if not mem:
        return None
    return {"org_id": mem.org_id, "user_id": user.id, "role": mem.role}


async def _require_org(request: Request) -> dict:
    auth = await _optional_org(request)
    if auth is None:
        raise _login_redirect()
    return auth


def _set_session(resp: RedirectResponse, token: str, request: Request) -> RedirectResponse:
    secure = request.url.scheme == "https" or settings.https_only
    resp.set_cookie(
        "arvo_token", token, httponly=True, samesite="strict", secure=secure, max_age=3600
    )
    return resp


async def _stats(org_id: str | None) -> dict:
    """Read-only org metrics. No org means true zeros, never demo data."""
    stats = {
        "accounts": 0,
        "opps": 0,
        "findings": 0,
        "critical": 0,
        "exposure": "0.00",
        "verified": "0.00",
        "debit": "0.00",
        "credit": "0.00",
        "ledger_count": 0,
    }
    if not org_id:
        return stats
    async with get_sessionmaker()() as s:
        stats["accounts"] = (
            await s.execute(select(func.count(Account.id)).where(Account.org_id == org_id))
        ).scalar() or 0
        stats["opps"] = (
            await s.execute(select(func.count(Opportunity.id)).where(Opportunity.org_id == org_id))
        ).scalar() or 0
        f_counts = (
            await s.execute(
                select(
                    func.count(Finding.id),
                    func.sum(case((Finding.severity == "CRITICAL", 1), else_=0)),
                    func.sum(cast(Finding.exposure_amount, Numeric)),
                ).where(Finding.org_id == org_id)
            )
        ).one()
        stats["findings"] = f_counts[0] or 0
        stats["critical"] = f_counts[1] or 0
        stats["exposure"] = money(f_counts[2] or 0)
        ledger_rows = (
            await s.execute(
                select(
                    FinancialLedger.entry_type,
                    func.sum(cast(FinancialLedger.amount, Numeric)),
                    func.count(FinancialLedger.id),
                )
                .where(FinancialLedger.org_id == org_id)
                .group_by(FinancialLedger.entry_type)
            )
        ).all()
        for entry_type, key in (("DEBIT", "debit"), ("CREDIT", "credit"), ("VERIFIED", "verified")):
            stats[key] = money(next((r[1] or 0 for r in ledger_rows if r[0] == entry_type), 0))
        stats["ledger_count"] = sum(r[2] for r in ledger_rows)
    ex_f = float(stats["exposure"])
    ver_f = float(stats["verified"])
    stats["leak_pct_verified"] = int(round(ver_f / ex_f * 100)) if ex_f > 0 else 0
    stats["leak_pct_open"] = 100 - stats["leak_pct_verified"] if ex_f > 0 else 0
    return stats


@router.get("/", response_class=HTMLResponse)
async def home_root(request: Request):
    auth = await _optional_org(request)
    stats = await _stats(auth["org_id"] if auth else None)
    return templates.TemplateResponse(
        request, "pages/home.html", _ctx("Home", "home", {"plans": PLANS, "stats": stats}, request)
    )


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    return await home_root(request)


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    return templates.TemplateResponse(
        request, "pages/pricing.html", _ctx("Pricing", "pricing", {"plans": PLANS}, request)
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "pages/login.html", _ctx("Login", "login", {}, request))


@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    async with get_sessionmaker()() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not user or not verify_password(password, user.hashed_password):
            return templates.TemplateResponse(
                request,
                "pages/login.html",
                _ctx("Login", "login", {"error": "Credenciais inválidas"}, request),
                status_code=401,
            )
        mem = (
            await s.execute(
                select(Membership).where(
                    Membership.user_id == user.id, Membership.is_active.is_(True)
                )
            )
        ).scalars().first()
        if not mem:
            return templates.TemplateResponse(
                request,
                "pages/login.html",
                _ctx("Login", "login", {"error": "Sem organização"}, request),
                status_code=403,
            )
        tok = create_token(user.id, mem.org_id, mem.role)
    return _set_session(RedirectResponse(url="/control-center", status_code=302), tok, request)


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(
        request, "pages/register.html", _ctx("Register", "register", {}, request)
    )


@router.post("/register", response_class=HTMLResponse)
async def register_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    org_name: str = Form(...),
    org_slug: str = Form(...),
):
    import re

    email = email.strip().lower()
    org_slug = org_slug.strip().lower()

    def _bad(message: str):
        return templates.TemplateResponse(
            request,
            "pages/register.html",
            _ctx("Register", "register", {"error": message}, request),
            status_code=400,
        )

    if "@" not in email or "." not in email.split("@")[-1]:
        return _bad("Email inválido")
    if len(password) < 8:
        return _bad("Senha precisa de ao menos 8 caracteres")
    if not re.fullmatch(r"[a-z0-9-]{2,100}", org_slug):
        return _bad("Slug: apenas letras minúsculas, números e hífen")
    if not org_name.strip():
        return _bad("Nome da organização obrigatório")
    org_name = org_name.strip()[:255]

    try:
        async with get_sessionmaker()() as s:
            tok, _, _ = await create_org_with_owner(
                s, email, password, org_name, org_slug, "Email já existe", "Slug já existe"
            )
    except HTTPException as e:
        return _bad(e.detail)
    return _set_session(RedirectResponse(url="/control-center", status_code=302), tok, request)


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("arvo_token")
    return resp


@router.get("/control-center", response_class=HTMLResponse)
async def control_center(request: Request):
    auth = await _require_org(request)
    stats = await _stats(auth["org_id"])
    return templates.TemplateResponse(
        request,
        "pages/control_center.html",
        _ctx("Control Center", "control-center", {"stats": stats}, request),
    )


@router.get("/findings", response_class=HTMLResponse)
async def findings(request: Request):
    auth = await _require_org(request)
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(Finding)
                .where(Finding.org_id == auth["org_id"])
                .order_by(Finding.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        rows = [
            {
                "id": r.id,
                "title": r.title,
                "kind": r.kind,
                "status": r.status,
                "severity": r.severity,
                "exposure": r.exposure_amount,
                "currency": r.currency,
            }
            for r in rows
        ]
    return templates.TemplateResponse(
        request, "pages/findings.html", _ctx("Findings", "findings", {"findings": rows}, request)
    )


@router.get("/deals", response_class=HTMLResponse)
async def deals(request: Request):
    auth = await _require_org(request)
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(Opportunity)
                .where(Opportunity.org_id == auth["org_id"])
                .order_by(Opportunity.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        rows = [
            {"id": r.id, "name": r.name, "stage": r.stage, "amount": r.amount, "currency": r.currency}
            for r in rows
        ]
        accounts_list = (
            await s.execute(
                select(Account)
                .where(Account.org_id == auth["org_id"])
                .order_by(Account.name)
                .limit(100)
            )
        ).scalars().all()
        accounts_list = [{"id": a.id, "name": a.name} for a in accounts_list]
    return templates.TemplateResponse(
        request,
        "pages/deals.html",
        _ctx(
            "Deals",
            "deals",
            {"deals": rows, "accounts": accounts_list, "stages": OPP_STAGES},
            request,
        ),
    )


@router.get("/accounts", response_class=HTMLResponse)
async def accounts(request: Request):
    auth = await _require_org(request)
    type_filter = request.query_params.get("type")
    if type_filter not in ("PROSPECT", "CUSTOMER"):
        type_filter = None
    async with get_sessionmaker()() as s:
        q = select(Account).where(Account.org_id == auth["org_id"])
        if type_filter:
            q = q.where(Account.type == type_filter)
        rows = (await s.execute(q.order_by(Account.created_at.desc()).limit(100))).scalars().all()
        rows = [{"id": r.id, "name": r.name, "domain": r.domain, "type": r.type} for r in rows]
    return templates.TemplateResponse(
        request,
        "pages/accounts.html",
        _ctx("Accounts", "accounts", {"accounts": rows, "type_filter": type_filter}, request),
    )


@router.post("/accounts")
async def accounts_create(
    request: Request,
    name: str = Form(...),
    domain: str = Form(""),
    type: str = Form("CUSTOMER"),
):
    from datetime import datetime, timezone

    auth = await _require_org(request)
    name = name.strip()
    if not name:
        raise HTTPException(400, "name required")
    if type not in ("CUSTOMER", "PROSPECT", "PARTNER"):
        raise HTTPException(400, "Invalid account type")
    ok, err, _ = await check_org_limits(auth["org_id"], "accounts")
    if not ok:
        raise HTTPException(402, err)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with get_sessionmaker()() as s:
        s.add(
            Account(
                org_id=auth["org_id"],
                name=name[:255],
                domain=domain.strip()[:255] or None,
                type=type,
                owner_id=auth["user_id"],
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    return RedirectResponse(url="/accounts", status_code=302)


@router.post("/accounts/import")
async def accounts_import(request: Request, file: UploadFile = File(...)):
    import csv
    import io

    auth = await _require_org(request)
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Upload exceeds 5 MB")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8", errors="ignore"))))
    valid = [r for r in rows if (r.get("name") or r.get("account_name") or r.get("company"))]
    ok, err, _ = await check_org_limits(auth["org_id"], "accounts", delta=len(valid))
    if not ok and valid:
        raise HTTPException(402, err)
    async with get_sessionmaker()() as s:
        raw = RawRecord(
            org_id=auth["org_id"],
            source="CSV",
            source_id=file.filename,
            payload={"rows": rows, "filename": file.filename},
            status="NORMALIZED",
        )
        s.add(raw)
        for r in valid:
            name = (r.get("name") or r.get("account_name") or r.get("company")).strip()
            s.add(
                Account(
                    org_id=auth["org_id"],
                    name=name,
                    domain=(r.get("domain") or "").strip() or None,
                    type="CUSTOMER",
                    owner_id=auth["user_id"],
                    extra_data={"ingest": "csv", "raw": r},
                )
            )
        await s.commit()
    return RedirectResponse(url="/accounts", status_code=302)


@router.post("/deals")
async def deals_create(
    request: Request,
    account_id: str = Form(...),
    name: str = Form(...),
    amount: str = Form("0"),
    stage: str = Form("PROSPECTING"),
    currency: str = Form("BRL"),
):
    auth = await _require_org(request)
    name = name.strip()
    currency = currency.strip().upper()
    if not name:
        raise HTTPException(400, "name required")
    if stage not in OPP_STAGES:
        raise HTTPException(400, "Invalid stage")
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(400, "currency must be a 3-letter code")
    try:
        amount_d = D(amount)
    except Exception as e:
        raise HTTPException(400, "amount must be a decimal number") from e
    if amount_d < 0:
        raise HTTPException(400, "amount must be >= 0")
    async with get_sessionmaker()() as s:
        acc = await s.get(Account, account_id)
        if not acc or acc.org_id != auth["org_id"]:
            raise HTTPException(404, "Account not found")
        s.add(
            Opportunity(
                org_id=auth["org_id"],
                account_id=account_id,
                name=name[:255],
                amount=money(amount_d),
                stage=stage,
                currency=currency,
                owner_id=auth["user_id"],
            )
        )
        await s.commit()
    return RedirectResponse(url="/deals", status_code=302)


@router.post("/findings")
async def findings_create(
    request: Request,
    title: str = Form(...),
    kind: str = Form("LEAKAGE"),
    severity: str = Form("MEDIUM"),
    exposure_amount: str = Form("0"),
    description: str = Form(""),
):
    auth = await _require_org(request)
    title = title.strip()
    if not title:
        raise HTTPException(400, "title required")
    if severity not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        raise HTTPException(400, "Invalid severity")
    try:
        amount = D(exposure_amount)
    except Exception as e:
        raise HTTPException(400, "exposure_amount must be a decimal number") from e
    if amount < 0:
        raise HTTPException(400, "exposure_amount must be >= 0")
    ok, err, _ = await check_org_limits(auth["org_id"], "findings")
    if not ok:
        raise HTTPException(402, err)
    async with get_sessionmaker()() as s:
        f = Finding(
            org_id=auth["org_id"],
            kind=(kind.strip().upper() or "LEAKAGE")[:100],
            title=title[:500],
            description=description or None,
            severity=severity,
            exposure_amount=money(amount),
            currency="BRL",
        )
        s.add(f)
        await s.flush()
        s.add(
            FinancialEvent(
                org_id=auth["org_id"],
                kind="RISK_EXPOSURE",
                amount=money(amount),
                currency="BRL",
                idempotency_key=idempotency_key(auth["org_id"], "finding", f.id),
            )
        )
        await s.commit()
    return RedirectResponse(url="/findings", status_code=302)


@router.post("/actions/{aid}/{transition}")
async def action_transition(
    request: Request, aid: str, transition: str, verified_amount: str = Form("0")
):
    auth = await _require_org(request)
    if transition not in ("approve", "execute", "verify"):
        raise HTTPException(404)
    capability = {"approve": "findings:approve", "execute": "actions:execute", "verify": "findings:approve"}[transition]
    if not has_capability(auth["role"], capability):
        raise HTTPException(403, f"Need {capability} capability")
    await transition_action(
        org_id=auth["org_id"], user_id=auth["user_id"], aid=aid,
        transition=transition, verified_amount=verified_amount,
    )
    return RedirectResponse(url="/actions", status_code=302)


@router.get("/financial-impact", response_class=HTMLResponse)
async def financial_impact(request: Request):
    auth = await _require_org(request)
    stats = await _stats(auth["org_id"])
    return templates.TemplateResponse(
        request,
        "pages/financial_impact.html",
        _ctx("Financial Impact", "financial-impact", {"impact": stats}, request),
    )


@router.get("/actions", response_class=HTMLResponse)
async def actions(request: Request):
    auth = await _require_org(request)
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(Action)
                .where(Action.org_id == auth["org_id"])
                .order_by(Action.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        rows = [
            {"id": r.id, "title": r.title, "status": r.status, "verified_amount": r.verified_amount}
            for r in rows
        ]
    return templates.TemplateResponse(
        request, "pages/actions.html", _ctx("Actions", "actions", {"actions": rows}, request)
    )


@router.get("/evidence", response_class=HTMLResponse)
async def evidence(request: Request):
    auth = await _require_org(request)
    try:
        limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        offset = max(int(request.query_params.get("offset", 0)), 0)
    except ValueError:
        raise HTTPException(400, "limit/offset must be integers")
    async with get_sessionmaker()() as s:
        # JSON equality is not supported by Postgres json; compare as text.
        pack_text = cast(Finding.evidence_pack, String)
        has_pack = func.coalesce(func.length(pack_text), 0) > 2
        has_evidence = has_pack | (
            select(FindingEvidence.id)
            .where(FindingEvidence.finding_id == Finding.id)
            .exists()
        )
        in_scope = (Finding.org_id == auth["org_id"]) & has_evidence
        total = (
            await s.execute(select(func.count(Finding.id)).where(in_scope))
        ).scalar() or 0
        packs = (
            await s.execute(
                select(Finding)
                .where(in_scope)
                .order_by(Finding.created_at.desc(), Finding.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        packs = [
            {
                "id": r.id,
                "title": r.title,
                "kind": r.kind,
                "status": r.status,
                "exposure_amount": r.exposure_amount,
                "pack": r.evidence_pack,
                "provenance": r.provenance,
            }
            for r in packs
        ]
        evs = (
            await s.execute(
                select(FindingEvidence)
                .join(Finding, FindingEvidence.finding_id == Finding.id)
                .where(Finding.org_id == auth["org_id"])
                .order_by(FindingEvidence.created_at.desc(), FindingEvidence.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        evidences = [
            {"id": e.id, "finding_id": e.finding_id, "kind": e.kind, "content": e.content}
            for e in evs
        ]
    return templates.TemplateResponse(
        request,
        "pages/evidence.html",
        _ctx(
            "Evidence",
            "evidence",
            {
                "evidences": evidences,
                "packs": packs,
                "limit": limit,
                "offset": offset,
                "total": total,
                "prev_offset": max(offset - limit, 0),
                "next_offset": offset + limit,
                "has_next": offset + limit < total,
            },
            request,
        ),
    )


@router.get("/integrations", response_class=HTMLResponse)
async def integrations(request: Request):
    auth = await _require_org(request)
    has_key = {
        "hubspot": bool(settings.hubspot_api_key),
        "salesforce": bool(settings.salesforce_client_id),
        "pipedrive": bool(settings.pipedrive_api_token),
        "twenty": bool(settings.twenty_crm_url),
        "gmail": bool(settings.gmail_client_id),
        "outlook": bool(settings.outlook_client_id),
    }
    items = [
        {"source": k, "status": "key set, sync 501" if has_key.get(k) else "no key, sync 501", "has_key": False}
        for k in ADAPTERS
    ]
    items += [
        {"source": "csv", "status": "ready", "has_key": True},
        {"source": "upload", "status": "ready", "has_key": True},
    ]
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(RawRecord.source, func.count(RawRecord.id))
                .where(RawRecord.org_id == auth["org_id"])
                .group_by(RawRecord.source)
            )
        ).all()
        counts = {r[0]: r[1] for r in rows}
    return templates.TemplateResponse(
        request,
        "pages/integrations.html",
        _ctx("Integrations", "integrations", {"items": items, "counts": counts}, request),
    )


@router.post("/settings/members")
async def settings_add_member(
    request: Request, email: str = Form(...), role: str = Form("VIEWER")
):
    auth = await _require_org(request)
    await add_org_member(
        auth["org_id"], auth["role"], email, role, "Cannot assign a role above your own"
    )
    return RedirectResponse(url="/settings", status_code=302)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    auth = await _require_org(request)
    async with get_sessionmaker()() as s:
        org = await s.get(Organization, auth["org_id"])
        mrows = (
            await s.execute(select(Membership).where(Membership.org_id == auth["org_id"]))
        ).scalars().all()
        emails = {
            u.id: u.email
            for u in (
                await s.execute(select(User).where(User.id.in_([m.user_id for m in mrows])))
            ).scalars().all()
        }
        members = [
            {"email": emails.get(m.user_id) or m.user_id[:8], "role": m.role, "active": m.is_active}
            for m in mrows
        ]
        acct_cnt = (
            await s.execute(select(func.count(Account.id)).where(Account.org_id == auth["org_id"]))
        ).scalar() or 0
        find_cnt = (
            await s.execute(select(func.count(Finding.id)).where(Finding.org_id == auth["org_id"]))
        ).scalar() or 0
        usage = {"accounts": acct_cnt, "findings": find_cnt, "users": len(members)}
        plan = plan_for_org(org.extra_data if org else None)
        plan_limits_data = plan_limits(plan)
        org_data = {"id": org.id, "name": org.name, "slug": org.slug} if org else None
    return templates.TemplateResponse(
        request,
        "pages/settings.html",
        _ctx(
            "Settings",
            "settings",
            {
                "org": org_data,
                "members": members,
                "plans": PLANS,
                "plan": plan,
                "plan_limits": plan_limits_data,
                "usage": usage,
                "app_url": settings.app_url,
                "studio_url": settings.supabase_studio_url,
            },
            request,
        ),
    )
