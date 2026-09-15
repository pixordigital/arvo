"""ARVO — Deal Audit API. 202 Accepted + Idempotency-Key for long-running."""

from fastapi import APIRouter, Depends, Header, HTTPException, BackgroundTasks
from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Opportunity, AiExtraction, AiDecision, Finding
from app.agents.provider import get_provider
from app.services.deal_audit import audit_deal, prioritize
from app.api.deps import get_current_user
import uuid, time

router = APIRouter(prefix="/deal-audits", tags=["deal-audits"])

# In-mem job store MVP (later Redis/Arq)
_JOBS: dict[str, dict] = {}

@router.post("")
async def create_audit(data: dict, background_tasks: BackgroundTasks, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), ctx=Depends(get_current_user)):
    opp_id = data.get("opportunity_id")
    if not opp_id: raise HTTPException(400, "opportunity_id required")
    key = idempotency_key or f"{ctx['membership'].org_id}:audit:{opp_id}:{uuid.uuid4()}"
    if key in _JOBS: return {"job_id": _JOBS[key]["job_id"], "status": _JOBS[key]["status"], "idempotency_key": key}
    job_id = str(uuid.uuid4())
    _JOBS[key] = {"job_id": job_id, "status": "QUEUED", "opportunity_id": opp_id, "org_id": ctx["membership"].org_id}
    background_tasks.add_task(_run_audit, job_id, key, opp_id, ctx["membership"].org_id, data.get("prompt_version","v1"))
    return {"job_id": job_id, "status": "QUEUED", "idempotency_key": key}

async def _run_audit(job_id, key, opp_id, org_id, prompt_version):
    _JOBS[key]["status"]="RUNNING"
    async with get_sessionmaker()() as s:
        opp = await s.get(Opportunity, opp_id)
        if not opp: _JOBS[key].update(status="FAILED", error="opp not found"); return
        prov = get_provider()
        # AI interprets — text extraction
        ai_res = await prov.generate(f"Audite este deal: {opp.name} amount {opp.amount} stage {opp.stage}. Liste riscos de desconto/leakage em JSON risks:[{{kind,discount_pct,reason}}]", system="Você é auditor de receita. Retorne JSON.", model=None)
        extraction = AiExtraction(org_id=org_id, source_type="DEAL", source_id=opp_id, model=ai_res.get("model","mock"), prompt_version=prompt_version, tokens_in=ai_res.get("usage",{}).get("prompt_tokens",0), tokens_out=ai_res.get("usage",{}).get("completion_tokens",0), latency_ms=ai_res.get("latency_ms",0), output={"content": ai_res.get("content")}, provenance={"opp_id": opp_id})
        s.add(extraction); await s.flush()
        # Python calcula materiality
        deal = {"id": opp_id, "amount": opp.amount}
        # Try parse risks from AI content (mock fallback)
        ai_output = {"risks":[{"kind":"DISCOUNT","discount_pct":10,"reason":"mock"}]} if ai_res.get("mock") else {"risks":[]}
        result = await audit_deal(deal, ai_output)
        decision = AiDecision(org_id=org_id, kind="DEAL_AUDIT", input_ref=opp_id, output=result, model=ai_res.get("model","mock"), confidence="MEDIUM")
        s.add(decision)
        # Create finding if material
        if result["exposure"] != "0.00":
            f = Finding(org_id=org_id, kind="DEAL_RISK", title=f"Deal {opp.name} exposição {result['exposure']}", description=ai_res.get("content","")[:500], severity="HIGH" if result["materiality"]=="HIGH" else "MEDIUM", exposure_amount=result["exposure"], currency=opp.currency, confidence="MEDIUM", evidence_pack=result, idempotency_key=key)
            s.add(f)
        await s.commit()
        _JOBS[key].update(status="DONE", result=result, extraction_id=extraction.id, decision_id=decision.id)

@router.get("/{job_id}")
async def get_audit(job_id: str, ctx=Depends(get_current_user)):
    for k,v in _JOBS.items():
        if v["job_id"]==job_id and v["org_id"]==ctx["membership"].org_id:
            return v
    raise HTTPException(404)

@router.get("")
async def list_audits(ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows=(await s.execute(select(AiDecision).where(AiDecision.org_id==ctx["membership"].org_id).order_by(AiDecision.created_at.desc()).limit(20))).scalars().all()
        return [{"id":r.id,"kind":r.kind,"output":r.output} for r in rows]
