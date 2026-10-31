"""Administrator-only run directory and historical execution inspection."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi_pagination import Page, Params
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import get_database
from app.managers import run_dashboard as manager
from app.managers.auth import is_admin
from app.managers.security import get_current_user
from app.schemas.request.run_dashboard import RunFilters
from app.schemas.response.run_dashboard import AdminRunDetail, AdminRunSummary


def private_response(response: Response) -> None:
    """Execution inputs and cross-account data must never be cached publicly."""
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    tags=["Run administration"],
    prefix="/admin/monitor-runs",
    dependencies=[
        Depends(get_current_user),
        Depends(is_admin),
        Depends(private_response),
    ],
)
Database = Annotated[AsyncSession, Depends(get_database)]
Filters = Annotated[RunFilters, Query()]


@router.get("")
async def list_runs(
    db: Database, filters: Filters, params: Annotated[Params, Depends()]
) -> Page[AdminRunSummary]:
    """Search executions across accounts with status, dates and ownership filters."""
    return await manager.list_runs(filters, params, db)


@router.get("/summary")
async def summary(db: Database, filters: Filters) -> dict[str, Any]:
    """Read summary cards under the same scope as the run directory."""
    return await manager.run_counts(filters, db)


@router.get("/{run_id}")
async def run_detail(run_id: UUID, db: Database) -> AdminRunDetail:
    """Read snapshots, traces, knowledge revisions, evidence and alert outputs."""
    return await manager.run_detail(run_id, db)
