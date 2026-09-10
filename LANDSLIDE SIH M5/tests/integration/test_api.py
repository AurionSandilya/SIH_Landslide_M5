from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from api.deps import SERVICE_KEY_HEADER
from config.settings import settings
from main import app
from models.db_models import Alert, async_session_factory
from repository.config_repo import ConfigRepository
from tests.conftest import insert_prediction

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_live_no_auth_required(client):
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "live"}


@pytest.mark.asyncio
async def test_health_ready_checks_db(client):
    resp = await client.get("/health/ready")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_risk_event_rejected_without_service_key(client):
    resp = await client.post("/internal/risk-event", json={"prediction_id": "00000000-0000-0000-0000-000000000001"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_risk_event_rejected_with_wrong_service_key(client):
    resp = await client.post(
        "/internal/risk-event",
        json={"prediction_id": "00000000-0000-0000-0000-000000000001"},
        headers={SERVICE_KEY_HEADER: "wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_risk_event_accepted_with_correct_key_but_unknown_prediction(client):
    resp = await client.post(
        "/internal/risk-event",
        json={"prediction_id": "00000000-0000-0000-0000-000000000099"},
        headers={SERVICE_KEY_HEADER: settings.M3_SERVICE_KEY},
    )
    # Prediction doesn't exist -> deferred to reconciliation, not a 500.
    assert resp.status_code == 202
    assert "not yet visible" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_risk_event_end_to_end_via_http(client):
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        await repo.set("persistence.WARNING.escalate.min_confirmations", "1", "test")
        await repo.set("persistence.WARNING.escalate.min_duration_sec", "0", "test")
        await session.commit()
        pid = await insert_prediction(session, "area-http", "WARNING", datetime.now(timezone.utc))

    resp = await client.post(
        "/internal/risk-event",
        json={"prediction_id": str(pid), "area_id": "area-http"},
        headers={SERVICE_KEY_HEADER: settings.M3_SERVICE_KEY},
    )
    assert resp.status_code == 202

    async with async_session_factory() as session:
        result = await session.execute(select(Alert).where(Alert.area_id == "area-http"))
        alert = result.scalar_one()
        assert alert.current_severity.value == "WARNING"


@pytest.mark.asyncio
async def test_lifecycle_acknowledge_not_found(client):
    resp = await client.post(
        "/internal/lifecycle/acknowledge",
        json={"alert_id": "00000000-0000-0000-0000-000000000099"},
        headers={SERVICE_KEY_HEADER: settings.M3_SERVICE_KEY},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lifecycle_cancel_requires_reason(client):
    resp = await client.post(
        "/internal/lifecycle/cancel",
        json={"alert_id": "00000000-0000-0000-0000-000000000099", "reason": ""},
        headers={SERVICE_KEY_HEADER: settings.M3_SERVICE_KEY},
    )
    assert resp.status_code == 422  # pydantic min_length validation
