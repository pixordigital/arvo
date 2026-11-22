"""Outbox publisher — pending → HTTP to AIOS with retry."""

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from app.core.config import settings
from app.database.engine import get_sessionmaker
from app.database.models import IntegrationEvent, IntegrationOutbox

from .events import AiosEvent

logger = logging.getLogger(__name__)


async def enqueue_outbox(
    event_type: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
    business_trace_id: str | None = None,
    event_version: str = "1",
    session=None,
) -> str:
    envelope = AiosEvent(
        type=event_type,
        payload=payload,
        business_trace_id=business_trace_id,
        event_version=event_version,
    )
    outbox_id = str(uuid.uuid4())
    row = IntegrationOutbox(
        id=outbox_id,
        peer="aios",
        event_type=event_type,
        payload={**envelope.payload, "event_version": envelope.event_version},
        business_trace_id=business_trace_id,
        idempotency_key=idempotency_key or outbox_id,
        status="pending",
    )
    if session is not None:
        session.add(row)
        await session.flush()
        return outbox_id
    async with get_sessionmaker()() as db:
        db.add(row)
        await db.commit()
    await schedule_flush(outbox_id)
    return outbox_id


async def schedule_flush(outbox_id: str) -> None:
    if os.getenv("ARVO_USE_ARQ_WORKER", "").strip().lower() not in {"1", "true", "yes"}:
        return
    pool = None
    try:
        from arq import create_pool

        from app.workers.runner import _redis_settings

        pool = await create_pool(_redis_settings())
        await pool.enqueue_job("integration_outbox_flush_job", outbox_id)
    except Exception:
        logger.debug(
            "outbox %s immediate flush unavailable; cron will retry",
            outbox_id[:8],
            exc_info=True,
        )
    finally:
        if pool is not None:
            await pool.close()


async def _claim_rows(
    batch: int, outbox_id: str | None = None
) -> list[IntegrationOutbox]:
    stale_before = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    async with get_sessionmaker()() as db:
        await db.execute(
            update(IntegrationOutbox)
            .where(
                IntegrationOutbox.status == "processing",
                IntegrationOutbox.updated_at < stale_before,
            )
            .values(status="pending")
        )
        if outbox_id:
            candidate_ids = [outbox_id]
        else:
            ids = await db.scalars(
                select(IntegrationOutbox.id)
                .where(IntegrationOutbox.status == "pending")
                .order_by(IntegrationOutbox.created_at)
                .limit(batch)
            )
            candidate_ids = list(ids)
        if not candidate_ids:
            return []
        result = await db.execute(
            update(IntegrationOutbox)
            .where(
                IntegrationOutbox.id.in_(candidate_ids),
                IntegrationOutbox.status == "pending",
            )
            .values(status="processing", attempts=IntegrationOutbox.attempts + 1)
            .returning(IntegrationOutbox.id)
        )
        claimed_ids = list(result.scalars())
        await db.commit()
        if not claimed_ids:
            return []
        rows = list(
            await db.scalars(
                select(IntegrationOutbox).where(
                    IntegrationOutbox.id.in_(claimed_ids)
                )
            )
        )
        order = {rid: index for index, rid in enumerate(claimed_ids)}
        return sorted(rows, key=lambda row: order[row.id])


async def _persist_outcomes(rows: list[IntegrationOutbox]) -> None:
    """One session for all terminal/outcome updates of a claimed batch."""
    if not rows:
        return
    async with get_sessionmaker()() as db:
        current_rows = await db.scalars(
            select(IntegrationOutbox).where(
                IntegrationOutbox.id.in_([row.id for row in rows]),
                IntegrationOutbox.status == "processing",
            )
        )
        by_id = {current.id: current for current in current_rows}
        for row in rows:
            current = by_id.get(row.id)
            if current is None:
                continue
            current.status = row.status
            current.last_error = row.last_error
            if row.status == "sent":
                current.sent_at = row.sent_at
        await db.commit()


async def _notify_exhausted(row: IntegrationOutbox, error: Exception) -> None:
    """Log and annotate the source event; never stop the rest of the batch."""
    logger.error(
        "integration outbox %s gave up after %d attempts (event=%s): %s",
        row.id[:8], row.attempts, row.event_type, error,
    )
    try:
        async with get_sessionmaker()() as db:
            await db.execute(
                update(IntegrationEvent)
                .where(IntegrationEvent.idempotency_key == row.idempotency_key)
                .values(response={
                    "delivered": False,
                    "reason": f"outbox exhausted after {row.attempts} attempts",
                })
            )
            await db.commit()
    except Exception:
        logger.debug("could not annotate integration event", exc_info=True)


async def _deliver(row: IntegrationOutbox, raise_errors: bool) -> bool:
    from app.integrations.aios.client import send_event

    try:
        await send_event(
            row.event_type,
            row.payload,
            idempotency_key=row.idempotency_key,
            business_trace_id=row.business_trace_id,
            event_version=row.payload.get("event_version", "1"),
        )
    except Exception as error:
        retryable = (
            not isinstance(error, httpx.HTTPStatusError)
            or error.response.status_code in (408, 429)
            or error.response.status_code >= 500
        )
        row.status = "pending" if retryable and row.attempts < 5 else "failed"
        row.last_error = str(error)[:500]
        if row.status == "failed":
            await _notify_exhausted(row, error)
        if raise_errors:
            raise
        logger.warning(
            "outbox %s attempt %d failed: %s", row.id[:8], row.attempts, error
        )
        return False
    row.status = "sent"
    row.sent_at = datetime.now(UTC).replace(tzinfo=None)
    row.last_error = None
    return True


async def flush_outbox(batch: int = 20) -> dict:
    if not settings.aios_integration_enabled or not settings.aios_base_url:
        return {"skipped": True, "reason": "integration disabled"}
    rows = await _claim_rows(batch)
    sent = 0
    failed = 0
    exhausted = 0
    for row in rows:
        if await _deliver(row, raise_errors=False):
            sent += 1
        else:
            failed += 1
            if row.status == "failed":
                exhausted += 1
    await _persist_outcomes(rows)
    if exhausted:
        logger.error("integration outbox: %d event(s) permanently undeliverable", exhausted)
    return {"sent": sent, "failed": failed, "exhausted": exhausted}


async def integration_outbox_flush(
    ctx,
    outbox_id: str | None = None,
    batch: int = 20,
):
    if not settings.aios_integration_enabled or not settings.aios_base_url:
        return {"skipped": True}
    if outbox_id:
        rows = await _claim_rows(1, outbox_id=outbox_id)
        if not rows:
            return {"skipped": True}
        try:
            await _deliver(rows[0], raise_errors=True)
        finally:
            await _persist_outcomes(rows)
        return {"sent": 1}
    return await flush_outbox(batch=batch)
