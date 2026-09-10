"""Repository for ``notification_cooldowns`` (architecture §28).

Cooldown is DB-backed (survives restarts) and keyed per
(alert_id, channel, trigger_type) - not a single shared timestamp - so SMS
and push can have independent cooldown policies, and an escalation trigger
type is never blocked by a repeat-notice trigger's cooldown.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.db_models import NotificationCooldown


class CooldownRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, alert_id, channel, trigger_type: str) -> NotificationCooldown | None:
        result = await self.session.execute(
            select(NotificationCooldown).where(
                NotificationCooldown.alert_id == alert_id,
                NotificationCooldown.channel == channel,
                NotificationCooldown.trigger_type == trigger_type,
            )
        )
        return result.scalar_one_or_none()

    async def is_active(self, alert_id, channel, trigger_type: str, now: datetime) -> bool:
        row = await self.get(alert_id, channel, trigger_type)
        return row is not None and row.cooldown_until > now

    async def set_cooldown(self, alert_id, channel, trigger_type: str, cooldown_until: datetime) -> None:
        stmt = (
            pg_insert(NotificationCooldown)
            .values(alert_id=alert_id, channel=channel, trigger_type=trigger_type, cooldown_until=cooldown_until)
            .on_conflict_do_update(
                index_elements=["alert_id", "channel", "trigger_type"],
                set_=dict(cooldown_until=cooldown_until),
            )
        )
        await self.session.execute(stmt)
