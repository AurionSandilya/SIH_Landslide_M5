import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from models.db_models import async_engine, async_session_factory
from models.external_models import RiskPrediction
from repository.config_repo import ConfigRepository

_TABLES = [
    "alert_audit_log",
    "notification_cooldowns",
    "alert_deliveries",
    "notification_intents",
    "alerts",
    "processed_risk_events",
    "alert_config",
    "risk_predictions",
]


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    async with async_engine.begin() as conn:
        for table in _TABLES:
            await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
    yield


@pytest_asyncio.fixture
async def session():
    async with async_session_factory() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture(autouse=True)
async def bootstrap_config():
    async with async_session_factory() as s:
        repo = ConfigRepository(s)
        await repo.bootstrap_defaults(updated_by="test")
        await s.commit()


async def insert_prediction(session, area_id: str, risk_band: str, prediction_timestamp: datetime, risk_score: float = 0.5, confidence: float = 0.9) -> uuid.UUID:
    prediction_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO risk_predictions (prediction_id, area_id, risk_score, risk_band, confidence, prediction_timestamp, model_version, created_at) "
            "VALUES (:pid, :area_id, :risk_score, :risk_band, :confidence, :pts, 'test-model-v1', now())"
        ),
        {
            "pid": prediction_id,
            "area_id": area_id,
            "risk_score": risk_score,
            "risk_band": risk_band,
            "confidence": confidence,
            "pts": prediction_timestamp,
        },
    )
    await session.commit()
    return prediction_id
