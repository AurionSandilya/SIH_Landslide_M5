class M5Error(Exception):
    """Base exception for M5 service errors."""


class AuthenticationError(M5Error):
    """Raised when internal service authentication fails."""


class ValidationError(M5Error):
    """Raised for request payload validation errors."""


class ProcessingError(M5Error):
    """Raised when alert processing encounters an unexpected state."""


class ExternalServiceError(M5Error):
    """Raised when calls to external services (M3, providers) fail."""


class PredictionNotFoundError(ProcessingError):
    """Raised when a webhook references a prediction_id that is not (yet)
    visible in ``risk_predictions`` after the retry/backoff window (§8)."""


class ConfigError(M5Error):
    """Raised for invalid/missing safety-critical configuration."""
