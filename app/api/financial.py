"""ARVO — Financial API: products, proposals, invoices, ledger, findings (deterministic)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Product, Proposal, ProposalItem, Invoice, FinancialEvent, FinancialLedger, Finding, FindingEvidence
from app.services.financial import calc_proposal_total, idempotency_key, money
from app.api.deps import get_current_user

router = APIRouter(tags=["financial"])

class ProductIn(BaseModel):
    name: str; sku: str|None=None; unit_price: str="0"; currency: str="BRL"

class ProposalIn(BaseModel):
    account_id: str|None=None; opportunity_id: str|None=None
    currency: str="BRL"; discount_pct: str="0"
    items: list[dict]  # [{description, quantity, unit_price, discount_pct, product_id}]

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
        # ledger entry for exposure (gross-net if discount)
        exp = money(float(gross)-float(net))
        if float(exp) !=0:
            fe=FinancialEvent(org_id=ctx["membership"].org_id, kind="DISCOUNT", amount=exp, currency=inp.currency, idempotency_key=idempotency_key(ctx["membership"].org_id,"proposal-discount",prop.id), attribution_level=2)
            s.add(fe); await s.flush()
            fl=FinancialLedger(org_id=ctx["membership"].org_id, event_id=fe.id, entry_type="DEBIT", amount=exp, currency=inp.currency, description=f"Discount exposure proposal {prop.id}")
            s.add(fl)
        await s.commit(); await s.refresh(prop)
        return {"id":prop.id,"gross":gross,"net":net,"exposure":exp if float(exp)!=0 else "0.00"}

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

@router.post("/findings")
async def create_finding(data: dict, ctx=Depends(get_current_user)):
    # Deterministic exposure must be provided; AI would have interpreted but Python stores
    async with get_sessionmaker()() as s:
        f=Finding(org_id=ctx["membership"].org_id, kind=data.get("kind","LEAKAGE"), title=data.get("title","Finding"), description=data.get("description"), severity=data.get("severity","MEDIUM"), exposure_amount=str(data.get("exposure_amount","0")), currency=data.get("currency","BRL"), confidence=data.get("confidence","MEDIUM"), evidence_pack=data.get("evidence_pack",{}), idempotency_key=data.get("idempotency_key"))
        s.add(f); await s.flush()
        fe=FinancialEvent(org_id=ctx["membership"].org_id, kind="RISK_EXPOSURE", amount=str(data.get("exposure_amount","0")), currency=data.get("currency","BRL"), idempotency_key=idempotency_key(ctx["membership"].org_id,"finding",f.id))
        s.add(fe); await s.commit(); await s.refresh(f)
        return {"id": f.id, "status": f.status}
