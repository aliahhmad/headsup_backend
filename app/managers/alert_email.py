"""Durable opt-in alert email delivery, independent of model work and inbox reads."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import NameEmail
from sqlalchemy import Select, Text, and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.config.settings import get_settings
from app.logs import logger
from app.managers.email import AlertEmailTransportError, EmailManager
from app.models.monitor import (
    Alert,
    AlertObservation,
    Monitor,
    MonitorRun,
    ObservationSource,
    utc_now,
)
from app.models.notification import Notification
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationPreferences,
)
from app.models.user import User
from app.schemas.email import EmailTemplateSchema

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

POLL_SECONDS = 5
LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
SendEmail = Callable[[EmailMessage], Awaitable[None]]


def compact_text(value: str, limit: int) -> str:
    """Bound display excerpts while retaining the full update in the app."""
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


async def enqueue_alert_email(
    session: AsyncSession, monitor: Monitor, alert: Alert, notification_id: UUID
) -> None:
    """Enqueue only at new publication; opting in later does not replay history."""
    preference = await session.get(
        NotificationPreferences, monitor.user_id, populate_existing=True
    )
    user = await session.get(User, monitor.user_id, populate_existing=True)
    if (
        not preference
        or not preference.email_alerts_enabled
        or not user
        or not user.verified
        or user.banned
    ):
        return
    run = await session.get(MonitorRun, alert.monitor_run_id)
    statement = insert(NotificationDelivery).values(
        notification_id=notification_id,
        channel="email",
        status="pending",
        recipient_email=user.email,
        attempt_count=0,
        monitor_snapshot={
            "name": monitor.name,
            "objective": compact_text(
                run.objective_snapshot if run else monitor.objective, 360
            ),
        },
    )
    await session.execute(
        statement.on_conflict_do_nothing(
            index_elements=[
                NotificationDelivery.notification_id,
                NotificationDelivery.channel,
            ]
        )
    )


@dataclass(frozen=True)
class ClaimedEmail:
    """A bounded attempt identity; never pass a database session through SMTP."""

    id: UUID
    token: UUID
    attempt: int


def finish_row(
    row: NotificationDelivery,
    status: str,
    *,
    reason: str | None = None,
    error: str | None = None,
) -> None:
    """Release ownership and record a terminal delivery outcome."""
    row.status = status
    row.reason_code = reason
    row.error_code = error
    row.completed_at = utc_now()
    row.lease_token = None
    row.lease_expires_at = None
    if status == "sent":
        row.sent_at = row.completed_at


async def claim_email(
    sessions: async_sessionmaker[AsyncSession],
) -> ClaimedEmail | None:
    """Recover preparation only; expired SMTP attempts require human inspection."""
    async with sessions() as session, session.begin():
        now = utc_now()
        await session.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.status == "sending",
                NotificationDelivery.lease_expires_at <= now,
            )
            .values(
                status="uncertain",
                error_code="smtp_confirmation_lost",
                completed_at=now,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        row = await session.scalar(
            select(NotificationDelivery)
            .where(
                or_(
                    and_(
                        NotificationDelivery.status == "pending",
                        NotificationDelivery.next_attempt_at <= now,
                    ),
                    and_(
                        NotificationDelivery.status == "processing",
                        NotificationDelivery.lease_expires_at <= now,
                    ),
                )
            )
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        if row.attempt_count >= MAX_ATTEMPTS:
            finish_row(row, "failed", error="email_attempts_exhausted")
            return None
        row.status = "processing"
        row.attempt_count += 1
        row.lease_token = uuid4()
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        row.started_at = now
        row.completed_at = None
        row.error_code = None
        row.reason_code = None
        return ClaimedEmail(row.id, row.lease_token, row.attempt_count)


def delivery_query(
    delivery_id: UUID,
) -> Select[tuple[NotificationDelivery, Notification, Monitor, Alert, User]]:
    """Require an alert reference within the same owned, nondeleted monitor."""
    return (
        select(NotificationDelivery, Notification, Monitor, Alert, User)
        .join(
            Notification,
            Notification.id == NotificationDelivery.notification_id,
        )
        .join(
            Monitor,
            and_(
                Monitor.id == Notification.scope_monitor_id,
                Monitor.user_id == Notification.user_id,
            ),
        )
        .join(
            Alert,
            and_(
                Alert.monitor_id == Monitor.id,
                Alert.id.cast(Text) == Notification.payload["alert_id"].astext,
            ),
        )
        .join(User, User.id == Notification.user_id)
        .where(
            NotificationDelivery.id == delivery_id,
            Notification.type == "alert.published",
            Monitor.deleted_at.is_(None),
        )
    )


async def email_context(
    session: AsyncSession, claimed: ClaimedEmail
) -> tuple[dict[str, Any] | None, str | None]:
    """Render only publishable content; never include private memories or run prompts."""
    row = (await session.execute(delivery_query(claimed.id))).first()
    if row is None:
        return None, "target_unavailable"
    delivery, notification, monitor, alert, user = row
    if delivery.status != "processing" or delivery.lease_token != claimed.token:
        return None, "claim_lost"
    preference = await session.get(
        NotificationPreferences, user.id, populate_existing=True
    )
    if not preference or not preference.email_alerts_enabled:
        return None, "email_disabled"
    if not user.verified or user.banned:
        return None, "recipient_ineligible"
    if user.email != delivery.recipient_email:
        return None, "recipient_changed"
    raw_sources = (
        await session.scalars(
            select(ObservationSource)
            .join(
                AlertObservation,
                AlertObservation.observation_id
                == ObservationSource.observation_id,
            )
            .where(AlertObservation.alert_id == alert.id)
            .order_by(ObservationSource.created_at, ObservationSource.id)
        )
    ).all()
    sources, seen_urls = [], set()
    for source in raw_sources:
        try:
            parsed = urlsplit(source.source_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.netloc)
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            valid = False
        if valid and source.source_url not in seen_urls:
            sources.append(
                {
                    "url": source.source_url,
                    "title": source.source_title or parsed.hostname or "Source",
                }
            )
            seen_urls.add(source.source_url)
    snapshot = delivery.monitor_snapshot
    return {
        "delivery_id": str(delivery.id),
        "recipient": user.email,
        "name": " ".join(user.first_name.split()),
        "monitor_id": str(monitor.id),
        "alert_id": str(alert.id),
        "monitor_name": snapshot["name"],
        "monitor_objective": snapshot["objective"],
        "alert_title": alert.title,
        "alert_body": alert.body,
        "alert_paragraphs": [
            part
            for part in alert.body.replace("\r\n", "\n").split("\n\n")
            if part.strip()
        ],
        "published_at": alert.created_at.strftime("%B %d, %Y at %H:%M UTC"),
        "preview": compact_text(alert.body, 120),
        "sources": sources,
    }, None


def render_alert_email(
    context: dict[str, Any], mailer: EmailManager
) -> EmailMessage:
    """Build an escaped, accessible multipart message with an exact-alert deep link."""
    frontend = (get_settings().frontend_url or "").rstrip("/")
    parsed = urlsplit(frontend)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("email_frontend_not_configured")
    body = {
        **context,
        "action_url": f"{frontend}/monitors/{context['monitor_id']}?alert={context['alert_id']}",
        "settings_url": f"{frontend}/profile",
        "frontend_url": frontend,
    }
    subject = compact_text(
        f"HeadsUp · {context['monitor_name']}: {context['alert_title']}", 180
    )
    message = mailer.render_template_message(
        EmailTemplateSchema(
            recipients=[
                NameEmail(
                    context["name"] or "HeadsUp member", context["recipient"]
                )
            ],
            subject=subject,
            template_name="alert_update.html",
            body=body,
        )
    )
    domain = str(mailer.conf.MAIL_FROM).rsplit("@", 1)[-1]
    message.replace_header(
        "Message-ID", f"<headsup-alert-{context['delivery_id']}@{domain}>"
    )
    return message


async def begin_sending(
    sessions: async_sessionmaker[AsyncSession],
    claimed: ClaimedEmail,
    recipient: str,
    message_id: str,
) -> bool:
    """Serialize the last opt-out check with preference changes, before SMTP."""
    async with sessions() as session, session.begin():
        data = (await session.execute(delivery_query(claimed.id))).first()
        if data is None:
            return False
        _, _, _, _, user = data
        preference = await session.scalar(
            select(NotificationPreferences)
            .where(NotificationPreferences.user_id == user.id)
            .with_for_update()
        )
        delivery = await session.get(
            NotificationDelivery,
            claimed.id,
            with_for_update=True,
            populate_existing=True,
        )
        if (
            delivery is None
            or delivery.status != "processing"
            or delivery.lease_token != claimed.token
        ):
            return False
        # Fresh account read after the preference lock avoids using a cached profile.
        await session.refresh(user)
        reason = (
            "email_disabled"
            if not preference or not preference.email_alerts_enabled
            else "recipient_ineligible"
            if not user.verified or user.banned
            else "recipient_changed"
            if user.email != recipient or delivery.recipient_email != recipient
            else None
        )
        if reason:
            finish_row(delivery, "skipped", reason=reason)
            return False
        delivery.status = "sending"
        delivery.lease_expires_at = utc_now() + timedelta(seconds=LEASE_SECONDS)
        delivery.message_id = message_id
        return True


async def complete_email(
    sessions: async_sessionmaker[AsyncSession],
    claimed: ClaimedEmail,
    *,
    status: str,
    reason: str | None = None,
    error: str | None = None,
) -> None:
    """Fence old workers; bounded retry only when non-delivery is known."""
    async with sessions() as session, session.begin():
        row = await session.get(
            NotificationDelivery, claimed.id, with_for_update=True
        )
        if (
            row is None
            or row.lease_token != claimed.token
            or row.status not in {"processing", "sending"}
        ):
            return
        if status == "pending" and row.attempt_count >= MAX_ATTEMPTS:
            status = "failed"
        finish_row(row, status, reason=reason, error=error)
        if status == "pending":
            row.completed_at = None
            row.next_attempt_at = utc_now() + timedelta(
                seconds=60 * 5 ** (row.attempt_count - 1)
            )


async def process_next_email(
    sessions: async_sessionmaker[AsyncSession],
    *,
    mailer: EmailManager | None = None,
    sender: SendEmail | None = None,
) -> UUID | None:
    """Deliver one committed email without re-running research or marking alerts read."""
    claimed = await claim_email(sessions)
    if claimed is None:
        return None
    smtp_started = False
    try:
        async with sessions() as session:
            context, reason = await email_context(session, claimed)
        if context is None:
            await complete_email(
                sessions, claimed, status="skipped", reason=reason
            )
            return claimed.id
        try:
            mailer = mailer or EmailManager()
            message = render_alert_email(context, mailer)
        except Exception:  # noqa: BLE001
            await complete_email(
                sessions,
                claimed,
                status="failed",
                error="email_render_or_configuration_failed",
            )
            return claimed.id
        if not await begin_sending(
            sessions, claimed, context["recipient"], str(message["Message-ID"])
        ):
            return claimed.id
        smtp_started = True
        await asyncio.wait_for(
            (sender or mailer.send_alert_message)(message), timeout=75
        )
        await complete_email(
            sessions, claimed, status="sent", reason="smtp_accepted"
        )
    except asyncio.CancelledError:
        await asyncio.shield(
            complete_email(
                sessions,
                claimed,
                status="uncertain" if smtp_started else "pending",
                error="email_worker_interrupted",
            )
        )
        raise
    except AlertEmailTransportError as exc:
        await complete_email(
            sessions,
            claimed,
            status="uncertain"
            if exc.uncertain
            else "pending"
            if exc.retryable
            else "failed",
            error=exc.code,
        )
    except Exception as exc:  # noqa: BLE001
        await complete_email(
            sessions,
            claimed,
            status="uncertain" if smtp_started else "pending",
            error="email_confirmation_lost"
            if smtp_started
            else "email_execution_failed",
        )
        logger.error("Alert email delivery failed ({})", type(exc).__name__)
    return claimed.id


async def serve_alert_emails(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Email work is independent of the automatic research queue switch."""
    while True:
        try:
            await process_next_email(sessions)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Alert email service unavailable ({})", type(exc).__name__
            )
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def alert_email_service(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Own the delivery loop and cancel it safely when the API stops."""
    task = asyncio.create_task(serve_alert_emails(sessions))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
