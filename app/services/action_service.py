"""Shared action lifecycle state machine (API + web)."""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.database.engine import get_sessionmaker
from app.database.models import Action, Finding, FindingEvidence, FinancialEvent, FinancialLedger
from app.services.financial import D, idempotency_key, money


async def transition_action(
    *,
    org_id: str,
    user_id: str,
    aid: str,
    transition: str,
    verified_amount: str = "0",
    publish_aios: bool = False,
) -> dict:
    """Apply one action transition. Caller owns authorization; this owns guards and side effects."""
    async with get_sessionmaker()() as s:
        a = await s.get(Action, aid)
        if not a or a.org_id != org_id:
            raise HTTPException(404)
        f = await s.get(Finding, a.finding_id)
        verified = None
        if transition == "approve":
            if a.status != "PENDING_APPROVAL":
                raise HTTPException(400, f"Cannot approve from {a.status}")
            a.status = "APPROVED"
            a.approved_by = user_id
            if f:
                f.status = "ACTION_APPROVED"
        elif transition == "execute":
            if a.status != "APPROVED":
                raise HTTPException(400, f"Need APPROVED, got {a.status}")
            a.status = "EXECUTED"
            a.executed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if f:
                f.status = "ACTION_EXECUTED"
        else:
            if a.status != "EXECUTED":
                raise HTTPException(400, f"Need EXECUTED, got {a.status}")
            if not f:
                raise HTTPException(400, "Action has no finding")
            amount = D(verified_amount)
            if amount <= 0:
                raise HTTPException(400, "verified_amount must be > 0")
            ev_count = (
                await s.execute(
                    select(func.count(FindingEvidence.id)).where(FindingEvidence.finding_id == f.id)
                )
            ).scalar() or 0
            if ev_count == 0 and not f.evidence_pack:
                raise HTTPException(400, "Evidence required before financial verification")
            if amount > D(f.exposure_amount):
                raise HTTPException(400, f"verified_amount exceeds exposure {f.exposure_amount}")
            verified = money(amount)
            a.status = "VERIFIED"
            a.verified_amount = verified
            f.status = "FINANCIALLY_VERIFIED"
            fe = FinancialEvent(
                org_id=org_id,
                kind="RECOVERED",
                amount=verified,
                currency="BRL",
                idempotency_key=idempotency_key(org_id, "verified", aid),
            )
            s.add(fe)
            await s.flush()
            s.add(
                FinancialLedger(
                    org_id=org_id,
                    event_id=fe.id,
                    entry_type="VERIFIED",
                    amount=verified,
                    currency="BRL",
                    description=f"Verified action {aid}",
                )
            )

        if publish_aios:
            from app.core.config import settings
            from app.integrations.aios.publisher import enqueue_outbox

            if getattr(settings, "aios_integration_enabled", False):
                if transition == "execute" and f is not None:
                    await enqueue_outbox(
                        "action.executed",
                        {"org_id": a.org_id, "action_id": a.id, "finding_id": a.finding_id,
                         "deal_id": (f.provenance or {}).get("source_deal_id"),
                         "title": a.title, "exposure_amount": f.exposure_amount},
                        business_trace_id=a.id, idempotency_key=f"exec:{a.id}",
                        session=s,
                    )
                elif transition == "verify":
                    await enqueue_outbox(
                        "action.verified",
                        {"org_id": org_id, "action_id": a.id,
                         "finding_id": a.finding_id, "deal_id": (f.provenance or {}).get("source_deal_id"),
                         "verified_amount": verified, "currency": "BRL"},
                        business_trace_id=a.id, idempotency_key=f"verify:{a.id}",
                        session=s,
                    )
        await s.commit()
        return {"id": a.id, "status": a.status, "verified_amount": verified}
