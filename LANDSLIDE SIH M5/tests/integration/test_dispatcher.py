from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from sqlalchemy import select

import notifications.dispatcher as dispatcher_module
from core.enums import (
    DeliveryChannel,
    DeliveryStatus,
    FailureType,
    NotificationIntentStatus,
    Severity,
    TriggerType,
)
from models.db_models import Alert, AlertDelivery, NotificationCooldown, NotificationIntent, async_session_factory
from notifications.base import ProviderError
from repository.alert_repo import AlertRepository
from repository.config_repo import ConfigRepository
from repository.notification_repo import NotificationRepository

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls: List[tuple] = []

    async def send(self, recipient_id: str, message: str) -> Dict[str, Any]:
        self.calls.append((recipient_id, message))
        if self.fail:
            raise self.fail
        return {"provider_message_id": f"fake-{recipient_id}"}


async def _make_alert_with_intent(session, area_id="area-notif", severity=Severity.WARNING, trigger_type=TriggerType.WARNING_ESCALATION):
    alert_repo = AlertRepository(session)
    alert = await alert_repo.create(
        area_id=area_id,
        current_severity=severity,
        lifecycle_status="ACTIVE",
        last_evaluated_prediction_timestamp=T0,
        last_evaluated_created_at=T0,
    )
    notif_repo = NotificationRepository(session)
    intent = await notif_repo.create_intent(
        alert_id=alert.alert_id,
        trigger_type=trigger_type,
        severity=severity,
        template_key="warning_escalation.warning",
        status=NotificationIntentStatus.PENDING,
    )
    await session.commit()
    return alert, intent


@pytest.mark.asyncio
async def test_dispatch_creates_delivery_and_marks_sent(monkeypatch):
    async with async_session_factory() as session:
        alert, intent = await _make_alert_with_intent(session)

    fake_sms = FakeProvider()

    async def fake_resolve(area_id, alert_id, severity):
        return [{"recipient_id": "+911234567890", "channel": "SMS"}]

    monkeypatch.setattr(dispatcher_module, "resolve_recipients", fake_resolve)
    monkeypatch.setitem(dispatcher_module._PROVIDERS, DeliveryChannel.SMS, fake_sms)

    processed = await dispatcher_module.dispatch_pending_batch()
    assert processed == 1
    assert len(fake_sms.calls) == 1

    async with async_session_factory() as session:
        deliveries = (await session.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert.alert_id))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == DeliveryStatus.SENT
        assert deliveries[0].provider_message_id == "fake-+911234567890"

        updated_intent = (await session.execute(select(NotificationIntent).where(NotificationIntent.notification_trigger_id == intent.notification_trigger_id))).scalar_one()
        assert updated_intent.status == NotificationIntentStatus.DISPATCHED

        cooldown = (await session.execute(select(NotificationCooldown).where(NotificationCooldown.alert_id == alert.alert_id))).scalar_one()
        assert cooldown.cooldown_until > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_dispatch_does_not_resend_when_in_cooldown(monkeypatch):
    async with async_session_factory() as session:
        alert, intent1 = await _make_alert_with_intent(session)
        # Manually set an active cooldown for this alert/channel/trigger.
        from repository.cooldown_repo import CooldownRepository
        from datetime import timedelta

        cooldown_repo = CooldownRepository(session)
        await cooldown_repo.set_cooldown(alert.alert_id, DeliveryChannel.SMS, TriggerType.WARNING_ESCALATION.value, datetime.now(timezone.utc) + timedelta(minutes=30))
        await session.commit()

    fake_sms = FakeProvider()

    async def fake_resolve(area_id, alert_id, severity):
        return [{"recipient_id": "+911234567890", "channel": "SMS"}]

    monkeypatch.setattr(dispatcher_module, "resolve_recipients", fake_resolve)
    monkeypatch.setitem(dispatcher_module._PROVIDERS, DeliveryChannel.SMS, fake_sms)

    await dispatcher_module.dispatch_pending_batch()
    assert len(fake_sms.calls) == 0  # suppressed by cooldown

    async with async_session_factory() as session:
        deliveries = (await session.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert.alert_id))).scalars().all()
        assert len(deliveries) == 0


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried(monkeypatch):
    async with async_session_factory() as session:
        alert, intent = await _make_alert_with_intent(session)

    fake_sms = FakeProvider(fail=ProviderError("bad number", FailureType.PERMANENT))

    async def fake_resolve(area_id, alert_id, severity):
        return [{"recipient_id": "+911234567890", "channel": "SMS"}]

    monkeypatch.setattr(dispatcher_module, "resolve_recipients", fake_resolve)
    monkeypatch.setitem(dispatcher_module._PROVIDERS, DeliveryChannel.SMS, fake_sms)

    await dispatcher_module.dispatch_pending_batch()
    assert len(fake_sms.calls) == 1

    async with async_session_factory() as session:
        deliveries = (await session.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert.alert_id))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == DeliveryStatus.FAILED
        assert deliveries[0].error_type == FailureType.PERMANENT

    # A second dispatch pass must not attempt this recipient again -
    # nothing PENDING is left to claim, but simulate a fresh intent from a
    # new escalation to prove permanent failures are excluded from resend
    # via get_or_create_pending_delivery's existing-row short-circuit.
    async with async_session_factory() as session:
        notif_repo = NotificationRepository(session)
        await notif_repo.set_intent_status(intent.notification_trigger_id, NotificationIntentStatus.PENDING)
        await session.commit()

    await dispatcher_module.dispatch_pending_batch()
    assert len(fake_sms.calls) == 1  # still only the original attempt


@pytest.mark.asyncio
async def test_transient_failure_recorded_for_retry(monkeypatch):
    async with async_session_factory() as session:
        alert, intent = await _make_alert_with_intent(session)

    fake_sms = FakeProvider(fail=ProviderError("timeout", FailureType.TRANSIENT))

    async def fake_resolve(area_id, alert_id, severity):
        return [{"recipient_id": "+911234567890", "channel": "SMS"}]

    monkeypatch.setattr(dispatcher_module, "resolve_recipients", fake_resolve)
    monkeypatch.setitem(dispatcher_module._PROVIDERS, DeliveryChannel.SMS, fake_sms)

    await dispatcher_module.dispatch_pending_batch()

    async with async_session_factory() as session:
        deliveries = (await session.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert.alert_id))).scalars().all()
        assert deliveries[0].status == DeliveryStatus.FAILED
        assert deliveries[0].error_type == FailureType.TRANSIENT
        assert deliveries[0].attempt_count == 1

        from repository.notification_repo import NotificationRepository as NR
        retryable = await NR(session).list_retryable_deliveries()
        # PENDING/SENDING only - a FAILED-transient row is picked up via
        # reconciliation re-driving the intent, not via list_retryable_deliveries
        # directly (documented limitation - see final report).


@pytest.mark.asyncio
async def test_m3_failure_leaves_intent_pending(monkeypatch):
    async with async_session_factory() as session:
        alert, intent = await _make_alert_with_intent(session)

    async def fake_resolve_fail(area_id, alert_id, severity):
        raise RuntimeError("M3 unreachable")

    monkeypatch.setattr(dispatcher_module, "resolve_recipients", fake_resolve_fail)

    await dispatcher_module.dispatch_pending_batch()

    async with async_session_factory() as session:
        updated_intent = (await session.execute(select(NotificationIntent).where(NotificationIntent.notification_trigger_id == intent.notification_trigger_id))).scalar_one()
        assert updated_intent.status == NotificationIntentStatus.PENDING  # never silently discarded


@pytest.mark.asyncio
async def test_reconciliation_requeues_transient_failed_delivery_for_retry(monkeypatch):
    """Regression: a TRANSIENT delivery failure alone was never retried -
    the intent moved to DISPATCHED and nothing ever revisited it. This
    proves reconcile_notifications requeues it and the retry succeeds."""
    from reconciliation.reconciler import reconcile_notifications

    async with async_session_factory() as session:
        alert, intent = await _make_alert_with_intent(session)

    failing_sms = FakeProvider(fail=ProviderError("timeout", FailureType.TRANSIENT))

    async def fake_resolve(area_id, alert_id, severity):
        return [{"recipient_id": "+911234567890", "channel": "SMS"}]

    monkeypatch.setattr(dispatcher_module, "resolve_recipients", fake_resolve)
    monkeypatch.setitem(dispatcher_module._PROVIDERS, DeliveryChannel.SMS, failing_sms)

    # First dispatch: fails transiently.
    await dispatcher_module.dispatch_pending_batch()
    async with async_session_factory() as session:
        delivery = (await session.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert.alert_id))).scalar_one()
        assert delivery.status == DeliveryStatus.FAILED
        assert delivery.error_type == FailureType.TRANSIENT
        assert delivery.attempt_count == 1
        intent_row = (await session.execute(select(NotificationIntent).where(NotificationIntent.notification_trigger_id == intent.notification_trigger_id))).scalar_one()
        assert intent_row.status == NotificationIntentStatus.DISPATCHED  # nothing retries this on its own

    # Now the provider recovers; reconciliation should requeue + succeed.
    working_sms = FakeProvider()
    monkeypatch.setitem(dispatcher_module._PROVIDERS, DeliveryChannel.SMS, working_sms)
    await reconcile_notifications()

    async with async_session_factory() as session:
        delivery = (await session.execute(select(AlertDelivery).where(AlertDelivery.alert_id == alert.alert_id))).scalar_one()
        assert delivery.status == DeliveryStatus.SENT
        assert delivery.attempt_count == 2  # original attempt + retry
    assert len(working_sms.calls) == 1
