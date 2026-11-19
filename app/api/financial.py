"""ARVO — Financial API: products, proposals, invoices, ledger, findings (deterministic)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Product, Proposal, ProposalItem, Invoice, FinancialEvent, FinancialLedger, Finding, FindingEvidence
from app.services.financial import D, calc_discount_exposure, calc_proposal_total, idempotency_key, money
from app.api.deps import get_current_user
from app.core.limits import check_org_limits

router = APIRouter(tags=["financial"])

SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

def _currency(v: str) -> str:
    v = (v or "").strip().upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("currency must be a 3-letter code")
    return v

class ProductIn(BaseModel):
    name: str; sku: str|None=None; unit_price: str="0"; currency: str="BRL"

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name required")
        return v[:255]

    @field_validator("unit_price")
    @classmethod
    def non_negative_price(cls, v: str) -> str:
        amount = D(v)
        if amount < 0:
            raise ValueError("unit_price must be >= 0")
        return money(amount)

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v: str) -> str:
        return _currency(v)

class ProposalIn(BaseModel):
    account_id: str|None=None; opportunity_id: str|None=None
    currency: str="BRL"; discount_pct: str="0"
    items: list[dict]  # [{description, quantity, unit_price, discount_pct, product_id}]

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v: str) -> str:
        return _currency(v)

    @field_validator("discount_pct")
    @classmethod
    def valid_discount(cls, v: str) -> str:
        pct = D(v)
        if pct < 0 or pct > 100:
            raise ValueError("discount_pct must be between 0 and 100")
        return str(pct)

    @field_validator("items")
    @classmethod
    def valid_items(cls, items: list[dict]) -> list[dict]:
        if not items:
            raise ValueError("at least one item required")
        cleaned = []
        for it in items:
            if not isinstance(it, dict):
                raise ValueError("each item must be an object")
            qty = D(it.get("quantity", "1"))
            up = D(it.get("unit_price", "0"))
            disc = D(it.get("discount_pct", "0"))
            if qty <= 0:
                raise ValueError("quantity must be > 0")
            if up < 0:
                raise ValueError("unit_price must be >= 0")
            if disc < 0 or disc > 100:
                raise ValueError("discount_pct must be between 0 and 100")
            cleaned.append({**it, "quantity": str(qty), "unit_price": money(up), "discount_pct": str(disc)})
        return cleaned

@router.post("/products")
async def create_product(inp: ProductIn, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        p=Product(org_id=ctx["membership"].org_id, name=inp.name, sku=inp.sku, unit_price=inp.unit_price, currency=inp.currency)
        s.add(p); await s.commit(); await s.refresh(p); return {"id":p.id}

@router.get("/products")
async def list_products(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows=(await s.execute(select(Product).where(Product.org_id==ctx["membership"].org_id))).scalars().all()
        return [{"id":r.id,"name":r.name,"unit_price":r.unit_price} for r in rows]

@router.post("/proposals")
async def create_proposal(inp: ProposalIn, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        gross, net = calc_proposal_total(inp.items)
        prop = Proposal(org_id=ctx["membership"].org_id, account_id=inp.account_id, opportunity_id=inp.opportunity_id, currency=inp.currency, discount_pct=inp.discount_pct, total_amount=net)
        s.add(prop); await s.flush()
        for it in inp.items:
            pi=ProposalItem(proposal_id=prop.id, product_id=it.get("product_id"), description=it.get("description",""), quantity=str(it.get("quantity","1")), unit_price=str(it.get("unit_price","0")), discount_pct=str(it.get("discount_pct","0")))
            s.add(pi)
        # ledger entry for exposure (gross-net if discount), Decimal only
        exp = calc_discount_exposure(gross, net)
        if exp != "0.00":
            fe=FinancialEvent(org_id=ctx["membership"].org_id, kind="DISCOUNT", amount=exp, currency=inp.currency, idempotency_key=idempotency_key(ctx["membership"].org_id,"proposal-discount",prop.id), attribution_level=2)
            s.add(fe); await s.flush()
            fl=FinancialLedger(org_id=ctx["membership"].org_id, event_id=fe.id, entry_type="DEBIT", amount=exp, currency=inp.currency, description=f"Discount exposure proposal {prop.id}")
            s.add(fl)
        await s.commit(); await s.refresh(prop)
        return {"id":prop.id,"gross":gross,"net":net,"exposure":exp}

@router.get("/proposals")
async def list_proposals(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows=(await s.execute(select(Proposal).where(Proposal.org_id==ctx["membership"].org_id).order_by(Proposal.created_at.desc()))).scalars().all()
        return [{"id":r.id,"total_amount":r.total_amount,"currency":r.currency,"status":r.status} for r in rows]

@router.get("/ledger")
async def list_ledger(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows=(await s.execute(select(FinancialLedger).where(FinancialLedger.org_id==ctx["membership"].org_id).order_by(FinancialLedger.created_at.desc()).limit(50))).scalars().all()
        return [{"id":r.id,"entry_type":r.entry_type,"amount":r.amount,"description":r.description} for r in rows]

@router.get("/findings")
async def list_findings(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows=(await s.execute(select(Finding).where(Finding.org_id==ctx["membership"].org_id).order_by(Finding.created_at.desc()).limit(50))).scalars().all()
        return [{"id":r.id,"kind":r.kind,"status":r.status,"title":r.title,"exposure_amount":r.exposure_amount} for r in rows]

class FindingIn(BaseModel):
    kind: str = "LEAKAGE"
    title: str = "Finding"
    description: str | None = None
    severity: str = "MEDIUM"
    exposure_amount: str = "0"
    currency: str = "BRL"
    confidence: str = "MEDIUM"
    evidence_pack: dict = {}
    idempotency_key: str | None = None

    @field_validator("kind")
    @classmethod
    def kind_not_blank(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("kind required")
        return v[:100]

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title required")
        return v[:500]

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        return v

    @field_validator("exposure_amount")
    @classmethod
    def non_negative_exposure(cls, v: str) -> str:
        amount = D(v)
        if amount < 0:
            raise ValueError("exposure_amount must be >= 0")
        return money(amount)

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v: str) -> str:
        return _currency(v)


@router.post("/findings")
async def create_finding(inp: FindingIn, ctx=Depends(get_current_user)):
    ok, err, _ = await check_org_limits(ctx["membership"].org_id, "findings")
    if not ok: raise HTTPException(402, err)
    async with get_sessionmaker()() as s:
        f=Finding(org_id=ctx["membership"].org_id, kind=inp.kind, title=inp.title, description=inp.description, severity=inp.severity, exposure_amount=inp.exposure_amount, currency=inp.currency, confidence=inp.confidence, evidence_pack=inp.evidence_pack, idempotency_key=inp.idempotency_key)
        s.add(f); await s.flush()
        fe=FinancialEvent(org_id=ctx["membership"].org_id, kind="RISK_EXPOSURE", amount=inp.exposure_amount, currency=inp.currency, idempotency_key=idempotency_key(ctx["membership"].org_id,"finding",f.id))
        s.add(fe); await s.commit(); await s.refresh(f)
        return {"id": f.id, "status": f.status}
