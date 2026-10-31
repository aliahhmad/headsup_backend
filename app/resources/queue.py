"""Administrator-only shared queue controls; requests do not await paid work."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_database
from app.managers import queue_control as manager
from app.managers.auth import is_admin
from app.managers.security import get_current_user
from app.models.user import User
from app.resources.run_dashboard import private_response
from app.schemas.request.queue import QueueSettingsUpdate
from app.schemas.response.queue import (
    QueueExecutionResponse,
    QueueStateResponse,
)

router = APIRouter(
    tags=["Queue administration"],
    prefix="/admin/queue",
    dependencies=[
        Depends(get_current_user),
        Depends(is_admin),
        Depends(private_response),
    ],
)
Database = Annotated[AsyncSession, Depends(get_database)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("")
async def state(db: Database) -> QueueStateResponse:
    """Read shared policy, worker heartbeat, backlog and recent attempts."""
    return await manager.queue_state(db)


@router.patch("/settings")
async def settings(
    data: QueueSettingsUpdate, db: Database, user: CurrentUser
) -> QueueStateResponse:
    """Enable automatic consumption, or finish the current item and pause."""
    await manager.set_automatic(
        db, enabled=data.automatic_enabled, user_id=user.id
    )
    await db.flush()
    return await manager.queue_state(db)


@router.post("/run-next", status_code=202)
async def run_next(db: Database, user: CurrentUser) -> QueueExecutionResponse:
    """Accept exactly one single-step request while automatic processing is off."""
    execution = await manager.request_single_step(db, user_id=user.id)
    return QueueExecutionResponse.model_validate(execution)
