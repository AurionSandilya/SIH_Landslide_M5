"""Bootstrap defaults for ``alert_config`` (architecture §19a).

These are **only** used to seed the DB-backed configuration table on first
run (``alert_config`` empty) or as an in-process fallback if a key is
missing from the table. Once a row exists in ``alert_config`` it always
wins over these constants — safety-relevant thresholds must be tunable
without a redeploy, and any change to them is audited (``CONFIG_CHANGED``).

Naming convention: ``persistence.<SEVERITY>.<escalate|downgrade>.<min_duration_sec|min_confirmations>``
"""

from core.enums import Severity, DeliveryChannel, TriggerType

SEVERITY_THRESHOLDS = {
    Severity.NORMAL: 0.0,
    Severity.WATCH: 0.40,
    Severity.WARNING: 0.60,
    Severity.CRITICAL: 0.80,
}

# Escalation windows: looser than downgrade windows, since under-alerting is the
# worse failure mode for a safety-critical system (architecture §15a).
_PERSISTENCE_DEFAULTS = {
    "persistence.WATCH.escalate.min_duration_sec": "300",       # 5 min
    "persistence.WATCH.escalate.min_confirmations": "2",
    "persistence.WARNING.escalate.min_duration_sec": "300",     # 5 min
    "persistence.WARNING.escalate.min_confirmations": "2",
    "persistence.CRITICAL.escalate.min_duration_sec": "180",    # 3 min - fast escalation
    "persistence.CRITICAL.escalate.min_confirmations": "2",
    "persistence.WATCH.downgrade.min_duration_sec": "600",      # 10 min
    "persistence.WATCH.downgrade.min_confirmations": "3",
    "persistence.WARNING.downgrade.min_duration_sec": "900",    # 15 min
    "persistence.WARNING.downgrade.min_confirmations": "3",
    "persistence.CRITICAL.downgrade.min_duration_sec": "900",   # 15 min
    "persistence.CRITICAL.downgrade.min_confirmations": "3",
    # NORMAL persistence before an ACTIVE/ACKNOWLEDGED alert is allowed to RESOLVE.
    "persistence.resolution.min_duration_sec": "900",           # 15 min
    "persistence.resolution.min_confirmations": "3",
}

_COOLDOWN_DEFAULTS = {
    f"cooldown.{DeliveryChannel.SMS.value}.{TriggerType.WATCH_ESCALATION.value}.seconds": "3600",
    f"cooldown.{DeliveryChannel.SMS.value}.{TriggerType.WARNING_ESCALATION.value}.seconds": "1800",
    f"cooldown.{DeliveryChannel.SMS.value}.{TriggerType.CRITICAL_ESCALATION.value}.seconds": "0",
    f"cooldown.{DeliveryChannel.SMS.value}.{TriggerType.RESOLUTION.value}.seconds": "0",
    f"cooldown.{DeliveryChannel.SMS.value}.{TriggerType.DOWNGRADE_NOTICE.value}.seconds": "1800",
    f"cooldown.{DeliveryChannel.PUSH.value}.{TriggerType.WATCH_ESCALATION.value}.seconds": "1800",
    f"cooldown.{DeliveryChannel.PUSH.value}.{TriggerType.WARNING_ESCALATION.value}.seconds": "900",
    f"cooldown.{DeliveryChannel.PUSH.value}.{TriggerType.CRITICAL_ESCALATION.value}.seconds": "0",
    f"cooldown.{DeliveryChannel.PUSH.value}.{TriggerType.RESOLUTION.value}.seconds": "0",
    f"cooldown.{DeliveryChannel.PUSH.value}.{TriggerType.DOWNGRADE_NOTICE.value}.seconds": "900",
}

_MISC_DEFAULTS = {
    "notify_on_downgrade": "false",                    # architecture §28a - off by default
    "max_prediction_gap_seconds": "3600",               # 1 hour, architecture §16
    "reconciliation.interval_seconds": "300",            # ~5 min, architecture §41
    "reconciliation.lookback_seconds": "1800",            # 30 min lookback window, §42
    "reconciliation.stale_processing_timeout_seconds": "120",
    "dispatch.batch_size": "100",
    "dispatch.batch_delay_seconds": "1",
    "dispatch.max_retries": "3",
    "dispatch.retry_backoff_seconds": "5,15,30,60",
}

DEFAULT_CONFIG: dict[str, str] = {
    **_PERSISTENCE_DEFAULTS,
    **_COOLDOWN_DEFAULTS,
    **_MISC_DEFAULTS,
}
