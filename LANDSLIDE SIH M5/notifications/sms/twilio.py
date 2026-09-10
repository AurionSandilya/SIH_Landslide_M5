import logging
from typing import Any, Dict

import httpx

from config.settings import settings
from core.enums import FailureType
from notifications.base import NotificationProvider, ProviderError

logger = logging.getLogger(__name__)


class TwilioProvider(NotificationProvider):
    """Twilio SMS provider.

    Uses Twilio's REST API directly via httpx rather than the twilio SDK to
    avoid an extra dependency for the MVP. Real credentials are required to
    actually send; without them this raises a PERMANENT ``ProviderError``
    immediately (a config problem, not something backoff can fix) - it does
    NOT fabricate a fake successful send.
    """

    _API_BASE = "https://api.twilio.com/2010-04-01"

    def __init__(self):
        self.account_sid = settings.TWILIO_ACCOUNT_SID
        self.auth_token = settings.TWILIO_AUTH_TOKEN
        self.from_number = settings.TWILIO_FROM_NUMBER

    async def send(self, recipient_id: str, message: str) -> Dict[str, Any]:
        if not (self.account_sid and self.auth_token and self.from_number):
            raise ProviderError("Twilio credentials not configured", failure_type=FailureType.PERMANENT)

        # ``recipient_id`` here is expected to already be a resolved phone
        # number (M3's recipient-resolution response, §29) - M5 never looks
        # up contact info itself.
        url = f"{self._API_BASE}/Accounts/{self.account_sid}/Messages.json"
        data = {"To": recipient_id, "From": self.from_number, "Body": message}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, data=data, auth=(self.account_sid, self.auth_token))
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Twilio request timed out: {exc}", failure_type=FailureType.TRANSIENT) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Twilio request failed: {exc}", failure_type=FailureType.TRANSIENT) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"Twilio transient error: HTTP {response.status_code}", failure_type=FailureType.TRANSIENT)
        if response.status_code >= 400:
            raise ProviderError(f"Twilio rejected request: HTTP {response.status_code} {response.text[:200]}", failure_type=FailureType.PERMANENT)

        body = response.json()
        return {"provider_message_id": body.get("sid")}
