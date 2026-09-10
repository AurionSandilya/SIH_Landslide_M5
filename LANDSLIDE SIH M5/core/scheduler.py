"""APScheduler singleton enforcement via a Postgres advisory lock
(architecture §60a, invariant #19).

Only one M5 instance may run scheduled reconciliation. Rather than relying
purely on a deployment policy (someone might accidentally scale to 2+
replicas), each instance attempts ``pg_try_advisory_lock(<fixed key>)`` on
a dedicated, long-lived connection at startup. Only the instance that wins
the lock enables its APScheduler jobs; the rest still serve the API
normally, they simply never run reconciliation - turning a documentation
rule into a structural guarantee.

This is a SESSION-level advisory lock (not the transaction-scoped
``pg_advisory_xact_lock`` used for per-area serialization in
``repository/alert_repo.py``) - it must be held on one specific connection
for the whole process lifetime and explicitly unlocked (or the connection
closed) to release it.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from config.settings import settings
from models.db_models import async_engine
from reconciliation.reconciler import run_reconciliation_cycle

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_lock_connection = None
_holds_lock = False


async def try_acquire_scheduler_lock() -> bool:
    """Attempt to become the scheduler-enabled instance. Returns whether
    this process won the lock. Idempotent - safe to call once at startup."""
    global _lock_connection, _holds_lock
    if _holds_lock:
        return True
    conn = await async_engine.connect()
    result = await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": settings.SCHEDULER_LOCK_KEY})
    acquired = bool(result.scalar())
    if acquired:
        _lock_connection = conn
        _holds_lock = True
        logger.info("acquired scheduler singleton lock (key=%s) - this instance will run reconciliation", settings.SCHEDULER_LOCK_KEY)
    else:
        await conn.close()
        logger.info("scheduler singleton lock (key=%s) held elsewhere - this instance is API-only", settings.SCHEDULER_LOCK_KEY)
    return acquired


async def release_scheduler_lock() -> None:
    global _lock_connection, _holds_lock
    if _lock_connection is not None:
        try:
            await _lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": settings.SCHEDULER_LOCK_KEY})
        except Exception:
            logger.exception("failed to explicitly unlock scheduler lock (connection close will release it anyway)")
        await _lock_connection.close()
        _lock_connection = None
    _holds_lock = False


async def start_scheduler_if_leader() -> bool:
    """Try to become the reconciliation leader and, if successful, start
    APScheduler with the reconciliation job. Returns True iff this
    instance is now running the scheduler."""
    global _scheduler
    if not settings.ENABLE_SCHEDULER:
        return False

    won = await try_acquire_scheduler_lock()
    if not won:
        return False

    async def _job():
        await run_reconciliation_cycle()

    from repository.config_repo import ConfigRepository
    from models.db_models import async_session_factory

    async with async_session_factory() as session:
        config_repo = ConfigRepository(session)
        interval_seconds = await config_repo.get_int("reconciliation.interval_seconds", default=300)

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(_job, "interval", seconds=interval_seconds, id="reconciliation", max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("reconciliation scheduler started, interval=%ss", interval_seconds)
    return True


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    await release_scheduler_lock()
