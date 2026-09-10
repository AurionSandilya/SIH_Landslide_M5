"""Reconciliation (architecture §41, §42, invariant #15).

Runs on a timer (~5 min, configurable) and performs four independent
recovery duties. Prediction reconciliation feeds the EXACT SAME
``alerting.evaluator.process_risk_event`` pipeline the webhook uses - there
is deliberately no second alerting implementation.

Idempotency safety (§42): rather than a fragile single
``last_processed_timestamp`` cursor (which loses events if a crash happens
between processing and checkpointing), this uses a small lookback window
every cycle. Repeated overlapping passes are safe because
``processed_risk_events`` / delivery uniqueness constraints make re-driving
already-completed work a no-op.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from alerting.evaluator import process_already_claimed_prediction, process_risk_event
from alerting.state_machine import apply_expiration, is_stale
from core.enums import (
    AuditEventType,
    LifecycleStatus,
    NotificationIntentStatus,
    OPEN_LIFECYCLE_STATUSES,
    ProcessingStatus,
)
from core.exceptions import PredictionNotFoundError
from models.db_models import Alert, NotificationIntent, async_session_factory
from models.external_models import RiskPrediction
from notifications.dispatcher import dispatch_pending_batch
from repository.alert_repo import AlertRepository
from repository.audit_repo import AuditRepository
from repository.config_repo import ConfigRepository
from repository.notification_repo import NotificationRepository
from repository.risk_event_repo import RiskEventRepository

logger = logging.getLogger(__name__)


async def reconcile_unprocessed_predictions() -> int:
    """Find predictions that either have no ``processed_risk_events`` row at
    all, or are stuck RETRYABLE, within the configured lookback window, and
    feed each through the same pipeline the webhook uses."""
    async with async_session_factory() as session:
        config_repo = ConfigRepository(session)
        lookback_seconds = await config_repo.get_int("reconciliation.lookback_seconds", default=1800)
        since = datetime.now(timezone.utc) - timedelta(seconds=lookback_seconds)

        result = await session.execute(
            select(RiskPrediction.prediction_id).where(RiskPrediction.created_at >= since)
        )
        recent_ids = [row[0] for row in result.all()]

    processed = 0
    for prediction_id in recent_ids:
        async with async_session_factory() as session:
            risk_repo = RiskEventRepository(session)
            existing = await risk_repo.get_by_prediction_id(prediction_id)
            if existing is not None and existing.status in (ProcessingStatus.COMPLETED, ProcessingStatus.PROCESSING):
                # PROCESSING is handled separately by reconcile_stale_processing
                # (it may be a live in-flight webhook, not something to steal).
                continue
            try:
                if existing is not None and existing.status == ProcessingStatus.RETRYABLE:
                    # A previous attempt already owns this row - reclaim it
                    # via UPDATE rather than calling process_risk_event's
                    # try_claim, which would hit the existing row (INSERT
                    # ON CONFLICT DO NOTHING) and silently no-op instead of
                    # actually retrying (the same bug class fixed for stale
                    # PROCESSING recovery).
                    won = await risk_repo.reclaim_retryable(prediction_id)
                    await session.commit()
                    if not won:
                        continue
                    await process_already_claimed_prediction(prediction_id, session)
                else:
                    await process_risk_event(prediction_id, session)
                processed += 1
            except PredictionNotFoundError:
                continue
            except Exception:
                logger.exception("reconciliation failed to process prediction_id=%s", prediction_id)
    return processed


async def reconcile_stale_processing() -> int:
    """Recover predictions stuck in PROCESSING past the timeout - e.g. a
    worker crashed between claiming ownership and completing Unit 1
    (architecture §11, §37)."""
    async with async_session_factory() as session:
        config_repo = ConfigRepository(session)
        timeout_seconds = await config_repo.get_int("reconciliation.stale_processing_timeout_seconds", default=120)
        stale_before = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        risk_repo = RiskEventRepository(session)
        stale = await risk_repo.list_stale_processing(stale_before)
        stale_ids = [row.prediction_id for row in stale]

    reclaimed = 0
    for prediction_id in stale_ids:
        async with async_session_factory() as session:
            risk_repo = RiskEventRepository(session)
            config_repo = ConfigRepository(session)
            timeout_seconds = await config_repo.get_int("reconciliation.stale_processing_timeout_seconds", default=120)
            stale_before = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
            won = await risk_repo.reclaim_stale(prediction_id, stale_before)
            await session.commit()
            if not won:
                continue
            try:
                # Ownership was just reclaimed via the UPDATE above - do NOT
                # go through process_risk_event's claim step again (it would
                # hit the existing row and incorrectly treat this as
                # "already owned", silently skipping real work).
                await process_already_claimed_prediction(prediction_id, session)
                reclaimed += 1
            except PredictionNotFoundError:
                continue
            except Exception:
                logger.exception("reconciliation failed to reprocess stale prediction_id=%s", prediction_id)
    return reclaimed


async def reconcile_notifications(batch_size: int | None = None) -> int:
    """Recover pending intents, incomplete deliveries, and retryable
    failures - this is literally the same dispatcher entry point the
    background loop uses (architecture §41), not a second implementation.
    Also requeues intents that have a bounded-retry-eligible TRANSIENT
    delivery failure (§39) - see ``_requeue_transient_failed_intents``.
    """
    await _requeue_transient_failed_intents()
    return await dispatch_pending_batch(batch_size=batch_size)


async def _requeue_transient_failed_intents() -> int:
    """A TRANSIENT delivery failure alone doesn't get retried by anything -
    the intent already moved to DISPATCHED after the batch that produced
    it. This finds intents with at least one TRANSIENT-failed delivery
    still under the retry cap and resets them to PENDING so the next
    dispatch pass (immediately following, in the same reconciliation
    cycle, or the background loop) re-attempts exactly those deliveries -
    ``get_or_create_pending_delivery``'s idempotency means the existing
    delivery row is reused/updated, never duplicated."""
    async with async_session_factory() as session:
        config_repo = ConfigRepository(session)
        notif_repo = NotificationRepository(session)
        max_retries = await config_repo.get_int("dispatch.max_retries", default=3)
        intent_ids = await notif_repo.list_transient_failed_intent_ids(max_retries)
        requeued = 0
        for intent_id in intent_ids:
            result = await session.execute(
                update(NotificationIntent)
                .where(NotificationIntent.notification_trigger_id == intent_id, NotificationIntent.status == NotificationIntentStatus.DISPATCHED)
                .values(status=NotificationIntentStatus.PENDING)
                .returning(NotificationIntent.notification_trigger_id)
            )
            if result.first() is not None:
                requeued += 1
        await session.commit()
        if requeued:
            logger.info("requeued %s intent(s) with retry-eligible transient delivery failures", requeued)
        return requeued


async def reconcile_stale_areas() -> int:
    """Detect prediction streams that have gone silent beyond the
    configured max gap and apply expiration (architecture §16, §41, §52).
    Missing data is never interpreted as NORMAL/safe."""
    async with async_session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.lifecycle_status.in_(list(OPEN_LIFECYCLE_STATUSES))))
        open_alert_ids = [row.alert_id for row in result.scalars().all()]

    expired = 0
    for alert_id in open_alert_ids:
        async with async_session_factory() as session:
            alert_repo = AlertRepository(session)
            audit_repo = AuditRepository(session)
            config_repo = ConfigRepository(session)

            alert = await alert_repo.get_by_id(alert_id)
            if alert is None or alert.lifecycle_status not in OPEN_LIFECYCLE_STATUSES:
                continue

            await alert_repo.acquire_area_lock(alert.area_id)
            alert = await alert_repo.get_by_id(alert_id)  # re-read under lock
            if alert is None or alert.lifecycle_status not in OPEN_LIFECYCLE_STATUSES:
                await session.rollback()
                continue

            max_gap = await config_repo.get_int("max_prediction_gap_seconds", default=3600)
            now = datetime.now(timezone.utc)
            if is_stale(alert, now, max_gap):
                apply_expiration(alert, now)
                await alert_repo.save(alert)
                await audit_repo.add_entry(
                    alert.alert_id,
                    AuditEventType.LIFECYCLE_EXPIRED.value,
                    {"reason": "max_prediction_gap_exceeded", "max_gap_seconds": max_gap},
                )
                await session.commit()
                expired += 1
            else:
                await session.rollback()
    return expired


async def run_reconciliation_cycle() -> dict:
    """Run all four reconciliation duties once. Returns a summary dict for
    logging/observability (architecture §61)."""
    summary = {}
    try:
        summary["predictions_reconciled"] = await reconcile_unprocessed_predictions()
    except Exception:
        logger.exception("reconcile_unprocessed_predictions failed")
        summary["predictions_reconciled"] = -1
    try:
        summary["stale_processing_reclaimed"] = await reconcile_stale_processing()
    except Exception:
        logger.exception("reconcile_stale_processing failed")
        summary["stale_processing_reclaimed"] = -1
    try:
        summary["notification_intents_dispatched"] = await reconcile_notifications()
    except Exception:
        logger.exception("reconcile_notifications failed")
        summary["notification_intents_dispatched"] = -1
    try:
        summary["areas_expired"] = await reconcile_stale_areas()
    except Exception:
        logger.exception("reconcile_stale_areas failed")
        summary["areas_expired"] = -1
    logger.info("reconciliation cycle complete: %s", summary)
    return summary
