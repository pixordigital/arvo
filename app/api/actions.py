"""ARVO — Recommendations & Actions lifecycle + Financial Impact."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, cast, case, Numeric
from app.database.engine import get_sessionmaker
from app.database.models import Finding, Recommendation, Action, FinancialLedger
from app.services.action_service import transition_action
from app.services.financial import D, money
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
    r = await transition_action(org_id=ctx["membership"].org_id, user_id=ctx["user"].id, aid=aid, transition="approve", publish_aios=True)
    return {"id": r["id"], "status": r["status"]}

@router.post("/actions/{aid}/execute")
async def execute_action(aid: str, ctx=Depends(get_current_user)):
    from app.database.models import has_capability
    if not has_capability(ctx["membership"].role, "actions:execute"):
        raise HTTPException(403, "Need execution capability")
    r = await transition_action(org_id=ctx["membership"].org_id, user_id=ctx["user"].id, aid=aid, transition="execute", publish_aios=True)
    return {"id": r["id"], "status": r["status"]}

@router.post("/actions/{aid}/verify")
async def verify_action(aid: str, data: dict, ctx=Depends(get_current_user)):
    if ctx["membership"].role not in ("OWNER","ADMIN","MANAGER"): raise HTTPException(403, "Need verification capability")
    amount = D(data.get("verified_amount", "0"))
    if amount <= 0: raise HTTPException(400, "verified_amount must be > 0")
    r = await transition_action(org_id=ctx["membership"].org_id, user_id=ctx["user"].id, aid=aid, transition="verify", verified_amount=data.get("verified_amount", "0"), publish_aios=True)
    return {"id": r["id"], "status": r["status"], "verified_amount": r["verified_amount"]}

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
    org = ctx["membership"].org_id
    async with get_sessionmaker()() as s:
        exposure, verified, ledger_count = (await s.execute(
            select(
                func.coalesce(func.sum(case((FinancialLedger.entry_type == "DEBIT", cast(FinancialLedger.amount, Numeric)), else_=0)), 0),
                func.coalesce(func.sum(case((FinancialLedger.entry_type == "VERIFIED", cast(FinancialLedger.amount, Numeric)), else_=0)), 0),
                func.count(FinancialLedger.id),
            ).where(FinancialLedger.org_id == org)
        )).one()
        findings_exposure, findings_count = (await s.execute(
            select(
                func.coalesce(func.sum(cast(Finding.exposure_amount, Numeric)), 0),
                func.count(Finding.id),
            ).where(Finding.org_id == org)
        )).one()
        return {
            "exposure_ledger": money(exposure),
            "verified": money(verified),
            "findings_exposure": money(findings_exposure),
            "findings_count": findings_count,
            "ledger_count": ledger_count,
        }
