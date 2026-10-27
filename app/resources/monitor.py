"""Authenticated monitor management and check-request endpoints."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi_pagination import Page, Params
from fastapi_pagination.ext.sqlalchemy import apaginate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_database
from app.managers import monitor as manager
from app.managers.security import get_current_user
from app.models.monitor import MonitorRun, Observation, ObservationSource
from app.models.user import User
from app.schemas.request.monitor import (
    MonitorCheckRequest,
    MonitorCreate,
    MonitorStatus,
    MonitorUpdate,
)
from app.schemas.response.monitor import (
    MonitorDetailResponse,
    MonitorResponse,
    MonitorRunDetailResponse,
    MonitorRunResponse,
    ObservationResponse,
    ObservationSourceResponse,
)


def private_response(response: Response) -> None:
    """Keep personal monitor configuration and context out of HTTP caches."""
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    tags=["Monitors"],
    prefix="/monitors",
    dependencies=[Depends(private_response)],
)
CurrentUser = Annotated[User, Depends(get_current_user)]
Database = Annotated[AsyncSession, Depends(get_database)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_monitor(
    data: MonitorCreate, user: CurrentUser, db: Database
) -> MonitorDetailResponse:
    """Create an active monitor and its initial records atomically."""
    monitor = await manager.create_monitor(data, user.id, db)
    return await manager.monitor_detail(monitor, db)


@router.get("")
async def list_monitors(
    user: CurrentUser,
    db: Database,
    params: Annotated[Params, Depends()],
    state: Annotated[MonitorStatus | None, Query(alias="status")] = None,
) -> Page[MonitorResponse]:
    """List only the authenticated user's monitors, optionally by status."""
    return cast(
        "Page[MonitorResponse]",
        await apaginate(db, manager.monitors_query(user.id, state), params),
    )


@router.get("/{monitor_id}")
async def get_monitor(
    monitor_id: UUID, user: CurrentUser, db: Database
) -> MonitorDetailResponse:
    """Read configuration, chat, current memories, and the latest run."""
    monitor = await manager.owned_monitor(monitor_id, user.id, db)
    return await manager.monitor_detail(monitor, db)


@router.patch("/{monitor_id}")
async def update_monitor(
    monitor_id: UUID,
    data: MonitorUpdate,
    user: CurrentUser,
    db: Database,
) -> MonitorDetailResponse:
    """Edit, pause, archive, or reactivate a monitor with version protection."""
    monitor = await manager.owned_monitor(monitor_id, user.id, db, lock=True)
    await manager.update_monitor(monitor, data, db)
    return await manager.monitor_detail(monitor, db)


@router.delete("/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitor(
    monitor_id: UUID,
    user: CurrentUser,
    db: Database,
    expected_version: Annotated[int, Query(ge=1)],
) -> Response:
    """Soft-delete a monitor and cancel its queued checks."""
    monitor = await manager.owned_monitor(monitor_id, user.id, db, lock=True)
    await manager.delete_monitor(monitor, expected_version, db)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{monitor_id}/checks", status_code=status.HTTP_202_ACCEPTED)
async def check_now(
    monitor_id: UUID,
    data: MonitorCheckRequest,
    user: CurrentUser,
    db: Database,
) -> MonitorRunResponse:
    """Queue a check or return an existing request without running research."""
    monitor = await manager.owned_monitor(monitor_id, user.id, db, lock=True)
    run = await manager.queue_manual_check(monitor, data.client_request_id, db)
    return MonitorRunResponse.model_validate(run)


@router.get("/{monitor_id}/runs")
async def list_runs(
    monitor_id: UUID,
    user: CurrentUser,
    db: Database,
    params: Annotated[Params, Depends()],
) -> Page[MonitorRunResponse]:
    """List the authorized monitor's check history, newest first."""
    await manager.owned_monitor(monitor_id, user.id, db)
    query = (
        select(MonitorRun)
        .where(MonitorRun.monitor_id == monitor_id)
        .order_by(MonitorRun.created_at.desc(), MonitorRun.id.desc())
    )
    return cast("Page[MonitorRunResponse]", await apaginate(db, query, params))


@router.get("/{monitor_id}/runs/{run_id}")
async def get_run(
    monitor_id: UUID,
    run_id: UUID,
    user: CurrentUser,
    db: Database,
) -> MonitorRunDetailResponse:
    """Read a run only through its owned, accessible parent monitor."""
    await manager.owned_monitor(monitor_id, user.id, db)
    run = await db.scalar(
        select(MonitorRun).where(
            MonitorRun.id == run_id, MonitorRun.monitor_id == monitor_id
        )
    )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitor run not found")
    observations = list(
        await db.scalars(
            select(Observation)
            .where(Observation.monitor_run_id == run.id)
            .order_by(Observation.created_at, Observation.id)
        )
    )
    sources = await db.scalars(
        select(ObservationSource)
        .join(Observation, Observation.id == ObservationSource.observation_id)
        .where(Observation.monitor_run_id == run.id)
        .order_by(ObservationSource.created_at, ObservationSource.id)
    )
    by_observation: dict[UUID, list[ObservationSourceResponse]] = {}
    for source in sources:
        by_observation.setdefault(source.observation_id, []).append(
            ObservationSourceResponse.model_validate(source)
        )
    return MonitorRunDetailResponse(
        **MonitorRunResponse.model_validate(run).model_dump(),
        observations=[
            ObservationResponse(
                id=observation.id,
                monitor_run_id=observation.monitor_run_id,
                item_key=observation.item_key,
                summary=observation.summary,
                created_at=observation.created_at,
                sources=by_observation.get(observation.id, []),
            )
            for observation in observations
        ],
    )
