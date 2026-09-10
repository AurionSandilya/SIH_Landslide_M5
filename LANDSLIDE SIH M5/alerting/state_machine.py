"""Deterministic lifecycle/severity state machine (architecture §13, §14,
§14a, §14b). Pure data transformation - no I/O - so it's fully unit
testable. The evaluator is responsible for persisting the mutations and
writing audit/notification-intent side effects.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from core.enums import LifecycleStatus, Severity, TERMINAL_LIFECYCLE_STATUSES
from models.db_models import Alert


@dataclass
class TransitionOutcome:
    severity_changed: bool
    direction: Optional[str]  # 'escalate' | 'downgrade' | 'resolve' | None
    new_severity: Optional[Severity]
    lifecycle_changed: bool
    new_lifecycle: Optional[LifecycleStatus]


def apply_confirmed_transition(alert: Alert, confirmed_severity: Severity, direction: str, prediction_timestamp: datetime) -> TransitionOutcome:
    """Apply a persistence-confirmed severity transition to ``alert``
    in-place. Must only be called for a non-terminal alert - a terminal
    alert is never mutated (architecture §14b); the evaluator creates a new
    alert instead.
    """
    if alert.lifecycle_status in TERMINAL_LIFECYCLE_STATUSES:
        raise ValueError("apply_confirmed_transition called on a terminal alert; this is a caller bug")

    alert.current_severity = confirmed_severity
    alert.band_entered_at = prediction_timestamp

    lifecycle_changed = False
    new_lifecycle = None

    if direction == "resolve":
        # Architecture §14: ACTIVE/ACKNOWLEDGED -> sustained NORMAL -> RESOLVED.
        # Lifecycle changes; severity+lifecycle remain two independent
        # dimensions right up to this point (invariant: acknowledgement
        # never blocks resolution either).
        alert.lifecycle_status = LifecycleStatus.RESOLVED
        alert.resolved_at = prediction_timestamp
        lifecycle_changed = True
        new_lifecycle = LifecycleStatus.RESOLVED

    return TransitionOutcome(
        severity_changed=True,
        direction=direction,
        new_severity=confirmed_severity,
        lifecycle_changed=lifecycle_changed,
        new_lifecycle=new_lifecycle,
    )


def is_stale(alert: Alert, now: datetime, max_gap_seconds: int) -> bool:
    """Architecture §16: missing data never means safe - a prediction
    stream that has gone silent beyond ``max_gap_seconds`` (measured
    against the last prediction actually received, not wall-clock since
    M5 last ran) makes the alert's data untrustworthy, which is expressed
    as EXPIRED, never as an implied NORMAL/RESOLVED."""
    if alert.lifecycle_status not in (LifecycleStatus.ACTIVE, LifecycleStatus.ACKNOWLEDGED):
        return False
    reference = alert.last_prediction_timestamp or alert.created_at
    if reference is None:
        return False
    return (now - reference).total_seconds() > max_gap_seconds


def apply_expiration(alert: Alert, now: datetime) -> TransitionOutcome:
    alert.lifecycle_status = LifecycleStatus.EXPIRED
    alert.expired_at = now
    return TransitionOutcome(
        severity_changed=False,
        direction=None,
        new_severity=None,
        lifecycle_changed=True,
        new_lifecycle=LifecycleStatus.EXPIRED,
    )
