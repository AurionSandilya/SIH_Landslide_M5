# Normalization layer for observations

"""Deterministic normalization utilities used by the environment adapters.

Each adapter returns a dictionary with the following *canonical* keys:

* ``area_id`` – str
* ``event_timestamp`` – ISO‑8601 UTC string
* ``metric`` – str (e.g. "rainfall", "temperature", "earthquake_magnitude", "sar_backscatter")
* ``value`` – float
* ``source`` – str (adapter name)

The functions in this module enforce the schema, convert timestamps to UTC,
perform simple unit‑normalisation (if required), and provide a lightweight
duplicate‑detection helper that can be used before persisting observations.
"""

from datetime import datetime, timezone
from typing import Dict, Any

_REQUIRED_FIELDS = {"area_id", "event_timestamp", "metric", "value", "source"}


def _ensure_fields(obs: Dict[str, Any]) -> None:
    """Raise ``ValueError`` if any required field is missing or of wrong type."""
    missing = _REQUIRED_FIELDS - obs.keys()
    if missing:
        raise ValueError(f"Observation missing required fields: {missing}")
    # Basic type checks (more thorough validation can be added later)
    if not isinstance(obs["area_id"], str):
        raise ValueError("area_id must be a string")
    if not isinstance(obs["metric"], str):
        raise ValueError("metric must be a string")
    if not isinstance(obs["value"], (int, float)):
        raise ValueError("value must be numeric")
    if not isinstance(obs["source"], str):
        raise ValueError("source must be a string")


def _to_utc_iso(ts: str) -> str:
    """Convert an arbitrary ISO‑8601 timestamp to UTC and return the ISO string.

    The input may contain a timezone offset; ``datetime.fromisoformat`` handles
    offsets in Python 3.11+.  The returned string is always ``+00:00``.
    """
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        # Assume naive timestamps are already UTC
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def normalize_observation(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Public entry point: validate, canonicalise, and return a clean observation.

    The adapters already map their provider‑specific fields to the canonical
    schema, so this function mainly guarantees type safety and UTC timestamps.
    """
    _ensure_fields(raw)
    raw["event_timestamp"] = _to_utc_iso(raw["event_timestamp"])
    # Ensure ``value`` is a float for storage consistency
    raw["value"] = float(raw["value"])
    return raw


def is_duplicate(existing: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """Simple duplicate detection.

    Two observations are considered duplicates if they share the same ``area_id``,
    ``metric`` and ``event_timestamp``.  The function expects both dictionaries to
    already be normalized.
    """
    return (
        existing["area_id"] == candidate["area_id"]
        and existing["metric"] == candidate["metric"]
        and existing["event_timestamp"] == candidate["event_timestamp"]
    )

# End of normalization utilities
