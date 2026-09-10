"""Repository for ``ProcessedRiskEvent`` — DB-enforced idempotency + the
per-prediction processing-ownership state machine (architecture §10, §11).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import ProcessingStatus
from models.db_models import ProcessedRiskEvent


class RiskEventRepository:
    """CRUD + ownership transitions for ProcessedRiskEvent."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_prediction_id(self, prediction_id) -> Optional[ProcessedRiskEvent]:
        result = await self.session.execute(
            select(ProcessedRiskEvent).where(ProcessedRiskEvent.prediction_id == prediction_id)
        )
        return result.scalar_one_or_none()

    async def try_claim(
        self,
        prediction_id,
        area_id: Optional[str] = None,
        prediction_timestamp: Optional[datetime] = None,
    ) -> bool:
        """Attempt to take ownership of processing this prediction.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` (architecture §10) so that
        concurrent webhook deliveries and/or a reconciliation pass racing the
        webhook can never both process the same ``prediction_id``.

        Returns ``True`` if this call won ownership (row inserted), ``False``
        if another attempt already owns/completed it.
        """
        stmt = (
            pg_insert(ProcessedRiskEvent)
            .values(
                prediction_id=prediction_id,
                area_id=area_id,
                prediction_timestamp=prediction_timestamp,
                status=ProcessingStatus.PROCESSING,
                processing_started_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=[ProcessedRiskEvent.prediction_id])
            .returning(ProcessedRiskEvent.prediction_id)
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    async def reclaim_stale(self, prediction_id, stale_before: datetime) -> bool:
        """Reclaim a prediction stuck in PROCESSING past the timeout
        (architecture §11, §37 "crash during processing"). Uses an atomic
        conditional UPDATE so two reconciliation runners racing each other
        can't both reclaim the same row.
        """
        stmt = (
            update(ProcessedRiskEvent)
            .where(
                ProcessedRiskEvent.prediction_id == prediction_id,
                ProcessedRiskEvent.status.in_([ProcessingStatus.PROCESSING, ProcessingStatus.RETRYABLE]),
                ProcessedRiskEvent.processing_started_at < stale_before,
            )
            .values(
                status=ProcessingStatus.PROCESSING,
                processing_started_at=datetime.now(timezone.utc),
                retry_count=ProcessedRiskEvent.retry_count + 1,
            )
            .returning(ProcessedRiskEvent.prediction_id)
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    async def reclaim_retryable(self, prediction_id) -> bool:
        """Atomically move a RETRYABLE row back to PROCESSING so it can be
        re-driven through ``process_already_claimed_prediction`` (architecture
        §37, §39). Unlike ``reclaim_stale`` this has no time condition -
        RETRYABLE already means "a previous attempt gave up and this is
        eligible for another try" - but it must still be a conditional
        UPDATE (not a blind claim) so two concurrent reconciliation passes
        can't both pick it up.
        """
        stmt = (
            update(ProcessedRiskEvent)
            .where(
                ProcessedRiskEvent.prediction_id == prediction_id,
                ProcessedRiskEvent.status == ProcessingStatus.RETRYABLE,
            )
            .values(status=ProcessingStatus.PROCESSING, processing_started_at=datetime.now(timezone.utc))
            .returning(ProcessedRiskEvent.prediction_id)
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    async def mark_completed(self, prediction_id) -> None:
        await self.session.execute(
            update(ProcessedRiskEvent)
            .where(ProcessedRiskEvent.prediction_id == prediction_id)
            .values(status=ProcessingStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        )

    async def mark_retryable(self, prediction_id, error: str) -> None:
        await self.session.execute(
            update(ProcessedRiskEvent)
            .where(ProcessedRiskEvent.prediction_id == prediction_id)
            .values(status=ProcessingStatus.RETRYABLE, last_error=error[:2000])
        )

    async def mark_failed(self, prediction_id, error: str) -> None:
        await self.session.execute(
            update(ProcessedRiskEvent)
            .where(ProcessedRiskEvent.prediction_id == prediction_id)
            .values(status=ProcessingStatus.FAILED, last_error=error[:2000])
        )

    async def list_stale_processing(self, stale_before: datetime, limit: int = 200) -> list[ProcessedRiskEvent]:
        result = await self.session.execute(
            select(ProcessedRiskEvent)
            .where(
                ProcessedRiskEvent.status == ProcessingStatus.PROCESSING,
                ProcessedRiskEvent.processing_started_at < stale_before,
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_retryable(self, limit: int = 200) -> list[ProcessedRiskEvent]:
        result = await self.session.execute(
            select(ProcessedRiskEvent).where(ProcessedRiskEvent.status == ProcessingStatus.RETRYABLE).limit(limit)
        )
        return list(result.scalars().all())
