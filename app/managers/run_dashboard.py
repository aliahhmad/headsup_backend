"""Read-only cross-account run inspection, used only by admin routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import HTTPException
from fastapi_pagination import Page
from sqlalchemy import func, or_, select

from app.models.monitor import (
    Alert,
    AlertCandidate,
    AlertJob,
    AlertObservation,
    Chat,
    Memory,
    MemoryRevision,
    Message,
    Monitor,
    MonitorRun,
    Observation,
    ObservationSource,
    RunModelCall,
    utc_now,
)
from app.models.user import User
from app.schemas.response.monitor import (
    AlertDetailResponse,
    AlertResponse,
    MonitorResponse,
    MonitorRunResponse,
    ObservationResponse,
    ObservationSourceResponse,
)
from app.schemas.response.run_dashboard import (
    AdminRunDetail,
    AdminRunSummary,
    ModelCallResponse,
    RunAccount,
    RunCandidateResponse,
    RunJobResponse,
)

if TYPE_CHECKING:
    from fastapi_pagination import Params
    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.request.run_dashboard import RunFilters


def runs_query(filters: RunFilters) -> Select[Any]:
    """Return all requested runs, including historical soft-deleted monitors."""
    query = (
        select(MonitorRun, Monitor, User, AlertJob)
        .join(Monitor, Monitor.id == MonitorRun.monitor_id)
        .join(User, User.id == Monitor.user_id)
        .outerjoin(AlertJob, AlertJob.monitor_run_id == MonitorRun.id)
    )
    if filters.status:
        query = query.where(MonitorRun.status == filters.status)
    if filters.trigger:
        query = query.where(MonitorRun.trigger == filters.trigger)
    if filters.alert_status == "not_evaluated":
        query = query.where(AlertJob.id.is_(None))
    elif filters.alert_status:
        query = query.where(AlertJob.status == filters.alert_status)
    if filters.user_id:
        query = query.where(User.id == filters.user_id)
    if filters.monitor_id:
        query = query.where(Monitor.id == filters.monitor_id)
    if filters.created_from:
        query = query.where(MonitorRun.created_at >= filters.created_from)
    if filters.created_to:
        query = query.where(MonitorRun.created_at <= filters.created_to)
    term = filters.q.strip()
    if term:
        escaped = (
            term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        clauses: list[Any] = [
            column.ilike(pattern, escape="\\")
            for column in (
                User.email,
                User.first_name,
                User.last_name,
                Monitor.name,
                MonitorRun.objective_snapshot,
            )
        ]
        try:
            identity = UUID(term)
            clauses.extend([MonitorRun.id == identity, Monitor.id == identity])
        except ValueError:
            pass
        if term.isascii() and term.isdigit() and len(term) <= 10:
            account_id = int(term)
            if account_id <= 2147483647:
                clauses.append(User.id == account_id)
        query = query.where(or_(*clauses))
    return query


async def run_counts(
    filters: RunFilters, session: AsyncSession
) -> dict[str, Any]:
    """Compute scope-consistent cards without downloading snapshots or outputs."""
    scope = (
        runs_query(filters)
        .with_only_columns(
            MonitorRun.id,
            MonitorRun.status,
            MonitorRun.started_at,
            MonitorRun.completed_at,
            AlertJob.status.label("alert_status"),
        )
        .subquery()
    )
    counts: dict[str, Any] = {"total": func.count()}
    for state in ("queued", "running", "succeeded", "failed", "cancelled"):
        counts[state] = func.count().filter(scope.c.status == state)
    for state in ("pending", "processing", "published", "skipped", "failed"):
        counts[f"alerts_{state}"] = func.count().filter(
            scope.c.alert_status == state
        )
    duration = func.extract("epoch", scope.c.completed_at - scope.c.started_at)
    counts["average_duration_seconds"] = func.avg(duration).filter(
        scope.c.started_at.is_not(None), scope.c.completed_at.is_not(None)
    )
    row = (
        await session.execute(
            select(
                *[value.label(key) for key, value in counts.items()]
            ).select_from(scope)
        )
    ).one()
    values = dict(row._mapping)
    average = values["average_duration_seconds"]
    values["average_duration_seconds"] = (
        float(average) if average is not None else None
    )
    return {**values, "as_of": utc_now()}


async def directory_counts(
    ids: list[UUID], session: AsyncSession
) -> dict[UUID, dict[str, Any]]:
    """Batch counts by the visible page to avoid per-row database queries."""
    output: dict[UUID, dict[str, Any]] = {identity: {} for identity in ids}
    for model, name in (
        (Observation, "observation_count"),
        (Alert, "alert_count"),
        (RunModelCall, "trace_count"),
    ):
        rows = await session.execute(
            select(model.monitor_run_id, func.count(model.id))
            .where(model.monitor_run_id.in_(ids))
            .group_by(model.monitor_run_id)
        )
        for run_id, count in rows:
            output[run_id][name] = count
    sources = await session.execute(
        select(Observation.monitor_run_id, func.count(ObservationSource.id))
        .join(
            ObservationSource,
            ObservationSource.observation_id == Observation.id,
        )
        .where(Observation.monitor_run_id.in_(ids))
        .group_by(Observation.monitor_run_id)
    )
    for run_id, count in sources:
        output[run_id]["source_count"] = count
    calls = await session.execute(
        select(
            RunModelCall.monitor_run_id, RunModelCall.stage, RunModelCall.status
        )
        .where(RunModelCall.monitor_run_id.in_(ids))
        .distinct(RunModelCall.monitor_run_id)
        .order_by(
            RunModelCall.monitor_run_id,
            RunModelCall.created_at.desc(),
            RunModelCall.id.desc(),
        )
    )
    for run_id, stage, status in calls:
        output[run_id]["latest_stage"] = stage
        output[run_id]["latest_request_status"] = status
    return output


async def list_runs(
    filters: RunFilters, params: Params, session: AsyncSession
) -> Page[AdminRunSummary]:
    """Paginate first, then enrich only the visible executions."""
    query = runs_query(filters)
    total = await session.scalar(
        select(func.count()).select_from(query.subquery())
    )
    ordering = (
        (MonitorRun.created_at.desc(), MonitorRun.id.desc())
        if filters.order == "newest"
        else (MonitorRun.created_at, MonitorRun.id)
    )
    rows = (
        await session.execute(
            query.order_by(*ordering)
            .offset((params.page - 1) * params.size)
            .limit(params.size)
        )
    ).all()
    metrics = (
        await directory_counts([row[0].id for row in rows], session)
        if rows
        else {}
    )
    now = utc_now()
    items = []
    for run, monitor, owner, job in rows:
        counts = metrics.get(run.id, {})
        items.append(
            AdminRunSummary(
                id=run.id,
                monitor_id=monitor.id,
                monitor_name=monitor.name,
                monitor_status=monitor.status,
                monitor_deleted_at=monitor.deleted_at,
                owner=RunAccount.model_validate(owner),
                status=run.status,
                trigger=run.trigger,
                attempt_count=run.attempt_count,
                created_at=run.created_at,
                started_at=run.started_at,
                completed_at=run.completed_at,
                error_code=run.error_code,
                alert_status=job.status if job else None,
                alert_error_code=job.error_code if job else None,
                elapsed_seconds=max(
                    0,
                    (
                        (run.completed_at or now) - run.started_at
                    ).total_seconds(),
                )
                if run.started_at
                else None,
                queue_seconds=max(
                    0,
                    (
                        (run.started_at or run.completed_at or now)
                        - run.created_at
                    ).total_seconds(),
                ),
                **{
                    name: counts.get(name, 0)
                    for name in (
                        "observation_count",
                        "source_count",
                        "alert_count",
                        "trace_count",
                    )
                },
                latest_stage=counts.get("latest_stage"),
                latest_request_status=counts.get("latest_request_status"),
            )
        )
    return Page[AdminRunSummary].create(items, params, total=total or 0)


async def run_detail(run_id: UUID, session: AsyncSession) -> AdminRunDetail:
    """Read inputs as captured, outputs as saved, and current state separately."""
    row = (
        await session.execute(
            select(MonitorRun, Monitor, User)
            .join(Monitor, Monitor.id == MonitorRun.monitor_id)
            .join(User, User.id == Monitor.user_id)
            .where(MonitorRun.id == run_id)
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Monitor run not found")
    run, monitor, owner = row
    findings = list(
        await session.scalars(
            select(Observation)
            .where(Observation.monitor_run_id == run.id)
            .order_by(Observation.created_at, Observation.id)
        )
    )
    sources = await session.scalars(
        select(ObservationSource)
        .join(Observation)
        .where(Observation.monitor_run_id == run.id)
        .order_by(ObservationSource.created_at, ObservationSource.id)
    )
    grouped: dict[UUID, list[ObservationSourceResponse]] = {}
    for source in sources:
        grouped.setdefault(source.observation_id, []).append(
            ObservationSourceResponse.model_validate(source)
        )
    observations = [
        ObservationResponse(
            **{
                name: getattr(finding, name)
                for name in (
                    "id",
                    "monitor_run_id",
                    "item_key",
                    "summary",
                    "created_at",
                )
            },
            sources=grouped.get(finding.id, []),
        )
        for finding in findings
    ]
    alerts = list(
        await session.scalars(
            select(Alert).where(Alert.monitor_run_id == run.id)
        )
    )
    links = await session.execute(
        select(
            AlertObservation.alert_id, AlertObservation.observation_id
        ).where(AlertObservation.monitor_run_id == run.id)
    )
    alert_links: dict[UUID, set[UUID]] = {}
    for alert_id, observation_id in links:
        alert_links.setdefault(alert_id, set()).add(observation_id)
    changes = await session.execute(
        select(MemoryRevision, Memory.kind)
        .join(Memory)
        .where(
            MemoryRevision.source_run_id == run.id,
            Memory.monitor_id == monitor.id,
        )
        .order_by(MemoryRevision.created_at, MemoryRevision.id)
    )
    current = await session.scalars(
        select(Memory)
        .where(Memory.monitor_id == monitor.id)
        .order_by(Memory.kind)
    )
    chat = await session.scalar(
        select(Chat).where(Chat.monitor_id == monitor.id)
    )
    message_count = (
        await session.scalar(
            select(func.count(Message.id)).where(Message.chat_id == chat.id)
        )
        if chat
        else 0
    )
    profile = await session.scalar(
        select(Memory.id).where(
            Memory.user_id == owner.id, Memory.kind == "user_profile"
        )
    )
    candidates = await session.scalars(
        select(AlertCandidate)
        .where(AlertCandidate.monitor_run_id == run.id)
        .order_by(AlertCandidate.observation_ref)
    )
    job = await session.scalar(
        select(AlertJob).where(AlertJob.monitor_run_id == run.id)
    )
    calls = await session.scalars(
        select(RunModelCall)
        .where(RunModelCall.monitor_run_id == run.id)
        .order_by(RunModelCall.created_at, RunModelCall.id)
    )
    return AdminRunDetail(
        run=MonitorRunResponse.model_validate(run),
        idempotency_key=run.idempotency_key,
        monitor=MonitorResponse.model_validate(monitor),
        monitor_deleted_at=monitor.deleted_at,
        owner=RunAccount.model_validate(owner),
        snapshot_phase="execution" if run.attempt_count else "enqueue",
        observations=observations,
        memories_used=run.context_snapshot.get("memories", []),
        memory_changes=[
            {
                "id": revision.id,
                "memory_id": revision.memory_id,
                "kind": kind,
                "version": revision.version,
                "content": revision.content,
                "change_reason": revision.change_reason,
                "source_kind": revision.source_kind,
                "created_at": revision.created_at,
            }
            for revision, kind in changes
        ],
        current_memories=[
            {
                "id": memory.id,
                "kind": memory.kind,
                "version": memory.version,
                "content": memory.content,
                "updated_at": memory.updated_at,
            }
            for memory in current
        ],
        context_policy={
            "scope": "current_worker_policy",
            "note": "Actual recorded payloads below are authoritative for a request. This policy is not a historical record of omitted content.",
            "personal_profile": {
                "present_now": profile is not None,
                "passed_separately": False,
            },
            "chat_messages": {
                "count_now": message_count or 0,
                "passed_separately": False,
            },
            "chat_summary": {
                "present_now": bool(chat and chat.summary),
                "passed_separately": False,
            },
            "request_inputs": {
                "research": ["objective", "monitor memory kinds/content"],
                "extraction": [
                    "objective",
                    "research sections",
                    "citation/source dictionary",
                ],
                "knowledge": [
                    "objective",
                    "previous knowledge",
                    "referenced observations",
                    "monitor context",
                    "historical observations/published alerts",
                    "initial-run flag",
                ],
                "composition": [
                    "objective",
                    "eligible candidates and sources",
                    "monitor context",
                ],
            },
        },
        alerts=[
            AlertDetailResponse(
                **AlertResponse.model_validate(alert).model_dump(),
                observations=[
                    observation
                    for observation in observations
                    if observation.id in alert_links.get(alert.id, set())
                ],
            )
            for alert in alerts
        ],
        candidates=[
            RunCandidateResponse.model_validate(candidate)
            for candidate in candidates
        ],
        alert_job=RunJobResponse.model_validate(job) if job else None,
        model_calls=[ModelCallResponse.model_validate(call) for call in calls],
    )
