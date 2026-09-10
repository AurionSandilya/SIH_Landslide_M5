"""Human-triggered lifecycle actions: acknowledge / cancel (architecture
§31, §32). M5 remains authoritative for the actual state transition even
though the request originates from an M4 operator relayed through M3.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import AuditEventType, LifecycleStatus, TERMINAL_LIFECYCLE_STATUSES
from repository.alert_repo import AlertRepository
from repository.audit_repo import AuditRepository


class LifecycleActionResult(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    NOOP = "NOOP"
    APPLIED = "APPLIED"


@dataclass
class LifecycleActionOutcome:
    result: LifecycleActionResult
    alert_id: Optional[UUID] = None
    lifecycle_status: Optional[LifecycleStatus] = None


async def acknowledge_alert(session: AsyncSession, alert_id: UUID) -> LifecycleActionOutcome:
    alert_repo = AlertRepository(session)
    audit_repo = AuditRepository(session)

    # Load first to discover area_id, then serialize against risk-event
    # processing for the same area via the same advisory lock used by the
    # evaluator (architecture §12), then re-read for a consistent view.
    alert = await alert_repo.get_by_id(alert_id)
    if alert is None:
        return LifecycleActionOutcome(result=LifecycleActionResult.NOT_FOUND)

    await alert_repo.acquire_area_lock(alert.area_id)
    alert = await alert_repo.get_by_id(alert_id)
    if alert is None:
        return LifecycleActionOutcome(result=LifecycleActionResult.NOT_FOUND)

    if alert.lifecycle_status in TERMINAL_LIFECYCLE_STATUSES:
        # Architecture §32: terminal states never transition back to ACTIVE
        # via a lifecycle action.
        return LifecycleActionOutcome(
            result=LifecycleActionResult.ALREADY_TERMINAL, alert_id=alert.alert_id, lifecycle_status=alert.lifecycle_status
        )

    if alert.lifecycle_status == LifecycleStatus.ACKNOWLEDGED:
        await audit_repo.add_entry(alert.alert_id, AuditEventType.LIFECYCLE_ACKNOWLEDGE_NOOP.value, {})
        return LifecycleActionOutcome(
            result=LifecycleActionResult.NOOP, alert_id=alert.alert_id, lifecycle_status=alert.lifecycle_status
        )

    alert.lifecycle_status = LifecycleStatus.ACKNOWLEDGED
    await alert_repo.save(alert)
    await audit_repo.add_entry(alert.alert_id, AuditEventType.LIFECYCLE_ACKNOWLEDGED.value, {})
    return LifecycleActionOutcome(
        result=LifecycleActionResult.APPLIED, alert_id=alert.alert_id, lifecycle_status=alert.lifecycle_status
    )


async def cancel_alert(session: AsyncSession, alert_id: UUID, reason: str) -> LifecycleActionOutcome:
    alert_repo = AlertRepository(session)
    audit_repo = AuditRepository(session)

    alert = await alert_repo.get_by_id(alert_id)
    if alert is None:
        return LifecycleActionOutcome(result=LifecycleActionResult.NOT_FOUND)

    await alert_repo.acquire_area_lock(alert.area_id)
    alert = await alert_repo.get_by_id(alert_id)
    if alert is None:
        return LifecycleActionOutcome(result=LifecycleActionResult.NOT_FOUND)

    if alert.lifecycle_status == LifecycleStatus.CANCELLED:
        await audit_repo.add_entry(alert.alert_id, AuditEventType.LIFECYCLE_CANCEL_NOOP.value, {"reason": reason})
        return LifecycleActionOutcome(
            result=LifecycleActionResult.NOOP, alert_id=alert.alert_id, lifecycle_status=alert.lifecycle_status
        )

    if alert.lifecycle_status in TERMINAL_LIFECYCLE_STATUSES:
        return LifecycleActionOutcome(
            result=LifecycleActionResult.ALREADY_TERMINAL, alert_id=alert.alert_id, lifecycle_status=alert.lifecycle_status
        )

    alert.lifecycle_status = LifecycleStatus.CANCELLED
    alert.cancelled_at = datetime.now(timezone.utc)
    await alert_repo.save(alert)
    await audit_repo.add_entry(alert.alert_id, AuditEventType.LIFECYCLE_CANCELLED.value, {"reason": reason})
    return LifecycleActionOutcome(
        result=LifecycleActionResult.APPLIED, alert_id=alert.alert_id, lifecycle_status=alert.lifecycle_status
    )
