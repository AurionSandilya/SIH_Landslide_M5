import logging
from typing import Any, Dict

import httpx

from config.settings import settings
from core.enums import FailureType
from notifications.base import NotificationProvider, ProviderError

logger = logging.getLogger(__name__)


class FCMProvider(NotificationProvider):
    """Firebase Cloud Messaging push provider (legacy HTTP API for the MVP;
    swap for the v1/HTTP-v2 API + service-account auth when moving past
    MVP scale). Without a configured server key this raises a PERMANENT
    ``ProviderError`` rather than pretending to send.
    """

    _API_URL = "https://fcm.googleapis.com/fcm/send"

    def __init__(self):
        self.server_key = settings.FCM_SERVER_KEY

    async def send(self, recipient_id: str, message: str) -> Dict[str, Any]:
        if not self.server_key:
            raise ProviderError("FCM server key missing", failure_type=FailureType.PERMANENT)

        # ``recipient_id`` here is expected to already be a resolved device
        # token (M3's recipient-resolution response, §29).
        headers = {"Authorization": f"key={self.server_key}", "Content-Type": "application/json"}
        payload = {"to": recipient_id, "notification": {"title": "Landslide Alert", "body": message}}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._API_URL, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"FCM request timed out: {exc}", failure_type=FailureType.TRANSIENT) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"FCM request failed: {exc}", failure_type=FailureType.TRANSIENT) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"FCM transient error: HTTP {response.status_code}", failure_type=FailureType.TRANSIENT)
        if response.status_code >= 400:
            raise ProviderError(f"FCM rejected request: HTTP {response.status_code} {response.text[:200]}", failure_type=FailureType.PERMANENT)

        body = response.json()
        if body.get("failure", 0) and not body.get("success", 0):
            raise ProviderError(f"FCM delivery failure: {body}", failure_type=FailureType.PERMANENT)

        message_id = None
        results = body.get("results") or []
        if results:
            message_id = results[0].get("message_id")
        return {"provider_message_id": message_id or body.get("multicast_id")}
