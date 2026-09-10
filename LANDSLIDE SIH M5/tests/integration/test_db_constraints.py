from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from core.enums import LifecycleStatus, Severity
from models.db_models import Alert, async_session_factory

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_partial_unique_index_rejects_second_open_alert_at_db_level():
    """Phase 10 / invariant #4: the 'one open alert per area' rule must be
    a real DB constraint, not just application-level advisory locking.
    This test bypasses the application layer entirely (no advisory lock
    taken, no AlertRepository) to prove Postgres itself rejects it."""
    async with async_session_factory() as session:
        session.add(
            Alert(
                area_id="area-constraint-test",
                current_severity=Severity.WARNING,
                lifecycle_status=LifecycleStatus.ACTIVE,
                last_evaluated_prediction_timestamp=T0,
                last_evaluated_created_at=T0,
            )
        )
        await session.commit()

    async with async_session_factory() as session:
        session.add(
            Alert(
                area_id="area-constraint-test",
                current_severity=Severity.WATCH,
                lifecycle_status=LifecycleStatus.ACKNOWLEDGED,  # also an "open" status
                last_evaluated_prediction_timestamp=T0,
                last_evaluated_created_at=T0,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_partial_unique_index_allows_terminal_plus_open():
    """A terminal alert for an area must NOT block a new open alert for
    the same area (architecture §14b: closed incidents are preserved, new
    ones can start)."""
    async with async_session_factory() as session:
        session.add(
            Alert(
                area_id="area-constraint-test-2",
                current_severity=Severity.NORMAL,
                lifecycle_status=LifecycleStatus.RESOLVED,
                last_evaluated_prediction_timestamp=T0,
                last_evaluated_created_at=T0,
            )
        )
        await session.commit()

    async with async_session_factory() as session:
        session.add(
            Alert(
                area_id="area-constraint-test-2",
                current_severity=Severity.WATCH,
                lifecycle_status=LifecycleStatus.ACTIVE,
                last_evaluated_prediction_timestamp=T0,
                last_evaluated_created_at=T0,
            )
        )
        await session.commit()  # must succeed - no IntegrityError
