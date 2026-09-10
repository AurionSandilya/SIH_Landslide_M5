"""Maps a confirmed severity transition onto notification trigger identity
(architecture §23) and message template key (§62a).

Kept separate from the state machine so "what does this transition mean for
notifications" is one obvious place to look/change, without touching
persistence or lifecycle logic.
"""

from typing import Optional

from core.enums import Severity, TriggerType

_ESCALATION_TRIGGER_BY_SEVERITY = {
    Severity.WATCH: TriggerType.WATCH_ESCALATION,
    Severity.WARNING: TriggerType.WARNING_ESCALATION,
    Severity.CRITICAL: TriggerType.CRITICAL_ESCALATION,
}


def trigger_type_for_transition(direction: str, to_severity: Severity) -> Optional[TriggerType]:
    """``direction`` is 'escalate', 'downgrade', or 'resolve'. Returns
    ``None`` if this transition never generates a notification_intent
    (callers still decide, via alert_config, whether a generated trigger
    actually fires - e.g. notify_on_downgrade)."""
    if direction == "escalate":
        return _ESCALATION_TRIGGER_BY_SEVERITY.get(to_severity)
    if direction == "resolve":
        return TriggerType.RESOLUTION
    if direction == "downgrade":
        return TriggerType.DOWNGRADE_NOTICE
    return None


def template_key_for(trigger_type: TriggerType, severity: Severity) -> str:
    """Deterministic template key, derived from trigger_type + severity
    (architecture §62a). The dispatcher resolves this + recipient locale
    to actual message text - the alert-decision path never composes
    free text."""
    return f"{trigger_type.value.lower()}.{severity.value.lower()}"
