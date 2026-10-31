"""Opt-in delivery, retry safety, account isolation and publication atomicity."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from app.config.settings import get_settings
from app.managers import alert_email as delivery
from app.managers import notification as notification_manager
from app.managers.email import AlertEmailTransportError, EmailManager
from app.managers.monitor_worker import process_next
from app.managers.notification_preferences import save_preferences
from app.models.monitor import Alert, Monitor, utc_now
from app.models.notification import Notification
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationPreferences,
)
from app.models.user import User
from app.schemas.notification import AlertPublishedEvent, AlertPublishedPayload
from tests.integration.test_notifications import inbox as inbox, providers

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def email_links(monkeypatch):
    """Use deterministic browser links and an explicitly suppressed SMTP transport."""
    settings = get_settings().model_copy(
        update={
            "frontend_url": "https://headsup.example.test",
            "mail_password": SecretStr("SMTP-SECRET-MUST-NOT-LEAK"),
        }
    )
    monkeypatch.setattr(delivery, "get_settings", lambda: settings)


async def enable(client, inbox, enabled=True):
    response = await client.patch(
        "/users/me/notification-preferences",
        headers=inbox["headers"],
        json={"email_alerts_enabled": enabled},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def publish(test_sessionmaker):
    return await process_next(test_sessionmaker, *providers())


@pytest.mark.parametrize("method", ["GET", "PATCH"])
async def test_preferences_require_authentication(client, method):
    response = await client.request(
        method,
        "/users/me/notification-preferences",
        json={"email_alerts_enabled": True} if method == "PATCH" else None,
    )
    assert response.status_code == 401


async def test_preferences_default_off_and_changes_are_private(inbox, client):
    path = "/users/me/notification-preferences"
    response = await client.get(path, headers=inbox["headers"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "email_alerts_enabled": False,
        "updated_at": None,
    }
    assert (await enable(client, inbox))["email_alerts_enabled"] is True
    assert (await client.get(path, headers=inbox["other_headers"])).json()[
        "email_alerts_enabled"
    ] is False
    assert (await client.get(path, headers=inbox["headers"])).json()[
        "email_alerts_enabled"
    ] is True
    assert (await enable(client, inbox, False))["email_alerts_enabled"] is False


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email_alerts_enabled": "false"},
        {"email_alerts_enabled": None},
        {"email_alerts_enabled": True, "user_id": 10},
        {"email_alerts_enabled": True, "recipient": "someone@example.test"},
    ],
)
async def test_preference_validation_is_strict(inbox, client, body):
    response = await client.patch(
        "/users/me/notification-preferences",
        headers=inbox["headers"],
        json=body,
    )
    assert response.status_code == 422


async def test_default_off_keeps_alert_and_inbox_without_email(
    inbox, test_db, test_sessionmaker
):
    await publish(test_sessionmaker)
    assert await test_db.scalar(select(func.count(Alert.id))) == 1
    assert await test_db.scalar(select(func.count(Notification.id))) == 2
    assert (
        await test_db.scalar(select(func.count(NotificationDelivery.id))) == 0
    )


async def test_publication_enqueues_once_and_opt_in_does_not_replay_history(
    inbox, client, test_db, test_sessionmaker
):
    await publish(test_sessionmaker)
    await enable(client, inbox)
    assert (
        await test_db.scalar(select(func.count(NotificationDelivery.id))) == 0
    )
    alert = await test_db.scalar(select(Alert))
    await notification_manager.publish_notification(
        test_db,
        inbox["monitor"],
        AlertPublishedEvent(payload=AlertPublishedPayload(alert_id=alert.id)),
    )
    assert (
        await test_db.scalar(select(func.count(NotificationDelivery.id))) == 0
    )


async def test_opted_in_delivery_has_monitor_and_sources_and_does_not_mark_read(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    outbox = await test_db.scalar(select(NotificationDelivery))
    assert outbox.status == "pending"
    assert outbox.monitor_snapshot["name"] == "NYC policies"
    sender = AsyncMock()
    mailer = EmailManager(suppress_send=True)
    assert (
        await delivery.process_next_email(
            test_sessionmaker, mailer=mailer, sender=sender
        )
        == outbox.id
    )
    sender.assert_awaited_once()
    message = sender.await_args.args[0]
    html = message.get_body(preferencelist=("html",)).get_content()
    plain = message.get_body(preferencelist=("plain",)).get_content()
    alert = await test_db.scalar(select(Alert))
    assert str(inbox["owner"].email) in message["To"]
    for body in (html, plain):
        assert "NYC policies" in body
        assert "Follow adopted city policies" in body
        assert "A new policy was adopted" in body
        assert "https://example.com/policy" in body
        assert (
            f"https://headsup.example.test/monitors/{alert.monitor_id}?alert={alert.id}"
            in body
        )
        assert "https://headsup.example.test/profile" in body
        assert "UTC" in body
        assert "SMTP-SECRET-MUST-NOT-LEAK" not in body
    await test_db.refresh(outbox)
    await test_db.refresh(alert)
    notice = await test_db.get(Notification, outbox.notification_id)
    assert outbox.status == "sent"
    assert outbox.sent_at is not None
    assert outbox.lease_token is None
    assert outbox.message_id == str(message["Message-ID"])
    assert alert.read_at is None
    assert notice.read_at is None and notice.seen_at is None
    assert (
        await delivery.process_next_email(
            test_sessionmaker, mailer=mailer, sender=sender
        )
        is None
    )
    sender.assert_awaited_once()


async def test_only_alerts_enqueue_email(inbox, client, test_db):
    await enable(client, inbox)
    for version, status in [(1, "paused"), (2, "active")]:
        response = await client.patch(
            f"/monitors/{inbox['monitor'].id}",
            headers=inbox["headers"],
            json={"expected_version": version, "status": status},
        )
        assert response.status_code == 200
    assert await test_db.scalar(select(func.count(Notification.id))) == 3
    assert (
        await test_db.scalar(select(func.count(NotificationDelivery.id))) == 0
    )


async def test_publication_rolls_back_if_outbox_insert_fails(
    inbox, client, test_db, test_sessionmaker, monkeypatch
):
    await enable(client, inbox)
    monkeypatch.setattr(
        notification_manager,
        "enqueue_alert_email",
        AsyncMock(side_effect=RuntimeError("Synthetic outbox failure")),
    )
    await publish(test_sessionmaker)
    assert await test_db.scalar(select(func.count(Alert.id))) == 0
    assert await test_db.scalar(select(func.count(Notification.id))) == 1
    assert (
        await test_db.scalar(select(func.count(NotificationDelivery.id))) == 0
    )


async def test_disabling_cancels_waiting_delivery(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    await enable(client, inbox, False)
    sender = AsyncMock()
    assert (
        await delivery.process_next_email(
            test_sessionmaker,
            mailer=EmailManager(suppress_send=True),
            sender=sender,
        )
        is None
    )
    sender.assert_not_awaited()
    outbox = await test_db.scalar(select(NotificationDelivery))
    assert outbox.status == "skipped" and outbox.reason_code == "email_disabled"
    await enable(client, inbox, True)
    assert (
        await delivery.process_next_email(
            test_sessionmaker,
            mailer=EmailManager(suppress_send=True),
            sender=sender,
        )
        is None
    )


async def test_opt_out_after_claim_prevents_smtp(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    claimed = await delivery.claim_email(test_sessionmaker)
    async with test_sessionmaker() as session:
        context, reason = await delivery.email_context(session, claimed)
    assert reason is None and context
    await enable(client, inbox, False)
    assert not await delivery.begin_sending(
        test_sessionmaker, claimed, context["recipient"], "<example@test>"
    )
    outbox = await test_db.get(NotificationDelivery, claimed.id)
    assert outbox.status == "skipped"


async def test_explicit_non_delivery_retries_without_duplicate_message_id(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    sender = AsyncMock(
        side_effect=AlertEmailTransportError(
            "smtp_connection_failed", retryable=True
        )
    )
    mailer = EmailManager(suppress_send=True)
    await delivery.process_next_email(
        test_sessionmaker, mailer=mailer, sender=sender
    )
    outbox = await test_db.scalar(select(NotificationDelivery))
    assert outbox.status == "pending" and outbox.attempt_count == 1
    first_message_id = sender.await_args.args[0]["Message-ID"]
    assert outbox.next_attempt_at > utc_now()
    outbox.next_attempt_at = utc_now() - timedelta(seconds=1)
    await test_db.commit()
    sender.side_effect = None
    await delivery.process_next_email(
        test_sessionmaker, mailer=mailer, sender=sender
    )
    await test_db.refresh(outbox)
    assert outbox.status == "sent" and outbox.attempt_count == 2
    assert sender.await_args.args[0]["Message-ID"] == first_message_id
    assert (
        await test_db.scalar(select(func.count(NotificationDelivery.id))) == 1
    )
    assert await test_db.scalar(select(func.count(Alert.id))) == 1


@pytest.mark.parametrize(
    "error",
    [
        AlertEmailTransportError("smtp_confirmation_lost", uncertain=True),
        RuntimeError("SMTP secret error body"),
    ],
)
async def test_uncertain_smtp_is_not_replayed(
    inbox, client, test_db, test_sessionmaker, error
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    sender = AsyncMock(side_effect=error)
    await delivery.process_next_email(
        test_sessionmaker,
        mailer=EmailManager(suppress_send=True),
        sender=sender,
    )
    outbox = await test_db.scalar(select(NotificationDelivery))
    assert outbox.status == "uncertain"
    assert outbox.lease_token is None
    assert "secret" not in outbox.error_code
    assert (
        await delivery.process_next_email(
            test_sessionmaker,
            mailer=EmailManager(suppress_send=True),
            sender=sender,
        )
        is None
    )
    sender.assert_awaited_once()


async def test_expired_sending_is_uncertain_and_preparation_can_recover(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    claimed = await delivery.claim_email(test_sessionmaker)
    row = await test_db.get(NotificationDelivery, claimed.id)
    row.lease_expires_at = utc_now() - timedelta(seconds=1)
    await test_db.commit()
    recovered = await delivery.claim_email(test_sessionmaker)
    assert recovered.id == claimed.id and recovered.token != claimed.token
    assert not await delivery.begin_sending(
        test_sessionmaker, claimed, row.recipient_email, "<old@test>"
    )
    row = await test_db.get(
        NotificationDelivery, recovered.id, populate_existing=True
    )
    row.status = "sending"
    row.lease_expires_at = utc_now() - timedelta(seconds=1)
    await test_db.commit()
    assert await delivery.claim_email(test_sessionmaker) is None
    await test_db.refresh(row)
    assert row.status == "uncertain"


async def test_retries_are_bounded(inbox, client, test_db, test_sessionmaker):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    sender = AsyncMock(
        side_effect=AlertEmailTransportError(
            "smtp_connection_failed", retryable=True
        )
    )
    for attempt in range(3):
        await delivery.process_next_email(
            test_sessionmaker,
            mailer=EmailManager(suppress_send=True),
            sender=sender,
        )
        row = await test_db.scalar(select(NotificationDelivery))
        await test_db.refresh(row)
        assert row.attempt_count == attempt + 1
        if attempt < 2:
            assert row.status == "pending"
            row.next_attempt_at = utc_now() - timedelta(seconds=1)
            await test_db.commit()
    assert row.status == "failed"
    assert await delivery.claim_email(test_sessionmaker) is None


@pytest.mark.parametrize("state", ["banned", "unverified", "changed_email"])
async def test_recipient_is_rechecked_before_send(
    inbox, client, test_db, test_sessionmaker, state
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    owner = await test_db.get(User, inbox["owner"].id)
    if state == "banned":
        owner.banned = True
    elif state == "unverified":
        owner.verified = False
    else:
        owner.email = f"{uuid4().hex}@example.test"
    await test_db.commit()
    sender = AsyncMock()
    await delivery.process_next_email(
        test_sessionmaker,
        mailer=EmailManager(suppress_send=True),
        sender=sender,
    )
    sender.assert_not_awaited()
    row = await test_db.scalar(select(NotificationDelivery))
    assert row.status == "skipped"


async def test_profile_email_change_disables_and_cancels_delivery(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    response = await client.put(
        f"/users/{inbox['owner'].id}",
        headers=inbox["headers"],
        json={
            "email": f"{uuid4().hex}@example.com",
            "password": "",
            "first_name": "Inbox",
            "last_name": "Owner",
        },
    )
    assert response.status_code == 200, response.text
    pref = await test_db.get(NotificationPreferences, inbox["owner"].id)
    row = await test_db.scalar(select(NotificationDelivery))
    assert not pref.email_alerts_enabled
    assert row.status == "skipped"


async def test_deleted_monitor_removes_pending_deliveries(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    response = await client.delete(
        f"/monitors/{inbox['monitor'].id}?expected_version=1",
        headers=inbox["headers"],
    )
    assert response.status_code == 204
    assert (
        await test_db.scalar(select(func.count(NotificationDelivery.id))) == 0
    )
    assert await delivery.claim_email(test_sessionmaker) is None


async def test_missing_frontend_configuration_fails_without_smtp(
    inbox, client, test_db, test_sessionmaker, monkeypatch
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    monkeypatch.setattr(
        delivery,
        "get_settings",
        lambda: get_settings().model_copy(update={"frontend_url": None}),
    )
    sender = AsyncMock()
    await delivery.process_next_email(
        test_sessionmaker,
        mailer=EmailManager(suppress_send=True),
        sender=sender,
    )
    row = await test_db.scalar(select(NotificationDelivery))
    assert (
        row.status == "failed"
        and row.error_code == "email_render_or_configuration_failed"
    )
    sender.assert_not_awaited()


async def test_email_delivery_is_independent_of_research_queue_pause(
    inbox, client, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    sender = AsyncMock()
    assert (
        await client.get("/admin/queue", headers=inbox["other_headers"])
    ).json()["automatic_enabled"] is False
    await delivery.process_next_email(
        test_sessionmaker,
        mailer=EmailManager(suppress_send=True),
        sender=sender,
    )
    sender.assert_awaited_once()


async def test_cancel_during_smtp_preserves_unknown_outcome(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    started = asyncio.Event()

    async def sender(message):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        delivery.process_next_email(
            test_sessionmaker,
            mailer=EmailManager(suppress_send=True),
            sender=sender,
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await test_db.scalar(select(NotificationDelivery))
    assert row.status == "uncertain"


async def test_active_send_is_not_claimed_twice_and_opt_out_stops_future_mail(
    inbox, client, test_db, test_sessionmaker
):
    await enable(client, inbox)
    await publish(test_sessionmaker)
    started, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def sender(message):
        sent.append(message)
        started.set()
        await release.wait()

    task = asyncio.create_task(
        delivery.process_next_email(
            test_sessionmaker,
            mailer=EmailManager(suppress_send=True),
            sender=sender,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert await delivery.claim_email(test_sessionmaker) is None
        await enable(client, inbox, False)
        row = await test_db.scalar(select(NotificationDelivery))
        assert row.status == "sending"
        release.set()
        await task
        await test_db.refresh(row)
        assert row.status == "sent"
        assert len(sent) == 1
        assert not (
            await client.get(
                "/users/me/notification-preferences", headers=inbox["headers"]
            )
        ).json()["email_alerts_enabled"]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
