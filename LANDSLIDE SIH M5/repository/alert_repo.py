"""Repository for the ``Alert`` entity."""

import hashlib
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import OPEN_LIFECYCLE_STATUSES
from models.db_models import Alert


def area_lock_key(area_id: str) -> int:
    """Deterministic 64-bit signed integer for ``pg_advisory_xact_lock``,
    derived from ``area_id`` (architecture §12). SHA-256 keeps collisions
    negligible; truncated to 8 bytes to fit Postgres' bigint lock key.
    """
    digest = hashlib.sha256(area_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class AlertRepository:
    """CRUD + area-scoped queries for the Alert model."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def acquire_area_lock(self, area_id: str) -> None:
        """Transaction-scoped advisory lock, automatically released on
        COMMIT/ROLLBACK - serializes concurrent evaluations of the same
        area (architecture §12, invariant #4)."""
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": area_lock_key(area_id)})

    async def get_by_id(self, alert_id) -> Optional[Alert]:
        result = await self.session.execute(select(Alert).where(Alert.alert_id == alert_id))
        return result.scalar_one_or_none()

    async def get_open_for_area(self, area_id: str) -> Optional[Alert]:
        """The single ACTIVE/ACKNOWLEDGED alert for this area, if any.
        Must be called AFTER ``acquire_area_lock`` in the same transaction
        so the read reflects a consistent, serialized view."""
        result = await self.session.execute(
            select(Alert).where(
                Alert.area_id == area_id,
                Alert.lifecycle_status.in_(list(OPEN_LIFECYCLE_STATUSES)),
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_for_area(self, area_id: str) -> Optional[Alert]:
        """Most recent alert for this area regardless of lifecycle status
        (including terminal). Used to compute the area's high-water mark
        (architecture §18) when there is no currently-open alert - a
        terminal alert's ``last_evaluated_*`` composite key is reused
        rather than standing up a new table."""
        result = await self.session.execute(
            select(Alert).where(Alert.area_id == area_id).order_by(Alert.updated_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def create(self, **kwargs) -> Alert:
        obj = Alert(**kwargs)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def save(self, alert: Alert) -> Alert:
        await self.session.flush()
        return alert
