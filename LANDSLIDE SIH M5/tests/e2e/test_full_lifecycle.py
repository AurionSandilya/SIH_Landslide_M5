"""End-to-end lifecycle test (audit §12 "REQUIRED TESTS", exact sequence
requested):

    NORMAL -> WATCH -> WARNING -> ACKNOWLEDGED -> CRITICAL -> WARNING ->
    NORMAL -> RESOLVED

Verifies audit records and notification behavior at each meaningful step.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from alerting.evaluator import process_risk_event
from alerting.lifecycle_actions import acknowledge_alert
from core.enums import LifecycleStatus, Severity, TriggerType
from models.db_models import Alert, AlertAuditLog, NotificationIntent, async_session_factory
from repository.config_repo import ConfigRepository
from tests.conftest import insert_prediction

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


async def _fast_config(session):
    repo = ConfigRepository(session)
    for sev in ("WATCH", "WARNING", "CRITICAL"):
        await repo.set(f"persistence.{sev}.escalate.min_duration_sec", "1", "test")
        await repo.set(f"persistence.{sev}.escalate.min_confirmations", "2", "test")
        await repo.set(f"persistence.{sev}.downgrade.min_duration_sec", "1", "test")
        await repo.set(f"persistence.{sev}.downgrade.min_confirmations", "2", "test")
    await repo.set("persistence.resolution.min_duration_sec", "1", "test")
    await repo.set("persistence.resolution.min_confirmations", "2", "test")
    await session.commit()


async def _feed(session, area_id, band, t):
    pid = await insert_prediction(session, area_id, band, t)
    await process_risk_event(pid, session)


@pytest.mark.asyncio
async def test_full_lifecycle_sequence():
    area_id = "area-e2e"
    t = T0

    async with async_session_factory() as session:
        await _fast_config(session)

        def next_t():
            nonlocal t
            t = t + timedelta(seconds=10)
            return t

        # NORMAL - no incident should exist yet.
        await _feed(session, area_id, "NORMAL", next_t())
        alerts = (await session.execute(select(Alert).where(Alert.area_id == area_id))).scalars().all()
        assert len(alerts) == 0

        # -> WATCH (needs 2 confirmations to confirm)
        await _feed(session, area_id, "WATCH", next_t())
        await _feed(session, area_id, "WATCH", next_t())
        alert = (await session.execute(select(Alert).where(Alert.area_id == area_id))).scalar_one()
        assert alert.current_severity == Severity.WATCH
        assert alert.lifecycle_status == LifecycleStatus.ACTIVE

        # -> WARNING
        await _feed(session, area_id, "WARNING", next_t())
        await _feed(session, area_id, "WARNING", next_t())
        alert = (await session.execute(select(Alert).where(Alert.area_id == area_id))).scalar_one()
        assert alert.current_severity == Severity.WARNING

        # -> ACKNOWLEDGED (human action)
        outcome = await acknowledge_alert(session, alert.alert_id)
        await session.commit()
        assert outcome.lifecycle_status == LifecycleStatus.ACKNOWLEDGED

        # -> CRITICAL (must still escalate despite ACKNOWLEDGED - invariant #5)
        await _feed(session, area_id, "CRITICAL", next_t())
        await _feed(session, area_id, "CRITICAL", next_t())
        alert = (await session.execute(select(Alert).where(Alert.area_id == area_id))).scalar_one()
        assert alert.current_severity == Severity.CRITICAL
        assert alert.lifecycle_status == LifecycleStatus.ACKNOWLEDGED  # untouched by escalation

        # -> WARNING (downgrade, persisted)
        await _feed(session, area_id, "WARNING", next_t())
        await _feed(session, area_id, "WARNING", next_t())
        alert = (await session.execute(select(Alert).where(Alert.area_id == area_id))).scalar_one()
        assert alert.current_severity == Severity.WARNING
        assert alert.lifecycle_status == LifecycleStatus.ACKNOWLEDGED  # still ACKNOWLEDGED, not reset

        # -> NORMAL -> RESOLVED (sustained NORMAL)
        await _feed(session, area_id, "NORMAL", next_t())
        await _feed(session, area_id, "NORMAL", next_t())
        alert = (await session.execute(select(Alert).where(Alert.area_id == area_id))).scalar_one()
        assert alert.current_severity == Severity.NORMAL
        assert alert.lifecycle_status == LifecycleStatus.RESOLVED
        assert alert.resolved_at is not None

        # ---- Audit trail assertions ----
        audit_rows = (
            await session.execute(select(AlertAuditLog).where(AlertAuditLog.alert_id == alert.alert_id).order_by(AlertAuditLog.timestamp.asc()))
        ).scalars().all()
        event_types = [row.event_type for row in audit_rows]

        assert "ALERT_CREATED" in event_types
        assert event_types.count("SEVERITY_ESCALATION") >= 3  # WATCH, WARNING, CRITICAL
        assert "LIFECYCLE_ACKNOWLEDGED" in event_types
        assert "SEVERITY_DOWNGRADE" in event_types  # CRITICAL -> WARNING
        assert "LIFECYCLE_RESOLVED" in event_types
        # Order sanity: creation before acknowledgement before resolution.
        assert event_types.index("ALERT_CREATED") < event_types.index("LIFECYCLE_ACKNOWLEDGED")
        assert event_types.index("LIFECYCLE_ACKNOWLEDGED") < event_types.index("LIFECYCLE_RESOLVED")

        # ---- Notification intent assertions ----
        intents = (await session.execute(select(NotificationIntent).where(NotificationIntent.alert_id == alert.alert_id))).scalars().all()
        trigger_types = [i.trigger_type for i in intents]
        assert TriggerType.WATCH_ESCALATION in trigger_types
        assert TriggerType.WARNING_ESCALATION in trigger_types
        assert TriggerType.CRITICAL_ESCALATION in trigger_types
        assert TriggerType.RESOLUTION in trigger_types
        # notify_on_downgrade defaults to false - no DOWNGRADE_NOTICE intent.
        assert TriggerType.DOWNGRADE_NOTICE not in trigger_types
