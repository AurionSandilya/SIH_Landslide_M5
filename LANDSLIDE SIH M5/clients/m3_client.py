"""Thin async wrapper around the internal M3 service.

M5's ONLY use of M3 over HTTP is recipient/geo-targeting resolution
(architecture §29). Re-reading a committed prediction (§7) is a direct SQL
read against the shared Postgres via ``repository/risk_prediction_repo.py``,
NOT an M3 API call - so there is intentionally no ``get_risk_prediction``
here.

All calls include ``X-Internal-Service-Key`` for authentication (§43); the
key itself is never logged.
"""

import json
import logging
from typing import Any, Dict, List

import httpx

from config.settings import settings
from core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

_SERVICE_KEY_HEADER = "X-Internal-Service-Key"

_client: "httpx.AsyncClient | None" = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=settings.M3_BASE_URL, timeout=settings.M3_HTTP_TIMEOUT_SECONDS)
    return _client


async def resolve_recipients(area_id: str, alert_id: str, severity: str) -> List[Dict[str, Any]]:
    """Resolve notification recipients for a given alert via M3's
    geo-targeting API (architecture §29). M5 never performs its own
    spatial calculations and never queries the user table directly.

    Response shape per §29:
    ``[{"recipient_id", "phone_number"?, "device_token"?,
        "preferred_channels": [...], "priority", "preferred_locale"?}, ...]``
    """
    client = _get_client()
    headers = {_SERVICE_KEY_HEADER: settings.M5_SERVICE_KEY or settings.M3_SERVICE_KEY, "Content-Type": "application/json"}
    payload = {"area_id": area_id, "alert_id": alert_id, "severity": severity}
    try:
        response = await client.post("/recipients/resolve", headers=headers, content=json.dumps(payload))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"M3 recipient resolution failed: {exc}") from exc
    return response.json()


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
