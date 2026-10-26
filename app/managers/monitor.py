"""Transactional monitor lifecycle and durable check-request management."""

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitor import (
    Chat,
    Memory,
    MemoryRevision,
    Monitor,
    MonitorRun,
    utc_now,
)
from app.schemas.request.monitor import (
    MonitorCreate,
    MonitorStatus,
    MonitorUpdate,
)
from app.schemas.response.monitor import (
    MonitorChatResponse,
    MonitorDetailResponse,
    MonitorMemoryResponse,
    MonitorResponse,
    MonitorRunResponse,
)

MANUAL_CHECK_COOLDOWN_SECONDS = 60


def monitors_query(
    user_id: int, state: MonitorStatus | None
) -> Select[tuple[Monitor]]:
    """Limit monitor reads to their owner and exclude soft-deleted records."""
    query = select(Monitor).where(
        Monitor.user_id == user_id, Monitor.deleted_at.is_(None)
    )
    if state is not None:
        query = query.where(Monitor.status == state)
    return query.order_by(Monitor.created_at.desc(), Monitor.id.desc())


async def owned_monitor(
    monitor_id: UUID, user_id: int, session: AsyncSession, *, lock: bool = False
) -> Monitor:
    """Authorize access before returning or locking a monitor."""
    query = select(Monitor).where(
        Monitor.id == monitor_id,
        Monitor.user_id == user_id,
        Monitor.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update().execution_options(
            populate_existing=True
        )
    monitor = await session.scalar(query)
    if monitor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Monitor not found")
    return monitor


def check_version(monitor: Monitor, expected_version: int) -> None:
    """Refuse to overwrite configuration changed by another request."""
    if monitor.version != expected_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Monitor has changed. Reload it and retry "
            "with the current version.",
        )


async def _memories(
    monitor_id: UUID, session: AsyncSession
) -> Sequence[Memory]:
    return (
        await session.scalars(
            select(Memory)
            .where(Memory.monitor_id == monitor_id)
            .order_by(Memory.kind)
        )
    ).all()


async def _enqueue(
    monitor: Monitor, session: AsyncSession, *, trigger: str, key: str
) -> MonitorRun:
    """Save one check request while the caller holds the monitor row lock."""
    existing = await session.scalar(
        select(MonitorRun).where(
            MonitorRun.monitor_id == monitor.id,
            MonitorRun.status.in_(["queued", "running"]),
        )
    )
    if existing is not None:
        return existing
    memories = await _memories(monitor.id, session)
    run = MonitorRun(
        monitor_id=monitor.id,
        trigger=trigger,
        idempotency_key=key,
        scheduled_for=utc_now() if trigger == "scheduled" else None,
        monitor_version=monitor.version,
        objective_snapshot=monitor.objective,
        context_snapshot={
            "memories": [
                {
                    "memory_id": str(memory.id),
                    "kind": memory.kind,
                    "version": memory.version,
                    "content": memory.content,
                }
                for memory in memories
            ]
        },
    )
    session.add(run)
    await session.flush()
    return run


async def create_monitor(
    data: MonitorCreate, user_id: int, session: AsyncSession
) -> Monitor:
    """Initialize configuration, chat, memories/revisions, and a first check."""
    monitor = Monitor(
        user_id=user_id,
        name=data.name,
        objective=data.objective,
        check_interval_seconds=data.check_interval_seconds,
        next_run_at=utc_now() + timedelta(seconds=data.check_interval_seconds),
    )
    session.add(monitor)
    await session.flush()
    session.add(Chat(monitor_id=monitor.id))
    for kind, content in (
        ("monitor_context", data.monitor_context),
        ("monitor_knowledge", ""),
    ):
        memory = Memory(
            user_id=user_id, monitor_id=monitor.id, kind=kind, content=content
        )
        session.add(memory)
        await session.flush()
        session.add(
            MemoryRevision(
                memory_id=memory.id,
                version=1,
                content=content,
                change_reason="Monitor created",
                source_kind="initialization",
            )
        )
    await session.flush()
    await _enqueue(monitor, session, trigger="initial", key="initial")
    return monitor


async def monitor_detail(
    monitor: Monitor, session: AsyncSession
) -> MonitorDetailResponse:
    """Read current workspace state without fetching memory revision history."""
    chat = (
        await session.scalars(select(Chat).where(Chat.monitor_id == monitor.id))
    ).one()
    memories = await _memories(monitor.id, session)
    latest = await session.scalar(
        select(MonitorRun)
        .where(MonitorRun.monitor_id == monitor.id)
        .order_by(MonitorRun.created_at.desc(), MonitorRun.id.desc())
        .limit(1)
    )
    return MonitorDetailResponse(
        **MonitorResponse.model_validate(monitor).model_dump(),
        chat=MonitorChatResponse.model_validate(chat),
        memories=[
            MonitorMemoryResponse.model_validate(memory) for memory in memories
        ],
        latest_run=MonitorRunResponse.model_validate(latest)
        if latest
        else None,
    )


async def _cancel_queued(
    monitor_id: UUID, session: AsyncSession, *, automatic_only: bool = False
) -> None:
    query = update(MonitorRun).where(
        MonitorRun.monitor_id == monitor_id, MonitorRun.status == "queued"
    )
    if automatic_only:
        query = query.where(MonitorRun.trigger.in_(["initial", "scheduled"]))
    await session.execute(
        query.values(status="cancelled", completed_at=utc_now())
    )


async def update_monitor(
    monitor: Monitor, data: MonitorUpdate, session: AsyncSession
) -> Monitor:
    """Apply a configuration edit under a version check and row lock."""
    check_version(monitor, data.expected_version)
    changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    changes = {
        key: value
        for key, value in changes.items()
        if getattr(monitor, key) != value
    }
    if not changes:
        return monitor
    previous_status = monitor.status
    for key, value in changes.items():
        setattr(monitor, key, value)
    monitor.version += 1
    monitor.updated_at = utc_now()
    if monitor.status != "active":
        monitor.next_run_at = None
    elif previous_status != "active" or "check_interval_seconds" in changes:
        monitor.next_run_at = utc_now() + timedelta(
            seconds=monitor.check_interval_seconds
        )
    if monitor.status == "archived":
        await _cancel_queued(monitor.id, session)
    elif monitor.status == "paused":
        await _cancel_queued(monitor.id, session, automatic_only=True)
    elif previous_status != "active":
        await _enqueue(
            monitor,
            session,
            trigger="scheduled",
            key=f"resume:{monitor.version}",
        )
    await session.flush()
    return monitor


async def delete_monitor(
    monitor: Monitor, expected_version: int, session: AsyncSession
) -> None:
    """Hide a monitor and cancel queued work while preserving its history."""
    check_version(monitor, expected_version)
    monitor.deleted_at = utc_now()
    monitor.updated_at = monitor.deleted_at
    monitor.next_run_at = None
    monitor.version += 1
    await _cancel_queued(monitor.id, session)
    await session.flush()


async def queue_manual_check(
    monitor: Monitor, client_request_id: UUID, session: AsyncSession
) -> MonitorRun:
    """Coalesce checks and deduplicate retries before rate limiting."""
    if monitor.status == "archived":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Archived monitors cannot run"
        )
    key = f"manual:{client_request_id}"
    existing = await session.scalar(
        select(MonitorRun)
        .where(
            MonitorRun.monitor_id == monitor.id,
            (MonitorRun.idempotency_key == key)
            | MonitorRun.status.in_(["queued", "running"]),
        )
        .order_by((MonitorRun.idempotency_key == key).desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    recent = await session.scalar(
        select(MonitorRun.id)
        .where(
            MonitorRun.monitor_id == monitor.id,
            MonitorRun.trigger == "manual",
            MonitorRun.created_at
            > utc_now() - timedelta(seconds=MANUAL_CHECK_COOLDOWN_SECONDS),
        )
        .limit(1)
    )
    if recent is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Wait a minute before requesting another check.",
            headers={"Retry-After": str(MANUAL_CHECK_COOLDOWN_SECONDS)},
        )
    return await _enqueue(monitor, session, trigger="manual", key=key)
