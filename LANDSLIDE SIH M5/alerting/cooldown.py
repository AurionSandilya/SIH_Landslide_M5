"""Per-channel/trigger notification cooldown (architecture §28).

Cooldown gates *repeat* notifications of the same ``trigger_type`` for the
same alert (e.g. severity flaps WARNING -> WATCH -> WARNING again within
one open alert, re-firing ``WARNING_ESCALATION``). A genuinely new
``trigger_type`` (e.g. ``CRITICAL_ESCALATION`` following a
``WARNING_ESCALATION``) is a distinct cooldown key by construction, so
escalation to a new severity is never blocked by a lower severity's
cooldown - no special-case bypass logic is needed, it falls out of the key
shape directly.

Cooldown is evaluated per channel (SMS/PUSH may have different windows)
at delivery-creation time in the dispatcher, since ``notification_intents``
is channel-agnostic and fan-out to channels only happens at dispatch.
"""

from datetime import datetime, timedelta, timezone

from alerting.config_service import get_cooldown_seconds
from core.enums import DeliveryChannel, TriggerType
from repository.config_repo import ConfigRepository
from repository.cooldown_repo import CooldownRepository


async def is_in_cooldown(
    cooldown_repo: CooldownRepository, alert_id, channel: DeliveryChannel, trigger_type: TriggerType
) -> bool:
    return await cooldown_repo.is_active(alert_id, channel, trigger_type.value, datetime.now(timezone.utc))


async def start_cooldown(
    cooldown_repo: CooldownRepository,
    config_repo: ConfigRepository,
    alert_id,
    channel: DeliveryChannel,
    trigger_type: TriggerType,
) -> None:
    seconds = await get_cooldown_seconds(config_repo, channel, trigger_type)
    if seconds <= 0:
        return
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    await cooldown_repo.set_cooldown(alert_id, channel, trigger_type.value, until)
