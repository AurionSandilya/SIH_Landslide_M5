import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from api.routes_health import router as health_router
from api.routes_internal import router as internal_router
from clients.m3_client import close_client
from config.logging_config import configure_logging
from config.settings import settings
from core.scheduler import start_scheduler_if_leader, stop_scheduler
from models.db_models import async_engine, async_session_factory
from notifications.dispatcher import start_dispatcher, stop_dispatcher
from repository.config_repo import ConfigRepository

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Confirm DB connectivity before accepting traffic.
    async with async_engine.begin() as conn:
        await conn.run_sync(lambda _: None)

    # Seed alert_config with bootstrap defaults on first run only - an
    # already-populated table always wins (architecture §19a).
    async with async_session_factory() as session:
        config_repo = ConfigRepository(session)
        inserted = await config_repo.bootstrap_defaults(updated_by="system:startup")
        await session.commit()
        if inserted:
            logger.info("seeded %s default alert_config keys", inserted)

    # Architecture §60a: only the instance that wins the advisory lock runs
    # scheduled reconciliation; every instance still serves the API.
    is_leader = await start_scheduler_if_leader()
    logger.info("scheduler leader for this instance: %s", is_leader)

    await start_dispatcher()
    logger.info("M5 service started (environment=%s)", settings.ENVIRONMENT)

    yield

    await stop_dispatcher()
    await stop_scheduler()
    await close_client()
    await async_engine.dispose()
    logger.info("M5 service shut down")


app = FastAPI(title="M5 Real-Time Data & Alerting Engine", lifespan=lifespan)
app.include_router(internal_router)
app.include_router(health_router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
