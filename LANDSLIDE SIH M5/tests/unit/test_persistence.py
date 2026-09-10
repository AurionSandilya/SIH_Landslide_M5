from datetime import datetime, timedelta, timezone

import pytest

from alerting.persistence import PersistenceThresholds, classify_direction, evaluate_candidate
from core.enums import Severity

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_classify_direction_escalate():
    assert classify_direction(Severity.WATCH, Severity.WARNING) == "escalate"


def test_classify_direction_downgrade():
    assert classify_direction(Severity.CRITICAL, Severity.WARNING) == "downgrade"


def test_classify_direction_resolve():
    assert classify_direction(Severity.WARNING, Severity.NORMAL) == "resolve"


def test_classify_direction_none_when_same():
    assert classify_direction(Severity.WARNING, Severity.WARNING) is None


def test_reading_matches_current_severity_clears_candidate():
    result = evaluate_candidate(
        current_severity=Severity.WATCH,
        candidate_severity=Severity.WARNING,
        candidate_since=T0,
        candidate_confirmations=2,
        incoming_band=Severity.WATCH,
        prediction_timestamp=T0 + timedelta(minutes=5),
        thresholds=None,
    )
    assert result.candidate_severity is None
    assert result.candidate_confirmations == 0
    assert result.confirmed_severity is None
    assert result.was_reset is True


def test_new_candidate_starts_tracking():
    thresholds = PersistenceThresholds(min_duration_sec=600, min_confirmations=5)
    result = evaluate_candidate(
        current_severity=Severity.WATCH,
        candidate_severity=None,
        candidate_since=None,
        candidate_confirmations=0,
        incoming_band=Severity.WARNING,
        prediction_timestamp=T0,
        thresholds=thresholds,
    )
    assert result.candidate_severity == Severity.WARNING
    assert result.candidate_since == T0
    assert result.candidate_confirmations == 1
    assert result.confirmed_severity is None


def test_candidate_continues_and_confirms_by_count():
    thresholds = PersistenceThresholds(min_duration_sec=99999, min_confirmations=2)
    result = evaluate_candidate(
        current_severity=Severity.WATCH,
        candidate_severity=Severity.WARNING,
        candidate_since=T0,
        candidate_confirmations=1,
        incoming_band=Severity.WARNING,
        prediction_timestamp=T0 + timedelta(minutes=5),
        thresholds=thresholds,
    )
    # duration threshold NOT met (99999s), but count threshold (2) is met
    assert result.confirmed_severity == Severity.WARNING
    assert result.direction == "escalate"
    assert result.candidate_severity is None
    assert result.candidate_confirmations == 0


def test_candidate_confirms_by_duration_not_count():
    thresholds = PersistenceThresholds(min_duration_sec=300, min_confirmations=99)
    result = evaluate_candidate(
        current_severity=Severity.WATCH,
        candidate_severity=Severity.WARNING,
        candidate_since=T0,
        candidate_confirmations=1,
        incoming_band=Severity.WARNING,
        prediction_timestamp=T0 + timedelta(minutes=6),  # 360s >= 300s
        thresholds=thresholds,
    )
    assert result.confirmed_severity == Severity.WARNING


def test_candidate_not_yet_confirmed_keeps_accumulating():
    thresholds = PersistenceThresholds(min_duration_sec=600, min_confirmations=5)
    result = evaluate_candidate(
        current_severity=Severity.WATCH,
        candidate_severity=Severity.WARNING,
        candidate_since=T0,
        candidate_confirmations=1,
        incoming_band=Severity.WARNING,
        prediction_timestamp=T0 + timedelta(minutes=5),  # 300s < 600s, 2 < 5
        thresholds=thresholds,
    )
    assert result.confirmed_severity is None
    assert result.candidate_confirmations == 2
    assert result.candidate_since == T0  # unchanged - still tracking from the original run


def test_candidate_reset_when_switching_to_a_third_band():
    # Currently confirmed=WATCH, candidate tracking WARNING for a while,
    # then a CRITICAL reading arrives - candidate resets to a fresh
    # CRITICAL run, WARNING run is abandoned (architecture §15a).
    thresholds = PersistenceThresholds(min_duration_sec=600, min_confirmations=5)
    result = evaluate_candidate(
        current_severity=Severity.WATCH,
        candidate_severity=Severity.WARNING,
        candidate_since=T0,
        candidate_confirmations=3,
        incoming_band=Severity.CRITICAL,
        prediction_timestamp=T0 + timedelta(minutes=8),
        thresholds=thresholds,
    )
    assert result.candidate_severity == Severity.CRITICAL
    assert result.candidate_confirmations == 1
    assert result.candidate_since == T0 + timedelta(minutes=8)
    assert result.was_new_candidate is True


def test_downgrade_confirms_after_persistence():
    thresholds = PersistenceThresholds(min_duration_sec=900, min_confirmations=3)
    result = evaluate_candidate(
        current_severity=Severity.CRITICAL,
        candidate_severity=Severity.WARNING,
        candidate_since=T0,
        candidate_confirmations=2,
        incoming_band=Severity.WARNING,
        prediction_timestamp=T0 + timedelta(minutes=16),  # 960s >= 900s
        thresholds=thresholds,
    )
    assert result.confirmed_severity == Severity.WARNING
    assert result.direction == "downgrade"


def test_resolution_direction_for_normal_target():
    thresholds = PersistenceThresholds(min_duration_sec=60, min_confirmations=99)
    result = evaluate_candidate(
        current_severity=Severity.WARNING,
        candidate_severity=Severity.NORMAL,
        candidate_since=T0,
        candidate_confirmations=1,
        incoming_band=Severity.NORMAL,
        prediction_timestamp=T0 + timedelta(minutes=2),
        thresholds=thresholds,
    )
    assert result.confirmed_severity == Severity.NORMAL
    assert result.direction == "resolve"
