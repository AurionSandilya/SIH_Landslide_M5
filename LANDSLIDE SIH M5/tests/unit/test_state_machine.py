from datetime import datetime, timedelta, timezone

import pytest

from alerting.state_machine import apply_confirmed_transition, apply_expiration, is_stale
from core.enums import LifecycleStatus, Severity
from models.db_models import Alert

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def make_alert(**overrides) -> Alert:
    defaults = dict(
        area_id="area-1",
        current_severity=Severity.WATCH,
        lifecycle_status=LifecycleStatus.ACTIVE,
        candidate_confirmations=0,
        last_prediction_timestamp=T0,
        created_at=T0,
    )
    defaults.update(overrides)
    return Alert(**defaults)


def test_escalate_updates_severity_not_lifecycle():
    alert = make_alert()
    outcome = apply_confirmed_transition(alert, Severity.WARNING, "escalate", T0 + timedelta(minutes=10))
    assert alert.current_severity == Severity.WARNING
    assert alert.lifecycle_status == LifecycleStatus.ACTIVE
    assert outcome.lifecycle_changed is False


def test_downgrade_keeps_alert_active():
    alert = make_alert(current_severity=Severity.CRITICAL)
    outcome = apply_confirmed_transition(alert, Severity.WARNING, "downgrade", T0 + timedelta(minutes=10))
    assert alert.current_severity == Severity.WARNING
    assert alert.lifecycle_status == LifecycleStatus.ACTIVE
    assert outcome.lifecycle_changed is False


def test_resolve_sets_terminal_lifecycle():
    alert = make_alert(current_severity=Severity.WARNING)
    outcome = apply_confirmed_transition(alert, Severity.NORMAL, "resolve", T0 + timedelta(minutes=20))
    assert alert.current_severity == Severity.NORMAL
    assert alert.lifecycle_status == LifecycleStatus.RESOLVED
    assert alert.resolved_at == T0 + timedelta(minutes=20)
    assert outcome.lifecycle_changed is True


def test_acknowledged_alert_can_still_escalate():
    # Invariant #5: acknowledgement never suppresses escalation.
    alert = make_alert(current_severity=Severity.WARNING, lifecycle_status=LifecycleStatus.ACKNOWLEDGED)
    outcome = apply_confirmed_transition(alert, Severity.CRITICAL, "escalate", T0 + timedelta(minutes=5))
    assert alert.current_severity == Severity.CRITICAL
    assert alert.lifecycle_status == LifecycleStatus.ACKNOWLEDGED  # untouched
    assert outcome.new_severity == Severity.CRITICAL


def test_terminal_alert_cannot_be_mutated():
    alert = make_alert(lifecycle_status=LifecycleStatus.RESOLVED)
    with pytest.raises(ValueError):
        apply_confirmed_transition(alert, Severity.WARNING, "escalate", T0)


def test_is_stale_true_beyond_max_gap():
    alert = make_alert(last_prediction_timestamp=T0)
    now = T0 + timedelta(hours=2)
    assert is_stale(alert, now, max_gap_seconds=3600) is True


def test_is_stale_false_within_max_gap():
    alert = make_alert(last_prediction_timestamp=T0)
    now = T0 + timedelta(minutes=30)
    assert is_stale(alert, now, max_gap_seconds=3600) is False


def test_is_stale_false_for_terminal_alert():
    alert = make_alert(last_prediction_timestamp=T0, lifecycle_status=LifecycleStatus.RESOLVED)
    now = T0 + timedelta(days=5)
    assert is_stale(alert, now, max_gap_seconds=3600) is False


def test_apply_expiration_sets_expired():
    alert = make_alert()
    now = T0 + timedelta(hours=3)
    outcome = apply_expiration(alert, now)
    assert alert.lifecycle_status == LifecycleStatus.EXPIRED
    assert alert.expired_at == now
    assert outcome.new_lifecycle == LifecycleStatus.EXPIRED
