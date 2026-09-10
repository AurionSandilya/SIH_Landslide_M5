from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from alerting.evaluator import process_risk_event
from core.enums import LifecycleStatus, ProcessingStatus, Severity
from models.db_models import Alert, ProcessedRiskEvent, async_session_factory
from reconciliation.reconciler import (
    reconcile_stale_areas,
    reconcile_stale_processing,
    reconcile_unprocessed_predictions,
)
from repository.config_repo import ConfigRepository
from tests.conftest import insert_prediction

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_reconciliation_processes_missed_prediction():
    """A prediction committed to risk_predictions but never delivered by
    webhook (e.g. webhook lost) is picked up by reconciliation, via the
    SAME process_risk_event pipeline (architecture §41, invariant #15)."""
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
        await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
        await session.commit()
        # Insert directly into risk_predictions WITHOUT calling process_risk_event -
        # simulates a missed webhook.
        pid = await insert_prediction(session, "area-recon", "WARNING", datetime.now(timezone.utc))

    processed = await reconcile_unprocessed_predictions()
    assert processed >= 1

    async with async_session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.area_id == "area-recon"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.WARNING


@pytest.mark.asyncio
async def test_reconciliation_reclaims_stale_processing():
    """A prediction stuck in PROCESSING past the timeout (simulated crash)
    is reclaimed and re-processed (architecture §11, §37)."""
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
        await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
        await repo.set("reconciliation.stale_processing_timeout_seconds", "1", "test")
        await session.commit()

        pid = await insert_prediction(session, "area-stale", "WARNING", datetime.now(timezone.utc))
        # Simulate a claim that crashed before completing: insert a
        # PROCESSING row with an old processing_started_at, bypassing the
        # normal pipeline (as a real crash would leave it).
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        session.add(
            ProcessedRiskEvent(
                prediction_id=pid,
                area_id="area-stale",
                status=ProcessingStatus.PROCESSING,
                processing_started_at=stale_time,
            )
        )
        await session.commit()

    reclaimed = await reconcile_stale_processing()
    assert reclaimed == 1

    async with async_session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.area_id == "area-stale"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.WARNING

        event = (await session.execute(select(ProcessedRiskEvent).where(ProcessedRiskEvent.prediction_id == pid))).scalar_one()
        assert event.status == ProcessingStatus.COMPLETED


@pytest.mark.asyncio
async def test_reconciliation_expires_stale_area():
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
        await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
        await repo.set("max_prediction_gap_seconds", "60", "test")
        await session.commit()

        # Prediction timestamped far in the past relative to wall-clock now().
        old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        pid = await insert_prediction(session, "area-expire", "WARNING", old_ts)
        await process_risk_event(pid, session)

        result = await session.execute(select(Alert).where(Alert.area_id == "area-expire"))
        alert = result.scalar_one()
        assert alert.lifecycle_status == LifecycleStatus.ACTIVE

    expired = await reconcile_stale_areas()
    assert expired == 1

    async with async_session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.area_id == "area-expire"))
        alert = result.scalar_one()
        assert alert.lifecycle_status == LifecycleStatus.EXPIRED
        assert alert.expired_at is not None


@pytest.mark.asyncio
async def test_reconciliation_does_not_expire_fresh_area():
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
        await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
        await repo.set("max_prediction_gap_seconds", "3600", "test")
        await session.commit()

        pid = await insert_prediction(session, "area-fresh", "WARNING", datetime.now(timezone.utc))
        await process_risk_event(pid, session)

    expired = await reconcile_stale_areas()
    assert expired == 0


@pytest.mark.asyncio
async def test_reconciliation_reprocesses_retryable_prediction():
    """Regression test: a RETRYABLE processed_risk_events row (a previous
    attempt failed and gave up) must actually be re-driven by
    reconcile_unprocessed_predictions, not silently skipped because a row
    already exists for that prediction_id."""
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
        await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
        await session.commit()

        pid = await insert_prediction(session, "area-retryable", "WARNING", datetime.now(timezone.utc))
        # Simulate a previous attempt that failed and was marked RETRYABLE
        # (as evaluator.process_risk_event does on an exception).
        session.add(
            ProcessedRiskEvent(
                prediction_id=pid,
                area_id="area-retryable",
                status=ProcessingStatus.RETRYABLE,
                processing_started_at=datetime.now(timezone.utc),
                last_error="simulated transient DB error",
                retry_count=1,
            )
        )
        await session.commit()

    processed = await reconcile_unprocessed_predictions()
    assert processed == 1

    async with async_session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.area_id == "area-retryable"))
        alert = result.scalar_one()
        assert alert.current_severity == Severity.WARNING

        event = (await session.execute(select(ProcessedRiskEvent).where(ProcessedRiskEvent.prediction_id == pid))).scalar_one()
        assert event.status == ProcessingStatus.COMPLETED
