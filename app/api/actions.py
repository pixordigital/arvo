"""ARVO — Recommendations & Actions lifecycle + Financial Impact."""

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from sqlalchemy import select, func
from app.database.engine import get_sessionmaker
from app.database.models import Finding, FindingEvidence, Recommendation, Action, FinancialLedger, FinancialEvent
from app.services.financial import D, idempotency_key, money
from app.api.deps import get_current_user, pagination

router = APIRouter(tags=["actions"])

@router.post("/recommendations")
async def create_rec(data: dict, ctx=Depends(get_current_user)):
    fid=data.get("finding_id")
    async with get_sessionmaker()() as s:
        f=await s.get(Finding, fid)
        if not f or f.org_id!=ctx["membership"].org_id: raise HTTPException(404)
        r=Recommendation(org_id=ctx["membership"].org_id, finding_id=fid, title=data.get("title") or f"Recomendação para {f.title}", description=data.get("description"), priority=data.get("priority","MEDIUM"))
        s.add(r); f.status="ACTION_PLANNED"; await s.commit(); await s.refresh(r)
        return {"id": r.id, "finding_id": fid}

@router.get("/recommendations")
async def list_recs(page=Depends(pagination), ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows=(await s.execute(
            select(Recommendation)
            .where(Recommendation.org_id==ctx["membership"].org_id)
            .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
            .offset(page["offset"]).limit(page["limit"])
        )).scalars().all()
        return [{"id":r.id,"finding_id":r.finding_id,"title":r.title,"priority":r.priority,"status":r.status} for r in rows]

@router.post("/actions")
async def create_action(data: dict, ctx=Depends(get_current_user)):
    fid=data.get("finding_id")
    rid=data.get("recommendation_id")
    async with get_sessionmaker()() as s:
        f=await s.get(Finding, fid)
        if not f or f.org_id!=ctx["membership"].org_id: raise HTTPException(404)
        a=Action(org_id=ctx["membership"].org_id, finding_id=fid, recommendation_id=rid, title=data.get("title") or f"Ação para {f.title}", status="PENDING_APPROVAL")
        s.add(a); f.status="ACTION_PLANNED"; await s.commit(); await s.refresh(a)
        return {"id": a.id, "status": a.status}

@router.post("/actions/{aid}/approve")
async def approve_action(aid: str, ctx=Depends(get_current_user)):
    if ctx["membership"].role not in ("OWNER","ADMIN","MANAGER"): raise HTTPException(403, "Need approval capability")
    async with get_sessionmaker()() as s:
        a=await s.get(Action, aid)
        if not a or a.org_id!=ctx["membership"].org_id: raise HTTPException(404)
        if a.status!="PENDING_APPROVAL": raise HTTPException(400, f"Cannot approve from {a.status}")
        a.status="APPROVED"; a.approved_by=ctx["user"].id
        f=await s.get(Finding, a.finding_id)
        if f: f.status="ACTION_APPROVED"
        await s.commit()
        return {"id": a.id, "status": a.status}

@router.post("/actions/{aid}/execute")
async def execute_action(aid: str, ctx=Depends(get_current_user)):
    from app.database.models import has_capability
    if not has_capability(ctx["membership"].role, "actions:execute"):
        raise HTTPException(403, "Need execution capability")
    async with get_sessionmaker()() as s:
        a=await s.get(Action, aid)
        if not a or a.org_id!=ctx["membership"].org_id: raise HTTPException(404)
        if a.status!="APPROVED": raise HTTPException(400, f"Need APPROVED, got {a.status}")
        a.status="EXECUTED"; a.executed_at=datetime.now(timezone.utc).replace(tzinfo=None)
        f=await s.get(Finding, a.finding_id)
        if f: f.status="ACTION_EXECUTED"
        await s.commit()
        # Bridge to AIOS: action executed -> agent can stop chasing / update deal stage.
        try:
            from app.core.config import settings as _s
            if getattr(_s, "aios_integration_enabled", False) and f is not None:
                from app.integrations.aios.publisher import enqueue_outbox
                await enqueue_outbox(
                    "action.executed",
                    {"org_id": a.org_id, "action_id": a.id, "finding_id": a.finding_id,
                     "title": a.title, "exposure_amount": f.exposure_amount},
                    business_trace_id=a.id, idempotency_key=f"exec:{a.id}",
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("aios outbox enqueue skipped: %s", e)
        return {"id": a.id, "status": a.status}

@router.post("/actions/{aid}/verify")
async def verify_action(aid: str, data: dict, ctx=Depends(get_current_user)):
    if ctx["membership"].role not in ("OWNER","ADMIN","MANAGER"): raise HTTPException(403, "Need verification capability")
    amount = D(data.get("verified_amount", "0"))
    if amount <= 0: raise HTTPException(400, "verified_amount must be > 0")
    async with get_sessionmaker()() as s:
        a=await s.get(Action, aid)
        if not a or a.org_id!=ctx["membership"].org_id: raise HTTPException(404)
        if a.status!="EXECUTED": raise HTTPException(400, f"Need EXECUTED, got {a.status}")
        f=await s.get(Finding, a.finding_id)
        if not f: raise HTTPException(400, "Action has no finding")
        ev_count=(await s.execute(select(func.count(FindingEvidence.id)).where(FindingEvidence.finding_id==f.id))).scalar() or 0
        if ev_count == 0 and not f.evidence_pack:
            raise HTTPException(400, "Evidence required before financial verification")
        if amount > D(f.exposure_amount):
            raise HTTPException(400, f"verified_amount exceeds exposure {f.exposure_amount}")
        verified = money(amount)
        a.status="VERIFIED"; a.verified_amount=verified
        f.status="FINANCIALLY_VERIFIED"
        # ledger VERIFIED credit
        fe=FinancialEvent(org_id=ctx["membership"].org_id, kind="RECOVERED", amount=verified, currency="BRL", idempotency_key=idempotency_key(ctx["membership"].org_id,"verified",aid))
        s.add(fe); await s.flush()
        fl=FinancialLedger(org_id=ctx["membership"].org_id, event_id=fe.id, entry_type="VERIFIED", amount=verified, currency="BRL", description=f"Verified action {aid}")
        s.add(fl)
        await s.commit()
        # Bridge to AIOS: verified recovery -> update deal + ledger proof.
        try:
            from app.core.config import settings as _s
            if getattr(_s, "aios_integration_enabled", False):
                from app.integrations.aios.publisher import enqueue_outbox
                await enqueue_outbox(
                    "action.verified",
                    {"org_id": ctx["membership"].org_id, "action_id": a.id,
                     "finding_id": a.finding_id, "verified_amount": verified,
                     "currency": "BRL"},
                    business_trace_id=a.id, idempotency_key=f"verify:{a.id}",
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("aios outbox enqueue skipped: %s", e)
        return {"id": a.id, "status": a.status, "verified_amount": verified}

@router.get("/actions")
async def list_actions(page=Depends(pagination), ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows=(await s.execute(
            select(Action)
            .where(Action.org_id==ctx["membership"].org_id)
            .order_by(Action.created_at.desc(), Action.id.desc())
            .offset(page["offset"]).limit(page["limit"])
        )).scalars().all()
        return [{"id":r.id,"finding_id":r.finding_id,"title":r.title,"status":r.status,"verified_amount":r.verified_amount} for r in rows]

@router.get("/financial-impact")
async def financial_impact(ctx=Depends(get_current_user)):
    from app.services.financial import sum_amounts
    async with get_sessionmaker()() as s:
        deb_vals = (await s.execute(select(FinancialLedger.amount).where(FinancialLedger.org_id==ctx["membership"].org_id, FinancialLedger.entry_type=="DEBIT"))).scalars().all()
        exposure = sum_amounts(deb_vals)
        ver_vals = (await s.execute(select(FinancialLedger.amount).where(FinancialLedger.org_id==ctx["membership"].org_id, FinancialLedger.entry_type=="VERIFIED"))).scalars().all()
        verified = sum_amounts(ver_vals)
        findings=(await s.execute(select(Finding).where(Finding.org_id==ctx["membership"].org_id))).scalars().all()
        total_exp=sum_amounts([f.exposure_amount for f in findings])
        return {"exposure_ledger": exposure, "verified": verified, "findings_exposure": total_exp, "findings_count": len(findings), "ledger_count": (await s.execute(select(func.count(FinancialLedger.id)).where(FinancialLedger.org_id==ctx["membership"].org_id))).scalar()}
