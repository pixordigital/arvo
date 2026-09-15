"""ARVO — Opportunities API."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Opportunity, Account
from app.api.deps import get_current_user

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

class OppIn(BaseModel):
    account_id: str
    name: str
    stage: str = "PROSPECTING"
    amount: str = "0"
    currency: str = "BRL"

@router.get("")
async def list_opps(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(Opportunity).where(Opportunity.org_id==ctx["membership"].org_id).order_by(Opportunity.created_at.desc()))).scalars().all()
        return [{"id":r.id,"name":r.name,"stage":r.stage,"amount":r.amount,"account_id":r.account_id} for r in rows]

@router.post("")
async def create_opp(inp: OppIn, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        acc = await s.get(Account, inp.account_id)
        if not acc or acc.org_id != ctx["membership"].org_id: raise HTTPException(404, "Account not found")
        o = Opportunity(org_id=ctx["membership"].org_id, account_id=inp.account_id, name=inp.name, stage=inp.stage, amount=inp.amount, currency=inp.currency, owner_id=ctx["user"].id)
        s.add(o); await s.commit(); await s.refresh(o)
        return {"id": o.id}

@router.get("/{oid}")
async def get_opp(oid: str, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        o = await s.get(Opportunity, oid)
        if not o or o.org_id != ctx["membership"].org_id: raise HTTPException(404)
        return {"id":o.id,"name":o.name,"stage":o.stage,"amount":o.amount,"currency":o.currency,"account_id":o.account_id}
