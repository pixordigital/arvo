"""ARVO — Financial Evidence Pack. Consolida source+calc+evidence+provenance."""

from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Finding, FindingEvidence, FinancialEvent, FinancialLedger, AiExtraction

async def build_pack(finding_id: str, org_id: str) -> dict:
    async with get_sessionmaker()() as s:
        f = await s.get(Finding, finding_id)
        if not f or f.org_id != org_id: return {}
        evs = (await s.execute(select(FindingEvidence).where(FindingEvidence.finding_id==finding_id))).scalars().all()
        fe = (await s.execute(select(FinancialEvent).where(FinancialEvent.org_id==org_id).order_by(FinancialEvent.created_at.desc()).limit(5))).scalars().all()
        ex = (await s.execute(select(AiExtraction).where(AiExtraction.source_id==finding_id).limit(1))).scalars().first()
        return {
            "finding": {"id": f.id, "title": f.title, "exposure": f.exposure_amount, "status": f.status, "severity": f.severity},
            "evidences": [{"kind":e.kind,"content":e.content} for e in evs],
            "financial_events": [{"kind":e.kind,"amount":e.amount,"idempotency_key":e.idempotency_key} for e in fe],
            "ai_extraction": {"model": ex.model, "confidence": ex.confidence} if ex else None,
            "provenance": f.provenance,
            "evidence_pack": f.evidence_pack,
        }
