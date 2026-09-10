from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or a .env file.

    Only deployment/bootstrap concerns live here (DB URL, credentials, ports).
    Safety-relevant alerting thresholds (persistence windows, cooldowns,
    max data gap, etc.) live in the DB-backed ``alert_config`` table per
    architecture §19a — NOT here — so they can be changed and audited
    without a redeploy. ``core/constants.py`` only supplies the bootstrap
    defaults used to seed that table on first run.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core service configuration
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/m5",
        description="SQLAlchemy async database URL",
    )

    # Service-key authentication (architecture §43). No default in real
    # deployments - M6 owns secret provisioning. A default is provided here
    # only so the module is importable in local/dev/test contexts; a blank
    # key is treated as "authentication disabled" ONLY when
    # ALLOW_INSECURE_NO_SERVICE_KEY is explicitly set (never in prod).
    M3_SERVICE_KEY: str = Field(default="", description="Expected value of X-Internal-Service-Key for M3 -> M5 calls")
    M5_SERVICE_KEY: str = Field(default="", description="Key M5 presents when calling back out, if required")
    ALLOW_INSECURE_NO_SERVICE_KEY: bool = Field(
        default=False,
        description="DEV/TEST ONLY: if true and M3_SERVICE_KEY is unset, internal API auth is skipped.",
    )

    M3_BASE_URL: str = Field(default="http://localhost:8001", description="Base URL for M3 API")
    M3_HTTP_TIMEOUT_SECONDS: float = Field(default=10.0)

    # Optional provider credentials (can be empty; providers no-op/raise if unset)
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_FROM_NUMBER: Optional[str] = None
    FCM_SERVER_KEY: Optional[str] = None

    # Scheduler singleton advisory lock key (architecture §60a). Must be a
    # fixed, distinct integer from the per-area advisory lock keyspace
    # (those are derived from a SHA-256 hash and effectively random; this
    # fixed low value is reserved and documented here).
    SCHEDULER_LOCK_KEY: int = Field(default=987654321, description="Advisory lock key for APScheduler singleton")

    # Notification dispatcher loop interval (how often it looks for PENDING
    # intents outside of reconciliation). Deployment concern, not a safety
    # threshold, so it stays here rather than in alert_config.
    DISPATCH_LOOP_INTERVAL_SECONDS: int = Field(default=15)

    ENABLE_SCHEDULER: bool = Field(default=True, description="Attempt to acquire the scheduler singleton lock on startup")
    ENABLE_DISPATCHER: bool = Field(default=True, description="Run the background notification dispatcher loop")

    # Logging configuration defaults
    LOG_LEVEL: str = Field(default="INFO", description="Logging level for the application")
    LOG_FORMAT_JSON: bool = Field(default=False, description="If true, emit logs as JSON")

    ENVIRONMENT: str = Field(default="development")


# Export a singleton settings instance for import throughout the codebase.
settings = Settings()
