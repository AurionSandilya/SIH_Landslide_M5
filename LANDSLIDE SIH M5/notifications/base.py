import abc
from typing import Any, Dict

from core.enums import FailureType


class ProviderError(Exception):
    """Raised by a notification provider's ``send``. ``failure_type``
    determines retry behavior (architecture §39): TRANSIENT failures are
    retried with bounded backoff, PERMANENT failures are not retried."""

    def __init__(self, message: str, failure_type: FailureType = FailureType.TRANSIENT):
        super().__init__(message)
        self.failure_type = failure_type


class NotificationProvider(abc.ABC):
    """Abstract base class for all notification providers (SMS, Push, etc.).

    Implementations must provide an async ``send`` method that returns a
    dict with at least ``provider_message_id``. On failure, raise
    ``ProviderError`` with the correct ``failure_type`` rather than a bare
    exception, so the dispatcher can classify transient vs permanent
    failures without inspecting provider-specific exception types.
    """

    @abc.abstractmethod
    async def send(self, recipient_id: str, message: str) -> Dict[str, Any]:
        raise NotImplementedError
