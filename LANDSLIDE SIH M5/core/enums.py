"""Enumerations shared across the M5 alerting engine.

These map 1:1 onto Postgres ``ENUM`` types used by ``models/db_models.py``.
Using ``str, Enum`` (rather than ``StrEnum``, which requires Python 3.11+)
keeps this importable on any supported runtime and keeps values JSON/DB
serializable as plain strings.
"""

from enum import Enum


class Severity(str, Enum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# Ordering used for escalation/downgrade comparisons (index = rank).
SEVERITY_ORDER = [Severity.NORMAL, Severity.WATCH, Severity.WARNING, Severity.CRITICAL]


def severity_rank(sev: "Severity") -> int:
    return SEVERITY_ORDER.index(sev)


class LifecycleStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# Lifecycle states that must never reopen / never accept further evaluation.
TERMINAL_LIFECYCLE_STATUSES = {
    LifecycleStatus.RESOLVED,
    LifecycleStatus.CANCELLED,
    LifecycleStatus.EXPIRED,
}

OPEN_LIFECYCLE_STATUSES = {LifecycleStatus.ACTIVE, LifecycleStatus.ACKNOWLEDGED}


class TriggerType(str, Enum):
    WATCH_ESCALATION = "WATCH_ESCALATION"
    WARNING_ESCALATION = "WARNING_ESCALATION"
    CRITICAL_ESCALATION = "CRITICAL_ESCALATION"
    RESOLUTION = "RESOLUTION"
    DOWNGRADE_NOTICE = "DOWNGRADE_NOTICE"


class DeliveryChannel(str, Enum):
    SMS = "SMS"
    PUSH = "PUSH"


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


class FailureType(str, Enum):
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"


class ProcessingStatus(str, Enum):
    """States for ``processed_risk_events.status`` (architecture §11)."""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    RETRYABLE = "RETRYABLE"
    FAILED = "FAILED"


class NotificationIntentStatus(str, Enum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    FAILED = "FAILED"


class AuditEventType(str, Enum):
    """Canonical ``event_type`` values for ``alert_audit_log`` (architecture §33)."""

    ALERT_CREATED = "ALERT_CREATED"
    SEVERITY_ESCALATION = "SEVERITY_ESCALATION"
    SEVERITY_DOWNGRADE = "SEVERITY_DOWNGRADE"
    CANDIDATE_RESET = "CANDIDATE_RESET"
    LIFECYCLE_ACKNOWLEDGED = "LIFECYCLE_ACKNOWLEDGED"
    LIFECYCLE_ACKNOWLEDGE_NOOP = "LIFECYCLE_ACKNOWLEDGE_NOOP"
    LIFECYCLE_RESOLVED = "LIFECYCLE_RESOLVED"
    LIFECYCLE_CANCELLED = "LIFECYCLE_CANCELLED"
    LIFECYCLE_CANCEL_NOOP = "LIFECYCLE_CANCEL_NOOP"
    LIFECYCLE_EXPIRED = "LIFECYCLE_EXPIRED"
    PREDICTION_SUPERSEDED = "PREDICTION_SUPERSEDED"
    NOTIFICATION_INTENT_CREATED = "NOTIFICATION_INTENT_CREATED"
    NOTIFICATION_SUPPRESSED_COOLDOWN = "NOTIFICATION_SUPPRESSED_COOLDOWN"
    CONFIG_CHANGED = "CONFIG_CHANGED"
