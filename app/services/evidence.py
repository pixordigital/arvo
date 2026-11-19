"""ARVO — Financial Evidence Pack. Only records actually linked to the finding."""

from sqlalchemy import select
from app.database.engine import get_sessionmaker
from app.database.models import Finding, FindingEvidence

async def build_pack(finding_id: str, org_id: str) -> dict:
    async with get_sessionmaker()() as s:
        f = await s.get(Finding, finding_id)
        if not f or f.org_id != org_id:
            return {}
        evs = (await s.execute(
            select(FindingEvidence).where(FindingEvidence.finding_id == finding_id)
        )).scalars().all()
        return {
            "finding": {
                "id": f.id,
                "title": f.title,
                "exposure": f.exposure_amount,
                "status": f.status,
                "severity": f.severity,
            },
            "evidences": [{"kind": e.kind, "content": e.content} for e in evs],
            "provenance": f.provenance,
            "evidence_pack": f.evidence_pack,
        }
