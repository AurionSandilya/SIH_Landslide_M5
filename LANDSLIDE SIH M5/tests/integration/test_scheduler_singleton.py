import pytest
from sqlalchemy import text

from core.scheduler import release_scheduler_lock, try_acquire_scheduler_lock
from models.db_models import async_engine


@pytest.mark.asyncio
async def test_only_one_instance_can_hold_the_scheduler_lock():
    """Architecture §60a / invariant #19: a second instance attempting the
    same advisory lock key must NOT acquire it while the first holds it -
    simulated here with two independent raw connections, since a session-
    level advisory lock is tied to the connection that took it."""
    conn_a = await async_engine.connect()
    conn_b = await async_engine.connect()
    try:
        key = 999999999
        result_a = await conn_a.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        assert result_a.scalar() is True

        result_b = await conn_b.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        assert result_b.scalar() is False  # second instance does NOT get the lock

        await conn_a.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        result_b2 = await conn_b.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        assert result_b2.scalar() is True  # released -> now available
        await conn_b.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    finally:
        await conn_a.close()
        await conn_b.close()


@pytest.mark.asyncio
async def test_try_acquire_scheduler_lock_module_function():
    won = await try_acquire_scheduler_lock()
    assert won is True
    await release_scheduler_lock()
