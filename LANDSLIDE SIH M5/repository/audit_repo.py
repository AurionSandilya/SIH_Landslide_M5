"""Repository for ``alert_audit_log`` (append-only - architecture §33/§34).

Only ``INSERT``/``SELECT`` are ever issued here - there is no update/delete
method on this repository at all, and the DB role enforces the same rule
independently (migrations/0002_audit_immutability.sql) so a bug here can't
silently defeat the guarantee.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.db_models import AlertAuditLog


class AuditRepository:
    """Writes immutable audit entries for alert + config events."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_entry(self, alert_id: Optional[UUID], event_type: str, metadata: Optional[dict] = None) -> AlertAuditLog:
        entry = AlertAuditLog(alert_id=alert_id, event_type=event_type, event_metadata=metadata)
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_alert(self, alert_id: UUID) -> list[AlertAuditLog]:
        result = await self.session.execute(
            select(AlertAuditLog).where(AlertAuditLog.alert_id == alert_id).order_by(AlertAuditLog.timestamp.asc())
        )
        return list(result.scalars().all())
