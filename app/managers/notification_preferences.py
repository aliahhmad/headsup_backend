"""Save opt-in immediately and cancel email work which has not begun sending."""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitor import utc_now
from app.models.notification import Notification
from app.models.notification_delivery import (
    NotificationDelivery,
    NotificationPreferences,
)
from app.schemas.response.notification_preferences import (
    NotificationPreferencesResponse,
)


async def read_preferences(
    session: AsyncSession, user_id: int
) -> NotificationPreferencesResponse:
    """Read fresh database state rather than the cached account profile."""
    row = await session.get(
        NotificationPreferences, user_id, populate_existing=True
    )
    return NotificationPreferencesResponse(
        email_alerts_enabled=bool(row and row.email_alerts_enabled),
        updated_at=row.updated_at if row else None,
    )


async def save_preferences(
    session: AsyncSession, user_id: int, *, enabled: bool
) -> NotificationPreferencesResponse:
    """Serialize with the final send gate on the preference row."""
    statement = insert(NotificationPreferences).values(
        user_id=user_id, email_alerts_enabled=enabled, updated_at=utc_now()
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[NotificationPreferences.user_id],
            set_={"email_alerts_enabled": enabled, "updated_at": utc_now()},
        )
    )
    if not enabled:
        own_notifications = select(Notification.id).where(
            Notification.user_id == user_id
        )
        await session.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.notification_id.in_(own_notifications),
                NotificationDelivery.status.in_(["pending", "processing"]),
            )
            .values(
                status="skipped",
                reason_code="email_disabled",
                error_code=None,
                completed_at=utc_now(),
                lease_token=None,
                lease_expires_at=None,
            )
        )
    return await read_preferences(session, user_id)
