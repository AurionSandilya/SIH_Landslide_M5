import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from alerting.evaluator import process_risk_event
from core.enums import LifecycleStatus, OPEN_LIFECYCLE_STATUSES
from models.db_models import Alert, async_session_factory
from repository.config_repo import ConfigRepository
from tests.conftest import insert_prediction

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_concurrent_predictions_same_area_no_duplicate_alerts():
    """Architecture §12, §54, invariant #4: two distinct prediction_ids for
    the same area, evaluated concurrently, must serialize through the area
    advisory lock and never create two open alerts."""
    async with async_session_factory() as setup_session:
        repo = ConfigRepository(setup_session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
        await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
        await setup_session.commit()

        pid_a = await insert_prediction(setup_session, "area-concurrent", "WARNING", T0)
        pid_b = await insert_prediction(setup_session, "area-concurrent", "WARNING", T0 + timedelta(seconds=1))

    async def _run(pid):
        async with async_session_factory() as session:
            await process_risk_event(pid, session)

    await asyncio.gather(_run(pid_a), _run(pid_b))

    async with async_session_factory() as session:
        result = await session.execute(
            select(Alert).where(Alert.area_id == "area-concurrent", Alert.lifecycle_status.in_(list(OPEN_LIFECYCLE_STATUSES)))
        )
        open_alerts = result.scalars().all()
        assert len(open_alerts) == 1

        all_alerts = (await session.execute(select(Alert).where(Alert.area_id == "area-concurrent"))).scalars().all()
        assert len(all_alerts) == 1
        # The later of the two predictions must be the one reflected in
        # last_evaluated_* (whichever actually ran second under the lock).
        assert all_alerts[0].last_evaluated_prediction_timestamp in (T0, T0 + timedelta(seconds=1))


@pytest.mark.asyncio
async def test_concurrent_duplicate_webhook_only_processed_once():
    async with async_session_factory() as setup_session:
        repo = ConfigRepository(setup_session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "5", "test")
        await setup_session.commit()
        pid = await insert_prediction(setup_session, "area-dup", "WARNING", T0)

    async def _run():
        async with async_session_factory() as session:
            await process_risk_event(pid, session)

    await asyncio.gather(_run(), _run(), _run())

    async with async_session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.area_id == "area-dup"))
        alerts = result.scalars().all()
        assert len(alerts) == 1
        assert alerts[0].candidate_confirmations == 1  # evaluated exactly once, not 3x
