"""Config read helpers + the audited config-write path (architecture §19a,
invariant #18: every config change that affects an alert decision produces
a ``CONFIG_CHANGED`` audit entry).
"""

from typing import Optional

from core.enums import DeliveryChannel, Severity, TriggerType, AuditEventType
from alerting.persistence import PersistenceThresholds
from repository.audit_repo import AuditRepository
from repository.config_repo import ConfigRepository


async def set_config(config_repo: ConfigRepository, audit_repo: AuditRepository, key: str, value: str, updated_by: str) -> None:
    """Update a config value and write the CONFIG_CHANGED audit entry in the
    same transaction as the caller's session."""
    old = await config_repo.get(key)
    row = await config_repo.set(key, value, updated_by)
    await audit_repo.add_entry(
        alert_id=None,
        event_type=AuditEventType.CONFIG_CHANGED.value,
        metadata={"config_key": key, "old_value": old, "new_value": value, "updated_by": updated_by},
    )


async def get_persistence_thresholds(
    config_repo: ConfigRepository, to_severity: Severity, direction: str
) -> Optional[PersistenceThresholds]:
    """``direction`` is 'escalate', 'downgrade', or 'resolve'. Returns
    ``None`` if this is not a real transition (unused - caller only invokes
    this once a direction has been classified)."""
    if direction == "resolve":
        prefix = "persistence.resolution"
    else:
        prefix = f"persistence.{to_severity.value}.{direction}"

    duration_raw = await config_repo.get(f"{prefix}.min_duration_sec")
    count_raw = await config_repo.get(f"{prefix}.min_confirmations")
    return PersistenceThresholds(
        min_duration_sec=int(duration_raw) if duration_raw is not None else None,
        min_confirmations=int(count_raw) if count_raw is not None else None,
    )


async def get_max_prediction_gap_seconds(config_repo: ConfigRepository) -> int:
    return await config_repo.get_int("max_prediction_gap_seconds", default=3600)


async def get_notify_on_downgrade(config_repo: ConfigRepository) -> bool:
    return await config_repo.get_bool("notify_on_downgrade", default=False)


async def get_cooldown_seconds(config_repo: ConfigRepository, channel: DeliveryChannel, trigger_type: TriggerType) -> int:
    key = f"cooldown.{channel.value}.{trigger_type.value}.seconds"
    return await config_repo.get_int(key, default=0)
