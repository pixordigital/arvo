"""ARVO worker — Arq + Redis. MVP: deal_audit jobs via arq; fallback BackgroundTasks if redis down."""
from arq import create_pool, cron
from arq.connections import RedisSettings
from app.core.config import settings

async def audit_job(ctx, opp_id: str, org_id: str, prompt_version: str = "v1"):
    import uuid
    from app.database.engine import get_sessionmaker
    from app.database.models import Opportunity, AiExtraction, AiDecision, Finding
    from app.agents.provider import get_provider
    from app.services.deal_audit import audit_deal
    async with get_sessionmaker()() as s:
        opp = await s.get(Opportunity, opp_id)
        if not opp: return {"error": "opp not found"}
        if opp.org_id != org_id: return {"error": "org mismatch"}
        prov = get_provider()
        ai_res = await prov.generate(f"Audite deal: {opp.name} amount {opp.amount} stage {opp.stage}", system="Você é auditor. Retorne JSON.", model=None)
        ex = AiExtraction(org_id=org_id, source_type="DEAL", source_id=opp_id, model=ai_res.get("model","mock"), prompt_version=prompt_version, output={"content": ai_res.get("content")}, provenance={"opp_id": opp_id})
        s.add(ex); await s.flush()
        ai_output = {"risks":[{"kind":"DISCOUNT","discount_pct":10,"reason":"mock"}]} if ai_res.get("mock") else {"risks":[]}
        result = await audit_deal({"id": opp_id, "amount": opp.amount}, ai_output)
        dec = AiDecision(org_id=org_id, kind="DEAL_AUDIT", input_ref=opp_id, output=result, model=ai_res.get("model","mock"))
        s.add(dec)
        finding_id = None
        outbox_id = None
        business_trace_id = str(uuid.uuid4())
        if result["exposure"] != "0.00":
            f = Finding(org_id=org_id, kind="DEAL_RISK", title=f"Deal {opp.name} exposição {result['exposure']}", severity="HIGH" if result["materiality"]=="HIGH" else "MEDIUM", exposure_amount=result["exposure"], currency=opp.currency, evidence_pack=result, provenance={"business_trace_id": business_trace_id, "opp_id": opp_id})
            s.add(f)
            await s.flush()
            finding_id = f.id
            if settings.aios_integration_enabled and settings.aios_base_url:
                from app.integrations.aios.publisher import enqueue_outbox
                outbox_id = await enqueue_outbox(
                    "commitment.at_risk",
                    {"finding_id": finding_id, "opportunity_id": opp_id, "org_id": org_id, "exposure": result["exposure"], "business_trace_id": business_trace_id},
                    business_trace_id=business_trace_id,
                    session=s,
                )
        await s.commit()
        if outbox_id:
            from app.integrations.aios.publisher import schedule_flush
            await schedule_flush(outbox_id)
        return {**result, "finding_id": finding_id, "business_trace_id": business_trace_id}

def _redis_settings() -> RedisSettings:
    """SASL: merge redis_url + redis_username/password se URL sem credenciais. Normaliza user '' → None."""
    url = settings.redis_url or "redis://localhost:6379/0"
    # se password separado e URL sem senha, injeta
    if settings.redis_password and "@" not in url.split("://", 1)[-1].split("/")[0]:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        user = settings.redis_username or p.username or ""
        # monta netloc com SASL
        auth = ""
        if user and settings.redis_password:
            auth = f"{user}:{settings.redis_password}@"
        elif settings.redis_password:
            auth = f":{settings.redis_password}@"
        elif user:
            auth = f"{user}@"
        netloc = f"{auth}{p.hostname or 'localhost'}:{p.port or 6379}"
        url = urlunparse((p.scheme, netloc, p.path or "/0", "", "", ""))
    s = RedisSettings.from_dsn(url)
    # ponytail: '' → None (AUTH default user usa só password, não user='')
    if s.username == "":
        s.username = None
    return s


async def integration_outbox_flush_job(ctx, outbox_id: str | None = None):
    from app.integrations.aios.publisher import integration_outbox_flush
    return await integration_outbox_flush(ctx, outbox_id=outbox_id)


async def integration_outbox_cron(ctx):
    from app.integrations.aios.publisher import flush_outbox
    await flush_outbox(batch=20)


class WorkerSettings:
    redis_settings = _redis_settings()
    functions = [audit_job, integration_outbox_flush_job]
    cron_jobs = [cron(integration_outbox_cron, second=30)]

async def enqueue_audit(opp_id: str, org_id: str, prompt_version: str = "v1"):
    pool = None
    try:
        pool = await create_pool(_redis_settings())
        job = await pool.enqueue_job("audit_job", opp_id, org_id, prompt_version)
        return job.job_id if job else None
    except Exception:
        return None
    finally:
        if pool is not None:
            await pool.close()

if __name__ == "__main__":
    import arq
    arq.run_worker(WorkerSettings)
