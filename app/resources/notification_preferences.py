"""Private profile controls for alert email delivery."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_database
from app.managers import notification_preferences as manager
from app.managers.security import get_current_user
from app.models.user import User
from app.resources.monitor import private_response
from app.schemas.request.notification_preferences import (
    NotificationPreferencesUpdate,
)
from app.schemas.response.notification_preferences import (
    NotificationPreferencesResponse,
)

router = APIRouter(
    tags=["Notification preferences"],
    prefix="/users/me/notification-preferences",
    dependencies=[Depends(private_response)],
)
Database = Annotated[AsyncSession, Depends(get_database)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("")
async def get_preferences(
    db: Database, user: CurrentUser
) -> NotificationPreferencesResponse:
    """Read only the authenticated account's saved opt-in."""
    return await manager.read_preferences(db, user.id)


@router.patch("")
async def set_preferences(
    data: NotificationPreferencesUpdate, db: Database, user: CurrentUser
) -> NotificationPreferencesResponse:
    """Toggle future email updates without changing in-app notification history."""
    return await manager.save_preferences(
        db, user.id, enabled=data.email_alerts_enabled
    )
