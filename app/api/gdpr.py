"""ARVO — LGPD: export, deletion, retention, audit."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Account, Contact, Opportunity, Finding, FinancialLedger
from app.api.deps import get_current_user

router = APIRouter(prefix="/gdpr", tags=["gdpr"])

@router.get("/export")
async def export_data(ctx=Depends(get_current_user)):
    org_id = ctx["membership"].org_id
    async with get_sessionmaker()() as s:
        accs = (await s.execute(select(Account).where(Account.org_id==org_id))).scalars().all()
        contacts = (await s.execute(select(Contact).where(Contact.org_id==org_id))).scalars().all()
        return {
            "org_id": org_id,
            "accounts": [{"id":a.id,"name":a.name,"domain":a.domain} for a in accs],
            "contacts": [{"id":c.id,"name":c.name,"email":c.email} for c in contacts],
            "notice": "LGPD export — dados minimizados ao org atual. Retenção padrão 5 anos."
        }

@router.delete("/account/{aid}")
async def delete_account(aid: str, ctx=Depends(get_current_user)):
    # Capability check
    if ctx["membership"].role not in ("OWNER","ADMIN"):
        raise HTTPException(403, "Need OWNER/ADMIN")
    async with get_sessionmaker()() as s:
        a = await s.get(Account, aid)
        if not a or a.org_id != ctx["membership"].org_id: raise HTTPException(404)
        await s.delete(a); await s.commit()
        return {"deleted": aid, "audit": f"Account {aid} deleted by {ctx['user'].id}"}

@router.get("/retention")
async def retention_policy(ctx=Depends(get_current_user)):
    return {
        "policy": "LGPD: minimização, retenção 5 anos para ledger/finding, export/delete via /gdpr/export e DELETE /gdpr/account/{id}, auditável via ledger",
        "retention_years": 5,
        "processor": "ARVO (controlador: organização)",
        "subprocessors": ["Supabase (Postgres)", "OpenRouter (LLM)"],
    }

@router.get("/audit")
async def audit_log(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(FinancialLedger).where(FinancialLedger.org_id==ctx["membership"].org_id).order_by(FinancialLedger.created_at.desc()).limit(20))).scalars().all()
        return [{"id":r.id,"entry_type":r.entry_type,"amount":r.amount,"created_at": r.created_at.isoformat()} for r in rows]
