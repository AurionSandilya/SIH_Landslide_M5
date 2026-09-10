"""Read-only access to M3's ``risk_predictions`` table (architecture §7).

M5 never trusts a webhook payload as the prediction itself - it always
re-reads the committed row. If the row isn't yet visible (replication lag /
transaction visibility race between M1's commit and M3's webhook firing),
architecture §8 requires a bounded retry with backoff (2s, 4s, 8s) before
giving up and leaving the event for reconciliation.
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.external_models import RiskPrediction

logger = logging.getLogger(__name__)

_RETRY_BACKOFF_SECONDS = [2, 4, 8]


class RiskPredictionRepository:
    """Read-only repository for the M3-owned ``risk_predictions`` table."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, prediction_id) -> Optional[RiskPrediction]:
        result = await self.session.execute(
            select(RiskPrediction).where(RiskPrediction.prediction_id == prediction_id)
        )
        return result.scalar_one_or_none()

    async def get_with_retry(self, prediction_id) -> Optional[RiskPrediction]:
        """Re-read the committed prediction, retrying per §8 if not yet
        visible. Returns ``None`` (never raises) if it is still missing
        after the full backoff schedule - the caller must leave the event
        for reconciliation rather than inventing a decision.
        """
        prediction = await self.get(prediction_id)
        if prediction is not None:
            return prediction
        for delay in _RETRY_BACKOFF_SECONDS:
            logger.info("prediction_id=%s not yet visible, retrying in %ss", prediction_id, delay)
            await asyncio.sleep(delay)
            prediction = await self.get(prediction_id)
            if prediction is not None:
                return prediction
        return None

    async def list_since(self, area_id: Optional[str], since, limit: int = 500) -> list[RiskPrediction]:
        """Used by reconciliation to find recent predictions in a lookback
        window (architecture §42), optionally scoped to one area."""
        stmt = select(RiskPrediction).where(RiskPrediction.created_at >= since).order_by(
            RiskPrediction.prediction_timestamp.asc(), RiskPrediction.created_at.asc(), RiskPrediction.prediction_id.asc()
        ).limit(limit)
        if area_id is not None:
            stmt = stmt.where(RiskPrediction.area_id == area_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
