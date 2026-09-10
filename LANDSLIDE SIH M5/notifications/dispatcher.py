"""Notification dispatcher — Unit 2 (architecture §24, §24a, §36).

External provider / M3 calls NEVER happen inside a DB transaction. Each
intent is processed in three phases with commits between them:

  1. (external) resolve recipients via M3
  2. (DB txn)   cooldown check + idempotent PENDING delivery rows, commit
  3. (external) provider .send() per delivery
  4. (DB txn)   record results + start cooldowns + close out the intent, commit

``dispatch_pending_batch`` is called both by the periodic background loop
AND by reconciliation's notification-recovery step (architecture §41) - one
implementation, two callers, per invariant #15.
"""

import asyncio
import logging
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from alerting.cooldown import is_in_cooldown, start_cooldown
from clients.m3_client import resolve_recipients
from config.settings import settings
from core.enums import DeliveryChannel, DeliveryStatus, FailureType, NotificationIntentStatus
from models.db_models import AlertDelivery, async_session_factory
from notifications.base import NotificationProvider, ProviderError
from notifications.push.fcm import FCMProvider
from notifications.sms.twilio import TwilioProvider
from notifications.templates import render_message
from repository.alert_repo import AlertRepository
from repository.config_repo import ConfigRepository
from repository.cooldown_repo import CooldownRepository
from repository.notification_repo import NotificationRepository

logger = logging.getLogger(__name__)

_dispatcher_task: "asyncio.Task | None" = None

_PROVIDERS: Dict[DeliveryChannel, NotificationProvider] = {
    DeliveryChannel.SMS: TwilioProvider(),
    DeliveryChannel.PUSH: FCMProvider(),
}


async def _process_one_intent(session: AsyncSession, intent_id) -> None:
    notif_repo = NotificationRepository(session)
    alert_repo = AlertRepository(session)
    config_repo = ConfigRepository(session)
    cooldown_repo = CooldownRepository(session)

    intent = await notif_repo.get_intent_by_id(intent_id)
    if intent is None:
        return
    alert = await alert_repo.get_by_id(intent.alert_id)
    if alert is None:
        logger.error("alert %s not found for intent %s", intent.alert_id, intent.notification_trigger_id)
        await notif_repo.set_intent_status(intent.notification_trigger_id, NotificationIntentStatus.FAILED)
        await session.commit()
        return

    # ---- Phase 1: external - resolve recipients via M3 --------------------
    try:
        recipients: List[Dict[str, Any]] = await resolve_recipients(
            area_id=alert.area_id, alert_id=str(intent.alert_id), severity=alert.current_severity.value
        )
    except Exception:
        logger.exception("M3 recipient resolution failed for intent %s", intent.notification_trigger_id)
        # Architecture §38: M3 failure -> intent stays PENDING, never
        # silently discarded, so the next dispatch/reconciliation pass
        # retries it once M3 recovers.
        await notif_repo.set_intent_status(intent.notification_trigger_id, NotificationIntentStatus.PENDING)
        await session.commit()
        return

    # ---- Phase 2: DB txn - cooldown filter + idempotent PENDING deliveries
    to_attempt = []  # list of (delivery_id, channel, recipient_id)
    for recipient in recipients:
        channel_str = (recipient.get("channel") or recipient.get("preferred_channels", [None])[0] or "").upper()
        recipient_id = recipient.get("recipient_id")
        try:
            channel = DeliveryChannel(channel_str)
        except ValueError:
            logger.warning("unknown channel '%s' for recipient %s, skipping", channel_str, recipient_id)
            continue

        if await is_in_cooldown(cooldown_repo, intent.alert_id, channel, intent.trigger_type):
            logger.info(
                "notification suppressed by cooldown alert=%s channel=%s trigger=%s",
                intent.alert_id, channel.value, intent.trigger_type.value,
            )
            continue

        delivery, created = await notif_repo.get_or_create_pending_delivery(
            alert_id=intent.alert_id,
            notification_trigger_id=intent.notification_trigger_id,
            recipient_id=recipient_id,
            channel=channel,
            status=DeliveryStatus.PENDING,
        )
        if delivery.status in (DeliveryStatus.SENT, DeliveryStatus.DELIVERED):
            continue  # already delivered - never resend (architecture §37)
        if delivery.status == DeliveryStatus.FAILED and delivery.error_type == FailureType.PERMANENT:
            continue  # permanent failure - never retried (architecture §39)
        to_attempt.append((delivery.delivery_id, channel, recipient_id))
    await session.commit()

    # ---- Phase 3: external - provider sends --------------------------------
    message = render_message(
        intent.template_key, locale="en", facts={"area_id": alert.area_id, "severity": alert.current_severity.value}
    )
    results = []  # (delivery_id, channel, ok, payload_or_error)
    for delivery_id, channel, recipient_id in to_attempt:
        provider = _PROVIDERS.get(channel)
        if provider is None:
            results.append((delivery_id, channel, False, ProviderError("no provider for channel", FailureType.PERMANENT)))
            continue
        try:
            payload = await provider.send(recipient_id, message)
            results.append((delivery_id, channel, True, payload))
        except ProviderError as exc:
            results.append((delivery_id, channel, False, exc))
        except Exception as exc:  # unexpected provider bug - treat as transient, never crash the batch
            logger.exception("unexpected provider error")
            results.append((delivery_id, channel, False, ProviderError(str(exc), FailureType.TRANSIENT)))

    # ---- Phase 4: DB txn - record results + cooldowns + close out intent --
    max_retries = await config_repo.get_int("dispatch.max_retries", default=3)
    for delivery_id, channel, ok, payload in results:
        if ok:
            await notif_repo.update_delivery_status(
                delivery_id,
                DeliveryStatus.SENT,
                provider_message_id=payload.get("provider_message_id"),
                attempt_count=AlertDelivery.attempt_count + 1,
                last_attempt_at=_now(),
                sent_at=_now(),
            )
            await start_cooldown(cooldown_repo, config_repo, intent.alert_id, channel, intent.trigger_type)
        else:
            exc: ProviderError = payload
            delivery = await notif_repo.get_delivery_by_id(delivery_id)
            attempt_count = (delivery.attempt_count if delivery else 0) + 1
            final = exc.failure_type == FailureType.PERMANENT or attempt_count >= max_retries
            await notif_repo.update_delivery_status(
                delivery_id,
                DeliveryStatus.FAILED,
                error_type=exc.failure_type,
                error_message=str(exc)[:2000],
                attempt_count=attempt_count,
                last_attempt_at=_now(),
            )
            if not final:
                logger.info("delivery %s transient failure, attempt %s/%s, will retry via reconciliation", delivery_id, attempt_count, max_retries)

    await notif_repo.set_intent_status(intent.notification_trigger_id, NotificationIntentStatus.DISPATCHED)
    await session.commit()


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def dispatch_pending_batch(batch_size: int | None = None) -> int:
    """Claim + process up to ``batch_size`` PENDING intents. Returns the
    number of intents processed. Uses its own session so it is safe to call
    from both the background loop and reconciliation."""
    async with async_session_factory() as session:
        config_repo = ConfigRepository(session)
        if batch_size is None:
            batch_size = await config_repo.get_int("dispatch.batch_size", default=100)
        notif_repo = NotificationRepository(session)
        intents = await notif_repo.claim_pending_batch(batch_size)
        await session.commit()

        processed = 0
        for intent in intents:
            try:
                await _process_one_intent(session, intent.notification_trigger_id)
            except Exception:
                logger.exception("failed processing intent %s", intent.notification_trigger_id)
                await session.rollback()
                async with session.begin():
                    await NotificationRepository(session).set_intent_status(
                        intent.notification_trigger_id, NotificationIntentStatus.PENDING
                    )
            processed += 1

        batch_delay = await config_repo.get_float("dispatch.batch_delay_seconds", default=1.0)
        if processed and batch_delay:
            await asyncio.sleep(batch_delay)
        return processed


async def _dispatch_loop() -> None:
    logger.info("Notification dispatcher loop started, interval=%ss", settings.DISPATCH_LOOP_INTERVAL_SECONDS)
    while True:
        try:
            await dispatch_pending_batch()
        except Exception:
            logger.exception("Unexpected error in dispatcher loop")
        await asyncio.sleep(settings.DISPATCH_LOOP_INTERVAL_SECONDS)


async def start_dispatcher() -> None:
    global _dispatcher_task
    if not settings.ENABLE_DISPATCHER:
        return
    if _dispatcher_task is None or _dispatcher_task.done():
        _dispatcher_task = asyncio.create_task(_dispatch_loop())
        logger.info("Dispatcher task started")


async def stop_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task and not _dispatcher_task.done():
        _dispatcher_task.cancel()
        try:
            await _dispatcher_task
        except asyncio.CancelledError:
            logger.info("Dispatcher task cancelled")
        _dispatcher_task = None
