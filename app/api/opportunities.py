"""ARVO — Opportunities API."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from app.core.config import settings
from app.database.engine import get_sessionmaker
from app.database.models import OPP_STAGES, Opportunity, Account
from app.api.financial import _currency
from app.api.deps import get_current_user, pagination
from app.services.financial import D, money

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
logger = logging.getLogger(__name__)

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

async def _shared_deals(org_id: str, limit: int, offset: int = 0) -> list[dict]:
    """AIOS CRM deals mapped to ARVO rows. Never falls back to local deals."""
    from app.integrations.aios.client import list_deals

    try:
        # shortcut: AIOS caps reads at 500; deeper offsets stay empty until peer pagination exists.
        data = await list_deals(org_id=org_id, limit=min(500, limit + offset))
    except Exception:
        logger.exception("aios deals: list_deals failed org=%s", org_id)
        return []
    if not isinstance(data, dict) or data.get("error"):
        logger.warning(
            "aios deals: peer error org=%s: %s",
            org_id,
            data.get("error") if isinstance(data, dict) else "invalid response",
        )
        return []
    rows = data.get("deals") or []
    if not isinstance(rows, list):
        logger.warning("aios deals: invalid deals payload org=%s", org_id)
        return []
    return [
        {
            "id": d.get("id"),
            "name": d.get("lead_name"),
            "stage": d.get("stage"),
            "amount": d.get("value"),
            "currency": d.get("currency"),
        }
        for d in rows[offset : offset + limit]
        # shortcut: skip rows with no usable id; templates slice o.id[:8] and would 500.
        if isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"]
    ]


@router.get("")
async def list_opps(page=Depends(pagination), ctx=Depends(get_current_user)):
    if settings.aios_integration_enabled:
        return await _shared_deals(ctx["membership"].org_id, page["limit"], page["offset"])
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
    if settings.aios_integration_enabled:
        raise HTTPException(
            409,
            "Deals are managed in AIOS (source of truth); local creation is disabled while integration is enabled.",
        )
    async with get_sessionmaker()() as s:
        acc = await s.get(Account, inp.account_id)
        if not acc or acc.org_id != ctx["membership"].org_id: raise HTTPException(404, "Account not found")
        o = Opportunity(org_id=ctx["membership"].org_id, account_id=inp.account_id, name=inp.name, stage=inp.stage, amount=inp.amount, currency=inp.currency, owner_id=ctx["user"].id)
        s.add(o); await s.commit(); await s.refresh(o)
        return {"id": o.id}

@router.get("/{oid}")
async def get_opp(oid: str, ctx=Depends(get_current_user)):
    if settings.aios_integration_enabled:
        # shortcut: AIOS caps reads at 500; deeper ids stay 404 until peer pagination exists.
        for row in await _shared_deals(ctx["membership"].org_id, 500):
            if row["id"] == oid:
                return row
        raise HTTPException(404)
    async with get_sessionmaker()() as s:
        o = await s.get(Opportunity, oid)
        if not o or o.org_id != ctx["membership"].org_id: raise HTTPException(404)
        return {"id":o.id,"name":o.name,"stage":o.stage,"amount":o.amount,"currency":o.currency,"account_id":o.account_id}
