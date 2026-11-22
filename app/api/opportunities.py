"""ARVO — Opportunities API."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import OPP_STAGES, Opportunity, Account
from app.api.financial import _currency
from app.api.deps import get_current_user, pagination
from app.services.financial import D, money

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

class OppIn(BaseModel):
    account_id: str
    name: str
    stage: str = "PROSPECTING"
    amount: str = "0"
    currency: str = "BRL"

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name required")
        return v[:255]

    @field_validator("stage")
    @classmethod
    def valid_stage(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in OPP_STAGES:
            raise ValueError(f"stage must be one of {OPP_STAGES}")
        return v

    @field_validator("amount")
    @classmethod
    def non_negative_amount(cls, v: str) -> str:
        try:
            amount = D(v)
        except Exception as e:
            raise ValueError("amount must be a decimal number") from e
        if amount < 0:
            raise ValueError("amount must be >= 0")
        return money(amount)

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v: str) -> str:
        return _currency(v)

@router.get("")
async def list_opps(page=Depends(pagination), ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(Opportunity)
            .where(Opportunity.org_id==ctx["membership"].org_id)
            .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
            .offset(page["offset"]).limit(page["limit"])
        )).scalars().all()
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
