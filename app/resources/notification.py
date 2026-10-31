"""Account-scoped in-app inbox; clients cannot create arbitrary events."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi_pagination import Page, Params
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_database
from app.managers import notification as manager
from app.managers.security import get_current_user
from app.models.user import User
from app.resources.monitor import private_response
from app.schemas.request.notification import (
    NotificationFilters,
    NotificationStateUpdate,
    NotificationsSeen,
)
from app.schemas.response.notification import (
    NotificationCounts,
    NotificationResponse,
)

router = APIRouter(
    tags=["Notifications"],
    prefix="/notifications",
    dependencies=[Depends(private_response)],
)
CurrentUser = Annotated[User, Depends(get_current_user)]
Database = Annotated[AsyncSession, Depends(get_database)]


@router.get("")
async def list_notifications(
    user: CurrentUser,
    db: Database,
    filters: Annotated[NotificationFilters, Query()],
    params: Annotated[Params, Depends()],
) -> Page[NotificationResponse]:
    """Read only this account's events, including its read history."""
    return await manager.list_notifications(db, user.id, filters, params)


@router.get("/count")
async def notification_count(
    user: CurrentUser, db: Database
) -> NotificationCounts:
    """Read the global inbox badge counts without acknowledging anything."""
    return await manager.counts(db, user.id)


@router.post("/seen")
async def seen(
    data: NotificationsSeen, user: CurrentUser, db: Database
) -> dict[str, int]:
    """Acknowledge only items actually displayed, without marking them read."""
    return {"updated": await manager.mark_seen(db, user.id, data.ids)}


@router.patch("/{notification_id}")
async def update_notification(
    notification_id: UUID,
    data: NotificationStateUpdate,
    user: CurrentUser,
    db: Database,
) -> NotificationResponse:
    """Set acknowledgement state and synchronize an associated alert's read state."""
    return await manager.update_state(db, user.id, notification_id, data)
