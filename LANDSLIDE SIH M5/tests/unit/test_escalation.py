from alerting.escalation import template_key_for, trigger_type_for_transition
from core.enums import Severity, TriggerType


def test_escalation_trigger_by_severity():
    assert trigger_type_for_transition("escalate", Severity.WATCH) == TriggerType.WATCH_ESCALATION
    assert trigger_type_for_transition("escalate", Severity.WARNING) == TriggerType.WARNING_ESCALATION
    assert trigger_type_for_transition("escalate", Severity.CRITICAL) == TriggerType.CRITICAL_ESCALATION


def test_resolve_trigger():
    assert trigger_type_for_transition("resolve", Severity.NORMAL) == TriggerType.RESOLUTION


def test_downgrade_trigger():
    assert trigger_type_for_transition("downgrade", Severity.WARNING) == TriggerType.DOWNGRADE_NOTICE


def test_no_transition_no_trigger():
    assert trigger_type_for_transition(None, Severity.WARNING) is None


def test_template_key_deterministic():
    key1 = template_key_for(TriggerType.CRITICAL_ESCALATION, Severity.CRITICAL)
    key2 = template_key_for(TriggerType.CRITICAL_ESCALATION, Severity.CRITICAL)
    assert key1 == key2 == "critical_escalation.critical"
