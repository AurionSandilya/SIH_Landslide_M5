"""SQLAlchemy ORM models for all M5-owned tables (architecture §46).

M5-owned:  alerts, notification_intents, alert_deliveries, alert_audit_log,
           processed_risk_events, alert_config, notification_cooldowns
M3-owned:  risk_predictions, risk_zones, recipients, users, ... — M5 only
           ever reads these. See ``models/external_models.py`` for the
           read-only shim used to query ``risk_predictions``; M5 never
           creates/migrates/writes that table (see migrations/README.md).
"""

from typing import AsyncGenerator

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from config.settings import settings
from core.enums import (
    DeliveryChannel,
    DeliveryStatus,
    FailureType,
    LifecycleStatus,
    NotificationIntentStatus,
    ProcessingStatus,
    Severity,
    TriggerType,
)

# A fixed naming convention keeps auto-generated constraint/index names
# stable across environments, which matters once real migrations exist
# (see migrations/).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata_obj = MetaData(naming_convention=NAMING_CONVENTION)
Base = declarative_base(metadata=metadata_obj)

# Async engine for PostgreSQL.
async_engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True, pool_pre_ping=True)

# Session factory for FastAPI dependency injection.
async_session_factory = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session; ensures proper cleanup after request."""
    async with async_session_factory() as session:
        yield session


# --------------------------------------------------------------
# Alert model (architecture §47)
# --------------------------------------------------------------
class Alert(Base):
    __tablename__ = "alerts"

    alert_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    area_id = Column(String, nullable=False)

    current_severity = Column(Enum(Severity, name="severity"), nullable=False, default=Severity.NORMAL)
    candidate_severity = Column(Enum(Severity, name="severity"), nullable=True)
    candidate_since = Column(DateTime(timezone=True), nullable=True)
    candidate_confirmations = Column(Integer, nullable=False, default=0, server_default="0")

    lifecycle_status = Column(Enum(LifecycleStatus, name="lifecycle_status"), nullable=False, default=LifecycleStatus.ACTIVE)

    band_entered_at = Column(DateTime(timezone=True), nullable=True)

    last_prediction_timestamp = Column(DateTime(timezone=True), nullable=True)

    # Composite ordering key of the last prediction that actually changed
    # authoritative state (architecture §17a). Also doubles as this area's
    # high-water mark once the alert becomes terminal (§18).
    last_evaluated_prediction_timestamp = Column(DateTime(timezone=True), nullable=True)
    last_evaluated_created_at = Column(DateTime(timezone=True), nullable=True)
    last_evaluated_prediction_id = Column(UUID(as_uuid=True), nullable=True)

    last_risk_score = Column(Float, nullable=True)
    last_confidence = Column(Float, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    expired_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Invariant #4 / architecture §12: at most one open (ACTIVE or
        # ACKNOWLEDGED) alert per area, enforced at the DB level, not just
        # in application code (Phase 10 of the audit fix list).
        Index(
            "uq_alerts_area_active",
            "area_id",
            unique=True,
            postgresql_where=text("lifecycle_status IN ('ACTIVE', 'ACKNOWLEDGED')"),
        ),
        # Used for the high-water-mark lookup (§18): "most recent alert,
        # any lifecycle status, for this area".
        Index("ix_alerts_area_id_updated_at", "area_id", "updated_at"),
    )


# --------------------------------------------------------------
# Notification Intent model (architecture §48)
# --------------------------------------------------------------
class NotificationIntent(Base):
    __tablename__ = "notification_intents"

    notification_trigger_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    alert_id = Column(UUID(as_uuid=True), ForeignKey("alerts.alert_id"), nullable=False)
    trigger_type = Column(Enum(TriggerType, name="trigger_type"), nullable=False)
    severity = Column(Enum(Severity, name="severity"), nullable=False)
    template_key = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    status = Column(
        Enum(NotificationIntentStatus, name="notification_intent_status"),
        nullable=False,
        default=NotificationIntentStatus.PENDING,
    )

    __table_args__ = (
        Index("ix_notification_intents_status", "status"),
        Index("ix_notification_intents_alert_id", "alert_id"),
    )


# --------------------------------------------------------------
# Alert Delivery model (architecture §49)
# --------------------------------------------------------------
class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"

    delivery_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    alert_id = Column(UUID(as_uuid=True), ForeignKey("alerts.alert_id"), nullable=False)
    notification_trigger_id = Column(
        UUID(as_uuid=True), ForeignKey("notification_intents.notification_trigger_id"), nullable=False
    )
    recipient_id = Column(String, nullable=False)
    channel = Column(Enum(DeliveryChannel, name="delivery_channel"), nullable=False)
    status = Column(Enum(DeliveryStatus, name="delivery_status"), nullable=False, default=DeliveryStatus.PENDING)
    provider_message_id = Column(String, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    error_type = Column(Enum(FailureType, name="failure_type"), nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False
    )

    __table_args__ = (
        # Architecture §25: at-most-once notification intent per
        # (alert, recipient, channel, trigger). This is what makes
        # dispatcher/reconciliation retries safe to run repeatedly.
        UniqueConstraint(
            "alert_id",
            "recipient_id",
            "channel",
            "notification_trigger_id",
            name="uq_alert_delivery_unique",
        ),
        Index("ix_alert_deliveries_status", "status"),
        Index("ix_alert_deliveries_intent", "notification_trigger_id"),
    )


# --------------------------------------------------------------
# Processed Risk Events - idempotency + processing-ownership table (§10, §11)
# --------------------------------------------------------------
class ProcessedRiskEvent(Base):
    __tablename__ = "processed_risk_events"

    prediction_id = Column(UUID(as_uuid=True), primary_key=True)
    # Denormalized from risk_predictions at insert time so reconciliation can
    # find "unprocessed predictions for area X" without a cross-schema join
    # every cycle. Still M5-owned data (a processing cursor), not a copy of
    # M3's canonical prediction record.
    area_id = Column(String, nullable=True)
    prediction_timestamp = Column(DateTime(timezone=True), nullable=True)

    status = Column(Enum(ProcessingStatus, name="processing_status"), nullable=False, default=ProcessingStatus.RECEIVED)
    received_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_error = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_processed_risk_events_status", "status"),
        Index("ix_processed_risk_events_area_id", "area_id"),
    )


# --------------------------------------------------------------
# Alert Config - key/value configuration table (§19a)
# --------------------------------------------------------------
class AlertConfig(Base):
    __tablename__ = "alert_config"

    config_key = Column(String, primary_key=True)
    config_value = Column(String, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False
    )
    updated_by = Column(String, nullable=False)


# --------------------------------------------------------------
# Notification Cooldowns - per alert/channel/trigger (§28)
# --------------------------------------------------------------
class NotificationCooldown(Base):
    __tablename__ = "notification_cooldowns"

    alert_id = Column(UUID(as_uuid=True), ForeignKey("alerts.alert_id"), nullable=False, primary_key=True)
    channel = Column(Enum(DeliveryChannel, name="delivery_channel"), nullable=False, primary_key=True)
    trigger_type = Column(String, nullable=False, primary_key=True)
    cooldown_until = Column(DateTime(timezone=True), nullable=False)


# --------------------------------------------------------------
# Alert Audit Log - append-only (§33, §34)
# --------------------------------------------------------------
class AlertAuditLog(Base):
    __tablename__ = "alert_audit_log"

    audit_id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    alert_id = Column(UUID(as_uuid=True), nullable=True)  # nullable: CONFIG_CHANGED entries have no alert_id
    event_type = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    # NOTE: named event_metadata, not "metadata" - that name collides with
    # SQLAlchemy's reserved ``Base.metadata`` attribute on every declarative
    # model and would raise ``InvalidRequestError`` at import time.
    event_metadata = Column(JSONB, nullable=True)

    __table_args__ = (Index("ix_alert_audit_log_alert_id", "alert_id"),)

    # No UPDATE/DELETE at the application DB-role level - enforced by
    # migrations/0002_audit_immutability.sql, not merely by convention.
