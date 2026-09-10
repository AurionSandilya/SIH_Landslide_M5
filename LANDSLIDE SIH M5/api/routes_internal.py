"""Internal service-to-service API surface (architecture §44).

No public alert APIs live here (those belong to M3, §44). Every route
requires the internal service-key (Phase 8 / §43).
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from alerting.evaluator import process_risk_event
from alerting.lifecycle_actions import LifecycleActionResult, acknowledge_alert, cancel_alert
from api.deps import require_service_key
from core.exceptions import PredictionNotFoundError, ProcessingError
from models.db_models import get_async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal"], dependencies=[Depends(require_service_key)])


class RiskEventPayload(BaseModel):
    """Architecture §7: the webhook is only a trigger - M5 never trusts this
    payload as the prediction itself. ``area_id`` is accepted only as an
    optional hint; the authoritative values always come from re-reading
    ``risk_predictions`` by ``prediction_id``."""

    prediction_id: UUID
    area_id: Optional[str] = None


@router.post("/risk-event", status_code=status.HTTP_202_ACCEPTED)
async def receive_risk_event(payload: RiskEventPayload, session: AsyncSession = Depends(get_async_session)):
    try:
        await process_risk_event(payload.prediction_id, session, area_id_hint=payload.area_id)
        return {"detail": "risk event processed"}
    except PredictionNotFoundError:
        # Architecture §8: not yet visible after retry/backoff - left for
        # reconciliation. 202 is still correct: the webhook was accepted,
        # even though evaluation didn't happen synchronously.
        logger.warning("prediction_id=%s not visible yet; deferring to reconciliation", payload.prediction_id)
        return {"detail": "prediction not yet visible; will be reconciled"}
    except ProcessingError:
        logger.exception("processing error for prediction_id=%s", payload.prediction_id)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unable to process risk event")
    except Exception:
        logger.exception("unexpected failure processing prediction_id=%s", payload.prediction_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Risk event processing failed")


class LifecycleAcknowledgePayload(BaseModel):
    alert_id: UUID


class LifecycleCancelPayload(BaseModel):
    alert_id: UUID
    reason: str = Field(min_length=1, description="Cancellation requires a reason (architecture §31)")


_RESULT_TO_HTTP = {
    LifecycleActionResult.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    LifecycleActionResult.ALREADY_TERMINAL: status.HTTP_409_CONFLICT,
}


@router.post("/lifecycle/acknowledge", status_code=status.HTTP_200_OK)
async def acknowledge(payload: LifecycleAcknowledgePayload, session: AsyncSession = Depends(get_async_session)):
    outcome = await acknowledge_alert(session, payload.alert_id)
    await session.commit()
    if outcome.result in _RESULT_TO_HTTP:
        raise HTTPException(status_code=_RESULT_TO_HTTP[outcome.result], detail=outcome.result.value)
    return {"alert_id": str(outcome.alert_id), "lifecycle_status": outcome.lifecycle_status.value, "result": outcome.result.value}


@router.post("/lifecycle/cancel", status_code=status.HTTP_200_OK)
async def cancel(payload: LifecycleCancelPayload, session: AsyncSession = Depends(get_async_session)):
    outcome = await cancel_alert(session, payload.alert_id, payload.reason)
    await session.commit()
    if outcome.result in _RESULT_TO_HTTP:
        raise HTTPException(status_code=_RESULT_TO_HTTP[outcome.result], detail=outcome.result.value)
    return {"alert_id": str(outcome.alert_id), "lifecycle_status": outcome.lifecycle_status.value, "result": outcome.result.value}
