"""ARVO worker — Arq + Redis. MVP: deal_audit jobs via arq; fallback BackgroundTasks if redis down."""
import logging

from arq import create_pool, cron
from arq.connections import RedisSettings
from app.core.config import settings

logger = logging.getLogger(__name__)

_RECONCILE_LIMIT = 100


def _allowed_aios_org_refs() -> list[str]:
    raw = getattr(settings, "aios_allowed_orgs", "") or ""
    return list(dict.fromkeys(v.strip() for v in raw.split(",") if v.strip()))


def _deal_event_type(deal: dict) -> str:
    """Created unless the deal carries evidence of a stage change."""
    previous = deal.get("previous_stage") or deal.get("old_stage")
    if previous and str(previous) != str(deal.get("stage") or ""):
        return "deal.stage_changed"
    if deal.get("new_stage") or (deal.get("created_at") and deal.get("updated_at") and str(deal["created_at"]) != str(deal["updated_at"])):
        return "deal.stage_changed"
    return "deal.created"


async def integration_reconcile(ctx):
    """Pull AIOS deals for provisioned orgs; reuse the existing deal event handler."""
    if not settings.aios_integration_enabled:
        return {"skipped": "disabled"}
    from sqlalchemy import or_, select

    from app.database.engine import get_sessionmaker
    from app.database.models import Organization
    from app.integrations.aios.client import list_deals
    from app.integrations.aios.routes import _handle_deal_event

    try:
        async with get_sessionmaker()() as s:
            allowed = _allowed_aios_org_refs()
            if allowed:
                orgs = (
                    await s.execute(
                        select(Organization).where(
                            or_(Organization.id.in_(allowed), Organization.slug.in_(allowed))
                        )
                    )
                ).scalars().all()
                # allowed values are AIOS org ids/slugs; unmatched values have no ARVO mapping
                pairs = [(org, ref) for ref in allowed for org in orgs if ref in (org.id, org.slug)]
            else:
                orgs = (await s.execute(select(Organization))).scalars().all()
                pairs = [(org, org.id) for org in orgs]
    except Exception:
        logger.exception("aios reconcile: org scan failed")
        return {"orgs": 0, "deals": 0, "errors": 1}

    stats = {"orgs": 0, "deals": 0, "errors": 0}
    for org, ref in pairs:
        stats["orgs"] += 1
        try:
            data = await list_deals(org_id=ref, limit=_RECONCILE_LIMIT)
        except Exception:
            logger.exception("aios reconcile: list_deals failed org=%s", ref)
            stats["errors"] += 1
            continue
        if data.get("error"):
            logger.warning("aios reconcile: peer error org=%s: %s", ref, data["error"])
            stats["errors"] += 1
            continue
        for deal in data.get("deals") or []:
            if not isinstance(deal, dict):
                continue
            deal_id = str(deal.get("id") or deal.get("deal_id") or "unknown")
            try:
                # handler resolves ARVO org via payload org_id (AIOS ref = ARVO id/slug)
                await _handle_deal_event(
                    {**deal, "org_id": ref},
                    f"reconcile:{ref}:{deal_id}"[:64],
                    _deal_event_type(deal),
                )
                stats["deals"] += 1
            except Exception:
                logger.exception("aios reconcile: deal event failed org=%s deal=%s", ref, deal_id)
                stats["errors"] += 1
    return stats


async def audit_job(ctx, job_id: str):
    """Run a persisted DealAuditJob — same code path as the API background task."""
    from app.api.deal_audit import _run_audit
    return await _run_audit(job_id)

def _redis_settings() -> RedisSettings:
    """Redis DSN com credenciais na URL; normaliza user '' → None."""
    s = RedisSettings.from_dsn(settings.redis_url or "redis://localhost:6379/0")
    # ponytail: SASL user '' → None; ceiling = single default Redis user, upgrade when ACL needs per-user AUTH
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
    functions = [audit_job, integration_outbox_flush_job, integration_reconcile]
    cron_jobs = [
        cron(integration_outbox_cron, second=30),
        cron(integration_reconcile, minute=set(range(0, 60, 5)), second=0),
    ]

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
