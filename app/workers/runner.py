"""ARVO worker — Arq + Redis. MVP: deal_audit jobs via arq; fallback BackgroundTasks if redis down."""
from arq import create_pool, cron
from arq.connections import RedisSettings
from app.core.config import settings

async def audit_job(ctx, job_id: str):
    """Run a persisted DealAuditJob — same code path as the API background task."""
    from app.api.deal_audit import _run_audit
    return await _run_audit(job_id)

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
    """Queue a persisted DealAuditJob. Returns its id, or None when Redis is down."""
    import uuid

    from app.database.engine import get_sessionmaker
    from app.database.models import DealAuditJob

    async with get_sessionmaker()() as s:
        job = DealAuditJob(
            org_id=org_id,
            opportunity_id=opp_id,
            idempotency_key=f"{org_id}:audit:{opp_id}:{uuid.uuid4()}"[:128],
            status="QUEUED",
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        job_id = job.id
    pool = None
    try:
        pool = await create_pool(_redis_settings())
        await pool.enqueue_job("audit_job", job_id)
        return job_id
    except Exception:
        async with get_sessionmaker()() as s:
            stored = await s.get(DealAuditJob, job_id)
            if stored:
                await s.delete(stored)
                await s.commit()
        return None
    finally:
        if pool is not None:
            await pool.close()

if __name__ == "__main__":
    import arq
    arq.run_worker(WorkerSettings)
