"""Minimal message-template rendering (architecture §62a).

M5 owns "what does a CRITICAL SMS say", but never composes free text inside
the deterministic alert-decision path (Unit 1) - only the dispatcher (Unit
2, after commit, outside the DB transaction) renders text for the actual
provider call. Templates are locale-keyed; English + Assamese are provided
for the MVP as the two languages named for this NER-India deployment.

For the MVP this is a small in-module dict, not a new service, per §62a -
swap for a ``notification_templates`` DB table if operators need to edit
copy without a deploy.
"""

from typing import Any, Dict

_DEFAULT_LOCALE = "en"

_TEMPLATES: Dict[str, Dict[str, str]] = {
    "watch_escalation.watch": {
        "en": "Landslide WATCH for your area ({area_id}). Stay alert to updates.",
        "as": "আপোনাৰ অঞ্চলত ({area_id}) মাটি নৰগা WATCH সতৰ্কবাণী। আপডেটৰ প্ৰতি সজাগ থাকক।",
    },
    "warning_escalation.warning": {
        "en": "Landslide WARNING for your area ({area_id}). Prepare to move to safety if advised.",
        "as": "আপোনাৰ অঞ্চলত ({area_id}) মাটি নৰগা WARNING সতৰ্কবাণী। পৰামৰ্শ দিয়া হ'লে সুৰক্ষিত স্থানলৈ যাবলৈ সাজু থাকক।",
    },
    "critical_escalation.critical": {
        "en": "URGENT: Landslide CRITICAL risk in your area ({area_id}). Move to safety now.",
        "as": "জৰুৰী: আপোনাৰ অঞ্চলত ({area_id}) মাটি নৰগা CRITICAL বিপদ। এতিয়াই সুৰক্ষিত স্থানলৈ যাওক।",
    },
    "downgrade_notice.watch": {
        "en": "Landslide risk in your area ({area_id}) has eased to WATCH. Remain cautious.",
        "as": "আপোনাৰ অঞ্চলৰ ({area_id}) মাটি নৰগা বিপদ কমি WATCH পৰ্যায়লৈ আহিছে। সাৱধান হৈ থাকক।",
    },
    "downgrade_notice.warning": {
        "en": "Landslide risk in your area ({area_id}) has eased to WARNING. Remain cautious.",
        "as": "আপোনাৰ অঞ্চলৰ ({area_id}) মাটি নৰগা বিপদ কমি WARNING পৰ্যায়লৈ আহিছে। সাৱধান হৈ থাকক।",
    },
    "resolution.normal": {
        "en": "The landslide risk for your area ({area_id}) has returned to normal.",
        "as": "আপোনাৰ অঞ্চলৰ ({area_id}) মাটি নৰগা বিপদ স্বাভাৱিক অৱস্থালৈ ঘূৰি আহিছে।",
    },
}


def render_message(template_key: str, locale: str, facts: Dict[str, Any]) -> str:
    entry = _TEMPLATES.get(template_key)
    if entry is None:
        # Fail closed to a safe, generic-but-informative message rather than
        # raising and losing the notification entirely - but this should be
        # treated as an operational gap (missing template) worth alerting on.
        return f"Landslide alert update for area {facts.get('area_id')}: {facts.get('severity')} ({template_key})"
    text = entry.get(locale) or entry.get(_DEFAULT_LOCALE)
    try:
        return text.format(**facts)
    except (KeyError, IndexError):
        return text
