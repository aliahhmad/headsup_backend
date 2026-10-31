"""Publish typed inbox events atomically and expose only owned live references."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException
from fastapi_pagination import Page, Params
from sqlalchemy import Select, Text, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.managers.alert_email import enqueue_alert_email
from app.models.monitor import Alert, Monitor, utc_now
from app.models.notification import Notification
from app.schemas.notification import (
    AlertPublishedEvent,
    MonitorActivatedEvent,
    NotificationEvent,
    notification_event,
)
from app.schemas.response.notification import (
    NotificationCounts,
    NotificationResponse,
    notification_response,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.request.notification import (
        NotificationFilters,
        NotificationStateUpdate,
    )


def visible_notifications(
    user_id: int,
) -> Select[tuple[Notification, Monitor, Alert]]:
    """Use the same scope for history, counts and state changes; hide dead links."""
    return (
        select(Notification, Monitor, Alert)
        .join(Monitor, Monitor.id == Notification.scope_monitor_id)
        .outerjoin(
            Alert,
            and_(
                Notification.type == "alert.published",
                Alert.monitor_id == Notification.scope_monitor_id,
                Alert.id.cast(Text) == Notification.payload["alert_id"].astext,
            ),
        )
        .where(
            Notification.user_id == user_id,
            Monitor.user_id == user_id,
            Monitor.deleted_at.is_(None),
            or_(Notification.type != "alert.published", Alert.id.is_not(None)),
        )
    )


async def publish_notification(
    session: AsyncSession, monitor: Monitor, event: NotificationEvent
) -> None:
    """Keep exact type validation and owner/reference checks before durable insert."""
    event = notification_event.validate_python(event.model_dump())
    if monitor.deleted_at is not None:
        raise ValueError("Cannot notify for a deleted monitor")
    if isinstance(event, AlertPublishedEvent):
        alert = await session.scalar(
            select(Alert).where(
                Alert.id == event.payload.alert_id,
                Alert.monitor_id == monitor.id,
            )
        )
        if alert is None:
            raise ValueError(
                "Notification alert does not belong to its monitor"
            )
        key = f"{event.type}:{alert.id}"
    else:
        expected_status = (
            "active" if isinstance(event, MonitorActivatedEvent) else "paused"
        )
        if (
            event.payload.monitor_id != monitor.id
            or event.payload.monitor_version != monitor.version
            or monitor.status != expected_status
        ):
            raise ValueError(
                "Notification does not match the monitor transition"
            )
        key = f"{event.type}:{monitor.id}:{monitor.version}"
    statement = insert(Notification).values(
        user_id=monitor.user_id,
        scope_monitor_id=monitor.id,
        type=event.type,
        payload=event.payload.model_dump(mode="json"),
        deduplication_key=key,
    )
    result = await session.execute(
        statement.on_conflict_do_nothing(
            index_elements=[
                Notification.user_id,
                Notification.deduplication_key,
            ]
        ).returning(Notification.id)
    )
    notification_id = result.scalar_one_or_none()
    if (
        notification_id is not None
        and isinstance(event, AlertPublishedEvent)
        and alert is not None
    ):
        await enqueue_alert_email(session, monitor, alert, notification_id)


def display_notification(
    notification: Notification, monitor: Monitor, alert: Alert | None
) -> NotificationResponse:
    """Construct safe internal links and plain text from validated owned data."""
    event = notification_event.validate_python(
        {"type": notification.type, "payload": notification.payload}
    )
    target = f"/monitors/{monitor.id}"
    if isinstance(event, AlertPublishedEvent):
        if alert is None:
            raise ValueError("Missing notification alert")
        title = alert.title
        message = alert.body[:240] + ("…" if len(alert.body) > 240 else "")
        target += f"?alert={alert.id}"
    elif isinstance(event, MonitorActivatedEvent):
        title = "Monitor activated"
        message = f"“{monitor.name}” is active."
    else:
        title = "Monitor paused"
        message = f"“{monitor.name}” is paused. Automatic checks are paused; you can still request a manual check."
    return notification_response.validate_python(
        {
            "id": notification.id,
            "created_at": notification.created_at,
            "read_at": notification.read_at,
            "seen_at": notification.seen_at,
            **event.model_dump(),
            "title": title,
            "message": message,
            "target": target,
        }
    )


async def list_notifications(
    session: AsyncSession,
    user_id: int,
    filters: NotificationFilters,
    params: Params,
) -> Page[NotificationResponse]:
    """Use stable newest-first pagination without altering read/seen state."""
    query = visible_notifications(user_id)
    if filters.type is not None:
        query = query.where(Notification.type == filters.type)
    if filters.unread:
        query = query.where(Notification.read_at.is_(None))
    total = await session.scalar(
        select(func.count()).select_from(query.subquery())
    )
    raw = params.to_raw_params()
    rows = (
        await session.execute(
            query.order_by(
                Notification.created_at.desc(), Notification.id.desc()
            )
            .limit(raw.limit)
            .offset(raw.offset)
        )
    ).all()
    return Page.create(
        [display_notification(*row) for row in rows],
        total=total or 0,
        params=params,
    )


async def counts(session: AsyncSession, user_id: int) -> NotificationCounts:
    """Unread counts track acknowledgement, unseen counts track actual exposure."""
    row = (
        await session.execute(
            visible_notifications(user_id).with_only_columns(
                func.count(),
                func.count().filter(Notification.read_at.is_(None)),
                func.count().filter(Notification.seen_at.is_(None)),
            )
        )
    ).one()
    return NotificationCounts(total=row[0], unread=row[1], unseen=row[2])


async def sync_alert_read(
    session: AsyncSession, *, user_id: int, alert: Alert
) -> None:
    """The caller holds monitor then alert locks before notification updates."""
    values: dict[str, Any] = {"read_at": alert.read_at}
    if alert.read_at is not None:
        values["seen_at"] = func.coalesce(Notification.seen_at, alert.read_at)
    await session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.scope_monitor_id == alert.monitor_id,
            Notification.type == "alert.published",
            Notification.payload["alert_id"].astext == str(alert.id),
        )
        .values(**values)
    )


async def update_state(
    session: AsyncSession,
    user_id: int,
    notification_id: UUID,
    data: NotificationStateUpdate,
) -> NotificationResponse:
    """Use monitor → alert → notification lock order for two-way read sync."""
    row = (
        await session.execute(
            visible_notifications(user_id).where(
                Notification.id == notification_id
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Notification not found")
    notification, monitor, alert = row
    monitor = await session.scalar(
        select(Monitor)
        .where(
            Monitor.id == monitor.id,
            Monitor.user_id == user_id,
            Monitor.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if monitor is None:
        raise HTTPException(404, "Notification not found")
    if alert is not None:
        alert = await session.scalar(
            select(Alert)
            .where(Alert.id == alert.id, Alert.monitor_id == monitor.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if alert is None:
            raise HTTPException(404, "Notification not found")
    notification = await session.scalar(
        select(Notification)
        .where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if notification is None:
        raise HTTPException(404, "Notification not found")
    now = utc_now()
    if data.read is not None:
        if alert is not None:
            alert.read_at = (alert.read_at or now) if data.read else None
            notification.read_at = alert.read_at
        else:
            notification.read_at = (
                (notification.read_at or now) if data.read else None
            )
        if data.read:
            notification.seen_at = notification.seen_at or now
    if data.seen:
        notification.seen_at = notification.seen_at or now
    await session.flush()
    return display_notification(notification, monitor, alert)


async def mark_seen(
    session: AsyncSession, user_id: int, ids: list[UUID]
) -> int:
    """Batch only rendered owned items; ignore other IDs without revealing them."""
    visible_ids = (
        visible_notifications(user_id)
        .with_only_columns(Notification.id)
        .where(Notification.id.in_(ids))
    )
    result = await session.execute(
        update(Notification)
        .where(Notification.id.in_(visible_ids), Notification.seen_at.is_(None))
        .values(seen_at=utc_now())
        .returning(Notification.id)
    )
    return len(result.all())


async def remove_monitor_notifications(
    session: AsyncSession, monitor_id: UUID
) -> None:
    """Soft deletion removes inbox copies and their user-visible links atomically."""
    await session.execute(
        delete(Notification).where(Notification.scope_monitor_id == monitor_id)
    )
