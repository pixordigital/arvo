"""ARVO — Deal Audit API. Persistent jobs + Idempotency-Key."""

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select

from app.database.engine import get_sessionmaker
from app.database.models import AiDecision, AiExtraction, DealAuditJob, Finding, Opportunity
from app.agents.provider import get_provider
from app.services.deal_audit import audit_deal, parse_ai_risks
from app.api.deps import get_current_user, pagination
import uuid

router = APIRouter(prefix="/deal-audits", tags=["deal-audits"])


def _job_view(job: DealAuditJob) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "opportunity_id": job.opportunity_id,
        "idempotency_key": job.idempotency_key,
        "result": job.result,
        "error": job.error,
    }


@router.post("")
async def create_audit(
    data: dict,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx=Depends(get_current_user),
):
    opp_id = data.get("opportunity_id")
    if not opp_id:
        raise HTTPException(400, "opportunity_id required")
    org_id = ctx["membership"].org_id
    key = (idempotency_key or f"{org_id}:audit:{opp_id}:{uuid.uuid4()}")[:128]
    async with get_sessionmaker()() as s:
        opportunity = await s.get(Opportunity, opp_id)
        if not opportunity or opportunity.org_id != org_id:
            raise HTTPException(404, "opportunity not found")
        existing = (
            await s.execute(
                select(DealAuditJob).where(
                    DealAuditJob.org_id == org_id, DealAuditJob.idempotency_key == key
                )
            )
        ).scalar_one_or_none()
        if existing:
            return _job_view(existing)
        job = DealAuditJob(
            org_id=org_id, opportunity_id=opp_id, idempotency_key=key, status="QUEUED"
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        job_id = job.id
    background_tasks.add_task(_run_audit, job_id)
    return {"job_id": job_id, "status": "QUEUED", "idempotency_key": key}


async def _run_audit(job_id: str):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with get_sessionmaker()() as s:
        job = await s.get(DealAuditJob, job_id)
        if not job or job.status != "QUEUED":
            return
        job.status = "RUNNING"
        job.updated_at = now
        await s.commit()
        org_id, opp_id = job.org_id, job.opportunity_id
        opp = await s.get(Opportunity, opp_id)
        if not opp or opp.org_id != org_id:
            job.status = "FAILED"
            job.error = "opportunity not found"
            await s.commit()
            return
        try:
            prov = get_provider()
            ai_res = await prov.generate(
                f"Audite este deal: {opp.name} amount {opp.amount} stage {opp.stage}. "
                "Liste riscos de desconto/leakage em JSON risks:[{kind,discount_pct,reason}]",
                system="Você é auditor de receita. Retorne apenas JSON válido.",
                model=None,
            )
            extraction = AiExtraction(
                org_id=org_id,
                source_type="DEAL",
                source_id=opp_id,
                model=ai_res.get("model", ""),
                prompt_version="v1",
                tokens_in=ai_res.get("usage", {}).get("prompt_tokens", 0),
                tokens_out=ai_res.get("usage", {}).get("completion_tokens", 0),
                latency_ms=ai_res.get("latency_ms", 0),
                output={"content": ai_res.get("content")},
                provenance={"opp_id": opp_id},
            )
            s.add(extraction)
            await s.flush()
            result = await audit_deal(
                {"id": opp_id, "amount": opp.amount},
                {"risks": parse_ai_risks(ai_res.get("content", ""))},
            )
            s.add(
                AiDecision(
                    org_id=org_id,
                    kind="DEAL_AUDIT",
                    input_ref=opp_id,
                    output=result,
                    model=ai_res.get("model", ""),
                    confidence="MEDIUM",
                )
            )
            if result["exposure"] != "0.00":
                existing = (
                    await s.execute(
                        select(Finding).where(
                            Finding.org_id == org_id,
                            Finding.idempotency_key == f"{org_id}:audit:{opp_id}",
                        )
                    )
                ).scalar_one_or_none()
                if not existing:
                    s.add(
                        Finding(
                            org_id=org_id,
                            kind="DEAL_RISK",
                            title=f"Deal {opp.name} exposição {result['exposure']}",
                            description=(ai_res.get("content") or "")[:500],
                            severity="HIGH" if result["materiality"] == "HIGH" else "MEDIUM",
                            exposure_amount=result["exposure"],
                            currency=opp.currency,
                            confidence="MEDIUM",
                            evidence_pack=result,
                            idempotency_key=f"{org_id}:audit:{opp_id}",
                        )
                    )
            job.status = "DONE"
            job.result = result
            job.error = None
            await s.commit()
        except Exception as error:
            await s.rollback()
            async with get_sessionmaker()() as s2:
                job2 = await s2.get(DealAuditJob, job_id)
                if job2:
                    job2.status = "FAILED"
                    job2.error = str(error)[:500]
                    await s2.commit()


@router.get("/{job_id}")
async def get_audit(job_id: str, ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        job = await s.get(DealAuditJob, job_id)
        if not job or job.org_id != ctx["membership"].org_id:
            raise HTTPException(404)
        return _job_view(job)


@router.get("")
async def list_audits(page=Depends(pagination), ctx=Depends(get_current_user)):
    async with get_sessionmaker()() as s:
        rows = (
            await s.execute(
                select(DealAuditJob)
                .where(DealAuditJob.org_id == ctx["membership"].org_id)
                .order_by(DealAuditJob.created_at.desc(), DealAuditJob.id.desc())
                .offset(page["offset"]).limit(page["limit"])
            )
        ).scalars().all()
        return [_job_view(r) for r in rows]
