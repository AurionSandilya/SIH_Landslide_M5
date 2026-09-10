from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from alerting.evaluator import process_risk_event
from alerting.lifecycle_actions import LifecycleActionResult, acknowledge_alert, cancel_alert
from core.enums import LifecycleStatus
from models.db_models import Alert, async_session_factory
from repository.config_repo import ConfigRepository
from tests.conftest import insert_prediction

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


async def _make_open_alert(session, area_id="area-1"):
    repo = ConfigRepository(session)
    await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
    await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
    await session.commit()
    pid = await insert_prediction(session, area_id, "WARNING", T0)
    await process_risk_event(pid, session)
    result = await session.execute(select(Alert).where(Alert.area_id == area_id))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_acknowledge_active_alert():
    async with async_session_factory() as session:
        alert = await _make_open_alert(session)
        outcome = await acknowledge_alert(session, alert.alert_id)
        await session.commit()
        assert outcome.result == LifecycleActionResult.APPLIED
        assert outcome.lifecycle_status == LifecycleStatus.ACKNOWLEDGED


@pytest.mark.asyncio
async def test_acknowledge_is_idempotent():
    async with async_session_factory() as session:
        alert = await _make_open_alert(session)
        await acknowledge_alert(session, alert.alert_id)
        await session.commit()
        outcome = await acknowledge_alert(session, alert.alert_id)
        await session.commit()
        assert outcome.result == LifecycleActionResult.NOOP


@pytest.mark.asyncio
async def test_cancel_requires_reason_and_sets_terminal():
    async with async_session_factory() as session:
        alert = await _make_open_alert(session)
        outcome = await cancel_alert(session, alert.alert_id, reason="false positive")
        await session.commit()
        assert outcome.result == LifecycleActionResult.APPLIED
        assert outcome.lifecycle_status == LifecycleStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_is_idempotent():
    async with async_session_factory() as session:
        alert = await _make_open_alert(session)
        await cancel_alert(session, alert.alert_id, reason="false positive")
        await session.commit()
        outcome = await cancel_alert(session, alert.alert_id, reason="again")
        await session.commit()
        assert outcome.result == LifecycleActionResult.NOOP


@pytest.mark.asyncio
async def test_cannot_acknowledge_terminal_alert():
    async with async_session_factory() as session:
        alert = await _make_open_alert(session)
        await cancel_alert(session, alert.alert_id, reason="done")
        await session.commit()
        outcome = await acknowledge_alert(session, alert.alert_id)
        await session.commit()
        assert outcome.result == LifecycleActionResult.ALREADY_TERMINAL


@pytest.mark.asyncio
async def test_acknowledge_not_found():
    import uuid
    async with async_session_factory() as session:
        outcome = await acknowledge_alert(session, uuid.uuid4())
        assert outcome.result == LifecycleActionResult.NOT_FOUND
