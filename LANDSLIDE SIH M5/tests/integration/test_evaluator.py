from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from alerting.evaluator import process_risk_event
from core.enums import LifecycleStatus, NotificationIntentStatus, Severity, TriggerType
from core.exceptions import PredictionNotFoundError
from models.db_models import Alert, AlertAuditLog, NotificationIntent, async_session_factory
from repository.config_repo import ConfigRepository
from tests.conftest import insert_prediction

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


async def _set_fast_config(session):
    """Small deterministic windows so tests don't need to wait real time -
    persistence is measured on prediction timestamps, which the test fully
    controls."""
    repo = ConfigRepository(session)
    for sev in ("WATCH", "WARNING", "CRITICAL"):
        await repo.set(f"persistence.{sev}.escalate.min_duration_sec", "1", "test")
        await repo.set(f"persistence.{sev}.escalate.min_confirmations", "2", "test")
        await repo.set(f"persistence.{sev}.downgrade.min_duration_sec", "1", "test")
        await repo.set(f"persistence.{sev}.downgrade.min_confirmations", "2", "test")
    await repo.set("persistence.resolution.min_duration_sec", "1", "test")
    await repo.set("persistence.resolution.min_confirmations", "2", "test")
    await session.commit()


@pytest.mark.asyncio
async def test_new_alert_created_and_candidate_tracked():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        pid = await insert_prediction(session, "area-1", "WARNING", T0)
        await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.NORMAL  # not confirmed yet (needs 2 confirmations)
        assert alert.candidate_severity == Severity.WARNING
        assert alert.candidate_confirmations == 1
        assert alert.lifecycle_status == LifecycleStatus.ACTIVE


@pytest.mark.asyncio
async def test_escalation_confirms_after_second_reading_and_notifies():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        pid1 = await insert_prediction(session, "area-1", "WARNING", T0)
        await process_risk_event(pid1, session)
        pid2 = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(seconds=5))
        await process_risk_event(pid2, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.WARNING
        assert alert.candidate_severity is None

        intents = (await session.execute(select(NotificationIntent).where(NotificationIntent.alert_id == alert.alert_id))).scalars().all()
        assert len(intents) == 1
        assert intents[0].trigger_type == TriggerType.WARNING_ESCALATION
        assert intents[0].status == NotificationIntentStatus.PENDING


@pytest.mark.asyncio
async def test_acknowledged_alert_still_escalates_and_notifies():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        for i in range(2):
            pid = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        alert.lifecycle_status = LifecycleStatus.ACKNOWLEDGED
        await session.commit()

        for i in range(2, 4):
            pid = await insert_prediction(session, "area-1", "CRITICAL", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.CRITICAL
        assert alert.lifecycle_status == LifecycleStatus.ACKNOWLEDGED  # untouched

        intents = (await session.execute(select(NotificationIntent).where(NotificationIntent.alert_id == alert.alert_id))).scalars().all()
        trigger_types = {i.trigger_type for i in intents}
        assert TriggerType.WARNING_ESCALATION in trigger_types
        assert TriggerType.CRITICAL_ESCALATION in trigger_types


@pytest.mark.asyncio
async def test_downgrade_does_not_notify_by_default():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        # Escalate straight to CRITICAL.
        for i in range(2):
            pid = await insert_prediction(session, "area-1", "CRITICAL", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)
        # Now ease to WARNING.
        for i in range(2, 4):
            pid = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.WARNING
        assert alert.lifecycle_status == LifecycleStatus.ACTIVE

        intents = (await session.execute(select(NotificationIntent).where(NotificationIntent.alert_id == alert.alert_id))).scalars().all()
        trigger_types = {i.trigger_type for i in intents}
        assert TriggerType.CRITICAL_ESCALATION in trigger_types
        assert TriggerType.DOWNGRADE_NOTICE not in trigger_types  # notify_on_downgrade defaults to false

        audit = (await session.execute(select(AlertAuditLog).where(AlertAuditLog.alert_id == alert.alert_id, AlertAuditLog.event_type == "SEVERITY_DOWNGRADE"))).scalars().all()
        assert len(audit) == 1  # downgrade is still audited even though not notified


@pytest.mark.asyncio
async def test_downgrade_notifies_when_enabled():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        config_repo = ConfigRepository(session)
        await config_repo.set("notify_on_downgrade", "true", "test")
        await session.commit()

        for i in range(2):
            pid = await insert_prediction(session, "area-1", "CRITICAL", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)
        for i in range(2, 4):
            pid = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        intents = (await session.execute(select(NotificationIntent).where(NotificationIntent.alert_id == alert.alert_id))).scalars().all()
        trigger_types = {i.trigger_type for i in intents}
        assert TriggerType.DOWNGRADE_NOTICE in trigger_types


@pytest.mark.asyncio
async def test_resolution_sustained_normal_closes_alert():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        for i in range(2):
            pid = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)
        for i in range(2, 4):
            pid = await insert_prediction(session, "area-1", "NORMAL", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.NORMAL
        assert alert.lifecycle_status == LifecycleStatus.RESOLVED
        assert alert.resolved_at is not None

        intents = (await session.execute(select(NotificationIntent).where(NotificationIntent.alert_id == alert.alert_id))).scalars().all()
        trigger_types = {i.trigger_type for i in intents}
        assert TriggerType.RESOLUTION in trigger_types


@pytest.mark.asyncio
async def test_out_of_order_prediction_is_superseded_not_applied():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        pid_new = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(minutes=10))
        await process_risk_event(pid_new, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert = result.scalar_one()
        assert alert.candidate_confirmations == 1

        # An older prediction arrives late.
        pid_old = await insert_prediction(session, "area-1", "CRITICAL", T0)
        await process_risk_event(pid_old, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        alert2 = result.scalar_one()
        # State must be untouched by the late/older prediction.
        assert alert2.candidate_severity == Severity.WARNING
        assert alert2.candidate_confirmations == 1

        audit = (await session.execute(select(AlertAuditLog).where(AlertAuditLog.alert_id == alert.alert_id, AlertAuditLog.event_type == "PREDICTION_SUPERSEDED"))).scalars().all()
        assert len(audit) == 1


@pytest.mark.asyncio
async def test_high_water_mark_blocks_late_prediction_after_closure():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        # Escalate to WARNING then resolve.
        for i in range(2):
            pid = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)
        for i in range(2, 4):
            pid = await insert_prediction(session, "area-1", "NORMAL", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-1"))
        closed_alert = result.scalar_one()
        assert closed_alert.lifecycle_status == LifecycleStatus.RESOLVED

        # A late CRITICAL prediction, timestamped BEFORE the resolution's last
        # evaluated prediction, arrives after closure.
        pid_late = await insert_prediction(session, "area-1", "CRITICAL", T0 + timedelta(seconds=1))
        await process_risk_event(pid_late, session)

        all_alerts = (await session.execute(select(Alert).where(Alert.area_id == "area-1"))).scalars().all()
        assert len(all_alerts) == 1  # no new alert was created
        assert all_alerts[0].lifecycle_status == LifecycleStatus.RESOLVED

        audit = (await session.execute(select(AlertAuditLog).where(AlertAuditLog.event_type == "PREDICTION_SUPERSEDED"))).scalars().all()
        assert any("high-water-mark" in str(a.event_metadata) for a in audit)


@pytest.mark.asyncio
async def test_new_prediction_after_closure_creates_new_alert():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        for i in range(2):
            pid = await insert_prediction(session, "area-1", "WARNING", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)
        for i in range(2, 4):
            pid = await insert_prediction(session, "area-1", "NORMAL", T0 + timedelta(seconds=i * 5))
            await process_risk_event(pid, session)

        # A genuinely newer prediction arrives after resolution.
        pid_new = await insert_prediction(session, "area-1", "WATCH", T0 + timedelta(hours=1))
        await process_risk_event(pid_new, session)

        all_alerts = (await session.execute(select(Alert).where(Alert.area_id == "area-1"))).scalars().all()
        assert len(all_alerts) == 2  # a new incident, old one preserved
        statuses = {a.lifecycle_status for a in all_alerts}
        assert LifecycleStatus.RESOLVED in statuses
        assert LifecycleStatus.ACTIVE in statuses


@pytest.mark.asyncio
async def test_duplicate_webhook_is_idempotent():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        pid = await insert_prediction(session, "area-1", "WARNING", T0)
        await process_risk_event(pid, session)
        await process_risk_event(pid, session)  # duplicate webhook delivery

        alerts = (await session.execute(select(Alert).where(Alert.area_id == "area-1"))).scalars().all()
        assert len(alerts) == 1
        assert alerts[0].candidate_confirmations == 1  # only evaluated once


@pytest.mark.asyncio
async def test_missing_prediction_raises_not_found():
    import uuid
    async with async_session_factory() as session:
        await _set_fast_config(session)
        with pytest.raises(PredictionNotFoundError):
            await process_risk_event(uuid.uuid4(), session)


@pytest.mark.asyncio
async def test_normal_reading_with_no_open_alert_creates_nothing():
    async with async_session_factory() as session:
        await _set_fast_config(session)
        pid = await insert_prediction(session, "area-1", "NORMAL", T0)
        await process_risk_event(pid, session)
        alerts = (await session.execute(select(Alert).where(Alert.area_id == "area-1"))).scalars().all()
        assert len(alerts) == 0
