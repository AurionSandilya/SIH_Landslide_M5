"""Read-only ORM shims for tables M5 does NOT own (architecture §3).

``risk_predictions`` is M3 schema authority (§7). M5 only ever runs
``SELECT`` against it to re-read a committed prediction by ``prediction_id``
— it never writes to it, never migrates it, and never trusts a webhook
payload as the prediction itself.

This module is intentionally excluded from ``models.db_models.Base`` /
``Base.metadata`` so that ``Base.metadata.create_all()`` (M5's own startup
schema bootstrap) never creates or alters this table in a real deployment,
where M3's migrations own it. For local/dev/test environments that don't
have a running M3 service, ``migrations/0000_test_only_m3_shim.sql``
creates a compatible table so the whole pipeline can be exercised
end-to-end against a single Postgres instance.
"""

from sqlalchemy import Column, DateTime, Float, MetaData, String, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

# Separate metadata/Base on purpose - see module docstring.
external_metadata = MetaData()
ExternalBase = declarative_base(metadata=external_metadata)


class RiskPrediction(ExternalBase):
    __tablename__ = "risk_predictions"

    prediction_id = Column(UUID(as_uuid=True), primary_key=True)
    area_id = Column(String, nullable=False)
    risk_score = Column(Float, nullable=False)
    risk_band = Column(String, nullable=False)  # matches core.enums.Severity values
    confidence = Column(Float, nullable=True)
    prediction_timestamp = Column(DateTime(timezone=True), nullable=False)
    model_version = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
