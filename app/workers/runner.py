"""ARVO worker — Arq + Redis. MVP: deal_audit jobs via arq; fallback BackgroundTasks if redis down."""
import asyncio
from arq import create_pool
from arq.connections import RedisSettings
from app.core.config import settings

async def audit_job(ctx, opp_id: str, org_id: str, prompt_version: str = "v1"):
    from app.database.engine import get_sessionmaker
    from app.database.models import Opportunity, AiExtraction, AiDecision, Finding
    from app.agents.provider import get_provider
    from app.services.deal_audit import audit_deal
    async with get_sessionmaker()() as s:
        opp = await s.get(Opportunity, opp_id)
        if not opp: return {"error": "opp not found"}
        prov = get_provider()
        ai_res = await prov.generate(f"Audite deal: {opp.name} amount {opp.amount} stage {opp.stage}", system="Você é auditor. Retorne JSON.", model=None)
        ex = AiExtraction(org_id=org_id, source_type="DEAL", source_id=opp_id, model=ai_res.get("model","mock"), prompt_version=prompt_version, output={"content": ai_res.get("content")}, provenance={"opp_id": opp_id})
        s.add(ex); await s.flush()
        ai_output = {"risks":[{"kind":"DISCOUNT","discount_pct":10,"reason":"mock"}]} if ai_res.get("mock") else {"risks":[]}
        result = await audit_deal({"id": opp_id, "amount": opp.amount}, ai_output)
        dec = AiDecision(org_id=org_id, kind="DEAL_AUDIT", input_ref=opp_id, output=result, model=ai_res.get("model","mock"))
        s.add(dec)
        if result["exposure"] != "0.00":
            f = Finding(org_id=org_id, kind="DEAL_RISK", title=f"Deal {opp.name} exposição {result['exposure']}", severity="HIGH" if result["materiality"]=="HIGH" else "MEDIUM", exposure_amount=result["exposure"], currency=opp.currency, evidence_pack=result)
            s.add(f)
        await s.commit()
        return result

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url) if settings.redis_url else RedisSettings()
    functions = [audit_job]

async def enqueue_audit(opp_id: str, org_id: str, prompt_version: str = "v1"):
    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        job = await pool.enqueue_job("audit_job", opp_id, org_id, prompt_version)
        await pool.close()
        return job.job_id if job else None
    except Exception as e:
        # redis down → caller should fallback to BackgroundTasks
        return None

if __name__ == "__main__":
    import arq
    arq.run_worker(WorkerSettings)
