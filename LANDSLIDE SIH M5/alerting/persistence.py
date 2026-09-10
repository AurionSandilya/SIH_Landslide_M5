"""Candidate-state persistence engine (architecture §15, §15a, §16).

M5 never changes ``current_severity`` because a single prediction crossed a
threshold. A candidate severity is confirmed once EITHER it has held for a
configured minimum duration (measured on prediction timestamps, never wall
clock / processing time) OR it has been confirmed by a configured minimum
number of consecutive qualifying predictions - whichever condition is
defined for that (from_severity, to_severity) pair in ``alert_config``.

This module is pure / side-effect-free (no DB, no I/O) so it is trivially
unit-testable - the evaluator is responsible for loading config and
persisting the resulting fields onto the ``Alert`` row.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from core.enums import Severity, severity_rank


@dataclass(frozen=True)
class PersistenceThresholds:
    min_duration_sec: Optional[int]
    min_confirmations: Optional[int]


@dataclass
class PersistenceResult:
    candidate_severity: Optional[Severity]
    candidate_since: Optional[datetime]
    candidate_confirmations: int
    confirmed_severity: Optional[Severity]  # set only when promotion should happen this evaluation
    direction: Optional[str]  # "escalate" | "downgrade" | "resolve" | None, describes the confirmed transition
    was_reset: bool
    was_new_candidate: bool


def classify_direction(from_severity: Severity, to_severity: Severity) -> Optional[str]:
    if to_severity == from_severity:
        return None
    if to_severity == Severity.NORMAL:
        return "resolve"
    if severity_rank(to_severity) > severity_rank(from_severity):
        return "escalate"
    return "downgrade"


def evaluate_candidate(
    *,
    current_severity: Severity,
    candidate_severity: Optional[Severity],
    candidate_since: Optional[datetime],
    candidate_confirmations: int,
    incoming_band: Severity,
    prediction_timestamp: datetime,
    thresholds: Optional[PersistenceThresholds],
) -> PersistenceResult:
    """Pure function implementing the full candidate lifecycle for a single
    qualifying (i.e. ordering-validated, non-superseded) prediction.

    ``thresholds`` is the (min_duration_sec, min_confirmations) pair for the
    (current_severity -> incoming_band) transition, or ``None`` if
    ``incoming_band == current_severity`` (no transition being evaluated).
    """
    # Reading matches the already-confirmed severity: nothing to track.
    if incoming_band == current_severity:
        was_reset = candidate_severity is not None
        return PersistenceResult(
            candidate_severity=None,
            candidate_since=None,
            candidate_confirmations=0,
            confirmed_severity=None,
            direction=None,
            was_reset=was_reset,
            was_new_candidate=False,
        )

    if incoming_band == candidate_severity:
        # Continuing an in-progress candidate run.
        new_confirmations = candidate_confirmations + 1
        new_since = candidate_since
    else:
        # Different band than both the confirmed severity and any
        # in-progress candidate -> the previous candidate run (if any) no
        # longer qualifies and is reset; a fresh candidate run starts now
        # (architecture §15a "candidate reset").
        new_confirmations = 1
        new_since = prediction_timestamp

    direction = classify_direction(current_severity, incoming_band)

    should_promote = False
    if thresholds is not None:
        duration_ok = False
        if thresholds.min_duration_sec is not None and new_since is not None:
            elapsed = (prediction_timestamp - new_since).total_seconds()
            duration_ok = elapsed >= thresholds.min_duration_sec
        count_ok = False
        if thresholds.min_confirmations is not None:
            count_ok = new_confirmations >= thresholds.min_confirmations
        should_promote = duration_ok or count_ok

    if should_promote:
        return PersistenceResult(
            candidate_severity=None,
            candidate_since=None,
            candidate_confirmations=0,
            confirmed_severity=incoming_band,
            direction=direction,
            was_reset=False,
            was_new_candidate=False,
        )

    return PersistenceResult(
        candidate_severity=incoming_band,
        candidate_since=new_since,
        candidate_confirmations=new_confirmations,
        confirmed_severity=None,
        direction=None,
        was_reset=False,
        was_new_candidate=(incoming_band != candidate_severity),
    )
