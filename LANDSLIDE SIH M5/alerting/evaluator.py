"""Top-level orchestrator: ``process_risk_event`` (architecture §35, §50).

This is the ONE authoritative pipeline. The webhook route and the
reconciliation job both call this exact function - there is intentionally
no second alerting implementation.

Two-transaction shape, matching the architecture's RECEIVED -> PROCESSING
-> COMPLETED processing-ownership model (§10, §11, §37):

  TXN A (short, DB-only): claim ``processed_risk_events`` idempotently and
    commit immediately, so ownership is durable/visible even if the
    process crashes a moment later.
  TXN B (DB-only, no external calls - §35): advisory lock -> read alert ->
    validate ordering -> persistence -> state machine -> write alert/audit
    /notification_intent -> mark COMPLETED -> commit.

If TXN B raises, it is rolled back and the event is marked RETRYABLE in a
small follow-up transaction so reconciliation can find and retry it - the
event is never silently dropped (architecture §37, §40 fail-closed).
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from alerting import config_service
from alerting.escalation import template_key_for, trigger_type_for_transition
from alerting.persistence import classify_direction, evaluate_candidate
from alerting.state_machine import apply_confirmed_transition
from core.enums import AuditEventType, NotificationIntentStatus, Severity
from core.exceptions import PredictionNotFoundError, ProcessingError
from models.db_models import Alert
from models.external_models import RiskPrediction
from repository.alert_repo import AlertRepository
from repository.audit_repo import AuditRepository
from repository.config_repo import ConfigRepository
from repository.notification_repo import NotificationRepository
from repository.risk_event_repo import RiskEventRepository
from repository.risk_prediction_repo import RiskPredictionRepository

logger = logging.getLogger(__name__)


def _severity_from_prediction(prediction: RiskPrediction) -> Severity:
    """M5 consumes M1's already-computed risk band as-is - it never
    reinterprets or recalibrates the ML risk score (architecture §7, §19:
    "M5 only consumes the resulting risk band/score - it never sets or
    interprets ML calibration")."""
    try:
        return Severity(prediction.risk_band)
    except ValueError as exc:
        raise ProcessingError(f"Unrecognized risk_band '{prediction.risk_band}' from M1/M3") from exc


def _composite_key(ts, created_at, pred_id):
    return (ts, created_at, pred_id)


async def process_risk_event(prediction_id: UUID, session: AsyncSession, area_id_hint: Optional[str] = None) -> None:
    """Process one prediction end-to-end, CLAIMING ownership first. Safe to
    call repeatedly for the same ``prediction_id`` (duplicate webhook,
    reconciliation overlap) - every call after the first is a fast no-op
    via ``processed_risk_events`` idempotency (architecture §53). This is
    the webhook entry point.

    Reconciliation's stale-PROCESSING recovery path already holds ownership
    (it reclaimed the row itself via an atomic UPDATE) and must NOT go
    through the claim step again - see ``process_already_claimed_prediction``.
    """
    pred_repo = RiskPredictionRepository(session)
    prediction = await pred_repo.get_with_retry(prediction_id)
    if prediction is None:
        # Architecture §8: never evaluate an uncommitted prediction. Leave
        # it entirely unclaimed so reconciliation's lookback-window scan
        # picks it up once it becomes visible.
        raise PredictionNotFoundError(f"prediction_id={prediction_id} not visible after retry/backoff")

    risk_repo = RiskEventRepository(session)
    claimed = await risk_repo.try_claim(
        prediction.prediction_id, area_id=prediction.area_id, prediction_timestamp=prediction.prediction_timestamp
    )
    await session.commit()  # TXN A

    if not claimed:
        logger.info("prediction_id=%s already owned/completed, skipping", prediction_id)
        return

    await _run_claimed_pipeline(prediction, session, risk_repo)


async def process_already_claimed_prediction(prediction_id: UUID, session: AsyncSession) -> None:
    """Process a prediction whose ``processed_risk_events`` ownership was
    already (re)claimed by the caller (reconciliation's stale-PROCESSING
    recovery, architecture §11/§37) - skips the claim step entirely, since
    attempting it again would hit the row that already exists and
    incorrectly no-op."""
    pred_repo = RiskPredictionRepository(session)
    prediction = await pred_repo.get(prediction_id)
    if prediction is None:
        raise PredictionNotFoundError(f"prediction_id={prediction_id} not visible")
    risk_repo = RiskEventRepository(session)
    await _run_claimed_pipeline(prediction, session, risk_repo)


async def _run_claimed_pipeline(prediction: RiskPrediction, session: AsyncSession, risk_repo: RiskEventRepository) -> None:
    try:
        await _evaluate_claimed_prediction(prediction, session)
        await risk_repo.mark_completed(prediction.prediction_id)
        await session.commit()  # TXN B
    except Exception as exc:
        await session.rollback()
        logger.exception("processing failed for prediction_id=%s", prediction.prediction_id)
        try:
            await risk_repo.mark_retryable(prediction.prediction_id, str(exc))
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("failed to mark prediction_id=%s retryable", prediction.prediction_id)
        raise


async def _evaluate_claimed_prediction(prediction: RiskPrediction, session: AsyncSession) -> None:
    alert_repo = AlertRepository(session)
    audit_repo = AuditRepository(session)
    config_repo = ConfigRepository(session)
    notif_repo = NotificationRepository(session)

    await alert_repo.acquire_area_lock(prediction.area_id)

    incoming_band = _severity_from_prediction(prediction)
    incoming_key = _composite_key(prediction.prediction_timestamp, prediction.created_at, prediction.prediction_id)

    open_alert = await alert_repo.get_open_for_area(prediction.area_id)

    if open_alert is not None:
        await _evaluate_against_open_alert(open_alert, prediction, incoming_band, incoming_key, alert_repo, audit_repo, config_repo, notif_repo)
        return

    # No open alert for this area - check the high-water mark before ever
    # starting a new incident (architecture §18).
    latest = await alert_repo.get_latest_for_area(prediction.area_id)
    if latest is not None:
        last_key = _composite_key(
            latest.last_evaluated_prediction_timestamp, latest.last_evaluated_created_at, latest.last_evaluated_prediction_id
        )
        if last_key[0] is not None and incoming_key <= last_key:
            await audit_repo.add_entry(
                latest.alert_id,
                AuditEventType.PREDICTION_SUPERSEDED.value,
                {
                    "prediction_id": str(prediction.prediction_id),
                    "reason": "older-than-area-high-water-mark",
                    "area_id": prediction.area_id,
                },
            )
            return

    if incoming_band == Severity.NORMAL:
        # Nothing to do - no incident exists and this reading doesn't start one.
        return

    await _create_new_alert(prediction, incoming_band, incoming_key, alert_repo, audit_repo, config_repo, notif_repo)


async def _evaluate_against_open_alert(
    alert: Alert, prediction: RiskPrediction, incoming_band: Severity, incoming_key, alert_repo, audit_repo, config_repo, notif_repo
) -> None:
    last_key = _composite_key(alert.last_evaluated_prediction_timestamp, alert.last_evaluated_created_at, alert.last_evaluated_prediction_id)

    if last_key[0] is not None and incoming_key <= last_key:
        # Architecture §17: older/equal predictions never modify authoritative
        # state - not severity, not lifecycle, not candidates, no notification.
        await audit_repo.add_entry(
            alert.alert_id,
            AuditEventType.PREDICTION_SUPERSEDED.value,
            {"prediction_id": str(prediction.prediction_id), "reason": "out-of-order"},
        )
        return

    direction = classify_direction(alert.current_severity, incoming_band)
    thresholds = None
    if direction is not None:
        thresholds = await config_service.get_persistence_thresholds(config_repo, incoming_band, direction)

    result = evaluate_candidate(
        current_severity=alert.current_severity,
        candidate_severity=alert.candidate_severity,
        candidate_since=alert.candidate_since,
        candidate_confirmations=alert.candidate_confirmations,
        incoming_band=incoming_band,
        prediction_timestamp=prediction.prediction_timestamp,
        thresholds=thresholds,
    )

    # These fields advance on every non-superseded prediction regardless of
    # whether it confirms/changes anything (architecture §15, §47).
    alert.last_evaluated_prediction_timestamp = prediction.prediction_timestamp
    alert.last_evaluated_created_at = prediction.created_at
    alert.last_evaluated_prediction_id = prediction.prediction_id
    alert.last_prediction_timestamp = prediction.prediction_timestamp
    alert.last_risk_score = prediction.risk_score
    alert.last_confidence = prediction.confidence

    # Persist the candidate fields unconditionally - evaluate_candidate()
    # already guarantees these are (None, None, 0) in the confirmed-promotion
    # case, so this single assignment covers both branches correctly (this
    # was previously only done in the "not yet confirmed" branch, which left
    # a stale candidate_severity on the alert after a promotion - a real bug
    # caught by test_escalation_confirms_after_second_reading_and_notifies).
    alert.candidate_severity = result.candidate_severity
    alert.candidate_since = result.candidate_since
    alert.candidate_confirmations = result.candidate_confirmations

    if result.confirmed_severity is not None:
        outcome = apply_confirmed_transition(alert, result.confirmed_severity, result.direction, prediction.prediction_timestamp)
        await _record_transition_and_notify(alert, outcome, audit_repo, config_repo, notif_repo)
    elif result.was_reset:
        await audit_repo.add_entry(alert.alert_id, AuditEventType.CANDIDATE_RESET.value, {"incoming_band": incoming_band.value})

    await alert_repo.save(alert)


async def _create_new_alert(prediction: RiskPrediction, incoming_band: Severity, incoming_key, alert_repo, audit_repo, config_repo, notif_repo) -> None:
    thresholds = await config_service.get_persistence_thresholds(config_repo, incoming_band, "escalate")
    result = evaluate_candidate(
        current_severity=Severity.NORMAL,
        candidate_severity=None,
        candidate_since=None,
        candidate_confirmations=0,
        incoming_band=incoming_band,
        prediction_timestamp=prediction.prediction_timestamp,
        thresholds=thresholds,
    )

    fields = dict(
        area_id=prediction.area_id,
        current_severity=Severity.NORMAL,
        candidate_severity=result.candidate_severity,
        candidate_since=result.candidate_since,
        candidate_confirmations=result.candidate_confirmations,
        last_prediction_timestamp=prediction.prediction_timestamp,
        last_evaluated_prediction_timestamp=incoming_key[0],
        last_evaluated_created_at=incoming_key[1],
        last_evaluated_prediction_id=incoming_key[2],
        last_risk_score=prediction.risk_score,
        last_confidence=prediction.confidence,
    )
    alert = await alert_repo.create(**fields)
    await audit_repo.add_entry(alert.alert_id, AuditEventType.ALERT_CREATED.value, {"initial_band": incoming_band.value})

    if result.confirmed_severity is not None:
        outcome = apply_confirmed_transition(alert, result.confirmed_severity, result.direction, prediction.prediction_timestamp)
        await _record_transition_and_notify(alert, outcome, audit_repo, config_repo, notif_repo)
        await alert_repo.save(alert)


async def _record_transition_and_notify(alert: Alert, outcome, audit_repo: AuditRepository, config_repo: ConfigRepository, notif_repo: NotificationRepository) -> None:
    if outcome.direction == "resolve":
        await audit_repo.add_entry(alert.alert_id, AuditEventType.LIFECYCLE_RESOLVED.value, {"final_severity": outcome.new_severity.value})
    elif outcome.direction == "escalate":
        await audit_repo.add_entry(alert.alert_id, AuditEventType.SEVERITY_ESCALATION.value, {"new_severity": outcome.new_severity.value})
    elif outcome.direction == "downgrade":
        await audit_repo.add_entry(alert.alert_id, AuditEventType.SEVERITY_DOWNGRADE.value, {"new_severity": outcome.new_severity.value})

    trigger_type = trigger_type_for_transition(outcome.direction, outcome.new_severity)
    if trigger_type is None:
        return

    should_notify = True
    if outcome.direction == "downgrade":
        should_notify = await config_service.get_notify_on_downgrade(config_repo)

    if not should_notify:
        return

    intent = await notif_repo.create_intent(
        alert_id=alert.alert_id,
        trigger_type=trigger_type,
        severity=outcome.new_severity,
        template_key=template_key_for(trigger_type, outcome.new_severity),
        status=NotificationIntentStatus.PENDING,
    )
    await audit_repo.add_entry(
        alert.alert_id,
        AuditEventType.NOTIFICATION_INTENT_CREATED.value,
        {"trigger_type": trigger_type.value, "notification_trigger_id": str(intent.notification_trigger_id)},
    )
