"""Outbox publisher — pending → HTTP to AIOS with retry."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update

from app.core.config import settings
from app.database.engine import get_sessionmaker
from app.database.models import IntegrationOutbox

logger = logging.getLogger(__name__)


async def enqueue_outbox(
    event_type: str,
    payload: dict,
    business_trace_id: str | None = None,
    peer: str = "aios",
    session=None,
) -> str:
    key = str(uuid.uuid4())
    row = IntegrationOutbox(
        id=key,
        peer=peer,
        event_type=event_type,
        payload=payload,
        business_trace_id=business_trace_id,
        idempotency_key=key,
        status="pending",
    )
    if session is not None:
        session.add(row)
        await session.flush()
        return key
    async with get_sessionmaker()() as db:
        db.add(row)
        await db.commit()
    await schedule_flush(key)
    return key


async def schedule_flush(outbox_id: str) -> None:
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
        claimed_ids = []
        for candidate_id in candidate_ids:
            result = await db.execute(
                update(IntegrationOutbox)
                .where(
                    IntegrationOutbox.id == candidate_id,
                    IntegrationOutbox.status == "pending",
                )
                .values(status="processing", attempts=IntegrationOutbox.attempts + 1)
            )
            if result.rowcount:
                claimed_ids.append(candidate_id)
        await db.commit()
        if not claimed_ids:
            return []
        return list(
            await db.scalars(
                select(IntegrationOutbox).where(IntegrationOutbox.id.in_(claimed_ids))
            )
        )


async def _deliver(row: IntegrationOutbox, raise_errors: bool) -> bool:
    from app.integrations.aios.client import send_event

    try:
        await send_event(
            row.event_type,
            row.payload,
            idempotency_key=row.idempotency_key,
        )
    except Exception as error:
        retryable = (
            not isinstance(error, httpx.HTTPStatusError)
            or error.response.status_code in (408, 429)
            or error.response.status_code >= 500
        )
        status = "pending" if retryable and row.attempts < 5 else "failed"
        async with get_sessionmaker()() as db:
            current = await db.get(IntegrationOutbox, row.id)
            if current and current.status == "processing":
                current.status = status
                current.last_error = str(error)[:500]
                await db.commit()
        if raise_errors:
            raise
        logger.warning(
            "outbox %s attempt %d failed: %s", row.id[:8], row.attempts, error
        )
        return False
    async with get_sessionmaker()() as db:
        current = await db.get(IntegrationOutbox, row.id)
        if current and current.status == "processing":
            current.status = "sent"
            current.sent_at = datetime.now(UTC).replace(tzinfo=None)
            current.last_error = None
            await db.commit()
    return True


async def flush_outbox(batch: int = 20) -> dict:
    if not settings.aios_integration_enabled or not settings.aios_base_url:
        return {"skipped": True, "reason": "integration disabled"}
    rows = await _claim_rows(batch)
    sent = 0
    failed = 0
    for row in rows:
        if await _deliver(row, raise_errors=False):
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed}


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
        await _deliver(rows[0], raise_errors=True)
        return {"sent": 1}
    return await flush_outbox(batch=batch)
