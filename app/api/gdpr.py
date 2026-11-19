"""ARVO — LGPD: export, deletion, retention, audit."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete as sa_delete, select
from app.database.engine import get_sessionmaker
from app.database.models import (
    Account, Contact, Opportunity, Finding, FindingEvidence, FinancialEvent,
    FinancialLedger, Membership, Proposal, User,
)
from app.api.deps import get_current_user
from app.services.financial import idempotency_key

router = APIRouter(prefix="/gdpr", tags=["gdpr"])

@router.get("/export")
async def export_data(ctx=Depends(get_current_user)):
    org_id = ctx["membership"].org_id
    async with get_sessionmaker()() as s:
        accs = (await s.execute(select(Account).where(Account.org_id==org_id))).scalars().all()
        contacts = (await s.execute(select(Contact).where(Contact.org_id==org_id))).scalars().all()
        opps = (await s.execute(select(Opportunity).where(Opportunity.org_id==org_id))).scalars().all()
        props = (await s.execute(select(Proposal).where(Proposal.org_id==org_id))).scalars().all()
        findings = (await s.execute(select(Finding).where(Finding.org_id==org_id))).scalars().all()
        events = (await s.execute(select(FinancialEvent).where(FinancialEvent.org_id==org_id))).scalars().all()
        ledger = (await s.execute(select(FinancialLedger).where(FinancialLedger.org_id==org_id))).scalars().all()
        mems = (await s.execute(select(Membership).where(Membership.org_id==org_id))).scalars().all()
        members = []
        for m in mems:
            u = await s.get(User, m.user_id)
            members.append({"user_id": m.user_id, "email": u.email if u else None, "role": m.role, "active": m.is_active})
        return {
            "org_id": org_id,
            "accounts": [{"id":a.id,"name":a.name,"domain":a.domain,"type":a.type} for a in accs],
            "contacts": [{"id":c.id,"name":c.name,"email":c.email,"role":c.role} for c in contacts],
            "opportunities": [{"id":o.id,"name":o.name,"stage":o.stage,"amount":o.amount,"currency":o.currency} for o in opps],
            "proposals": [{"id":p.id,"total_amount":p.total_amount,"currency":p.currency,"status":p.status} for p in props],
            "findings": [{"id":f.id,"title":f.title,"kind":f.kind,"status":f.status,"exposure_amount":f.exposure_amount} for f in findings],
            "financial_events": [{"id":e.id,"kind":e.kind,"amount":e.amount,"currency":e.currency} for e in events],
            "financial_ledger": [{"id":l.id,"entry_type":l.entry_type,"amount":l.amount,"description":l.description} for l in ledger],
            "members": members,
            "notice": "LGPD export — dados do org atual. Retenção padrão 5 anos para ledger/findings.",
        }

@router.delete("/account/{aid}")
async def delete_account(aid: str, ctx=Depends(get_current_user)):
    # Capability check
    if ctx["membership"].role not in ("OWNER","ADMIN"):
        raise HTTPException(403, "Need OWNER/ADMIN")
    org_id = ctx["membership"].org_id
    async with get_sessionmaker()() as s:
        a = await s.get(Account, aid)
        if not a or a.org_id != org_id: raise HTTPException(404)
        # SQLite does not enforce FKs; remove dependants explicitly on every backend.
        await s.execute(sa_delete(Contact).where(Contact.account_id == aid))
        await s.execute(sa_delete(Opportunity).where(Opportunity.account_id == aid))
        await s.delete(a)
        # Audit trail: append an event+ledger row describing the deletion.
        fe = FinancialEvent(
            org_id=org_id, kind="LGPD_DELETION", amount="0", currency="BRL",
            idempotency_key=idempotency_key(org_id, "lgpd-delete", aid),
            provenance={"deleted_account_id": aid, "deleted_by": ctx["user"].id},
        )
        s.add(fe)
        await s.flush()
        s.add(FinancialLedger(
            org_id=org_id, event_id=fe.id, entry_type="DELETION", amount="0", currency="BRL",
            description=f"LGPD account {aid} deleted by {ctx['user'].id}",
        ))
        await s.commit()
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
