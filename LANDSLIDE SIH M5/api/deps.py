"""Internal-API authentication (architecture §43, Phase 8).

Every internal endpoint requires ``X-Internal-Service-Key`` to match the
configured M3->M5 key. The key is never logged (including in error paths -
only a boolean match result and, on failure, a truncated/no value is ever
touched by logging). ``settings.ALLOW_INSECURE_NO_SERVICE_KEY`` exists only
for local dev/tests and must never be set in production; M6 owns real
secret provisioning/rotation.
"""

import hmac
import logging

from fastapi import Header, HTTPException, status

from config.settings import settings

logger = logging.getLogger(__name__)

SERVICE_KEY_HEADER = "X-Internal-Service-Key"


async def require_service_key(x_internal_service_key: str | None = Header(default=None, alias=SERVICE_KEY_HEADER)) -> None:
    expected = settings.M3_SERVICE_KEY

    if not expected:
        if settings.ALLOW_INSECURE_NO_SERVICE_KEY:
            logger.warning("internal API authentication is DISABLED (ALLOW_INSECURE_NO_SERVICE_KEY=true) - dev/test only")
            return
        logger.error("M3_SERVICE_KEY is not configured; rejecting internal API request")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service not configured")

    if not x_internal_service_key or not hmac.compare_digest(x_internal_service_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing service key")
