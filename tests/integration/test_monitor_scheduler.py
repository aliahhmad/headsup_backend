"""Test persisted scheduling, coalescing and PostgreSQL locking."""

# Scenario counts and time intervals are intentionally literal.
# ruff: noqa: PLR2004

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.database.db import Base
from app.managers import monitor as manager
from app.managers.monitor_worker import process_next
from app.models.monitor import Monitor, MonitorRun
from app.models.queue import QueueControl
from app.models.user import User
from app.schemas.request.monitor import MonitorCreate, MonitorUpdate
from app.schemas.research import ResearchResult

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
DUE = NOW - timedelta(hours=3)


@pytest_asyncio.fixture
async def scheduler_sessions(
    test_engine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Keep committed concurrency rows out of other workers' global queues."""
    schema = f"scheduler_{uuid4().hex}"
    engine = test_engine.execution_options(schema_translate_map={None: schema})
    async with engine.begin() as connection:
        await connection.execute(CreateSchema(schema))
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))


@pytest_asyncio.fixture
async def scheduled(test_db) -> tuple[User, Monitor, MonitorRun]:
    """An eligible monitor whose initial check has already finished."""
    owner = User(
        email=f"{uuid4().hex}@example.test",
        password="unused-scheduler-password",  # noqa: S106
        first_name="Schedule",
        last_name="Owner",
        verified=True,
        banned=False,
    )
    test_db.add(owner)
    await test_db.flush()
    monitor = await manager.create_monitor(
        MonitorCreate(
            name="Scheduled topic",
            objective="Watch supported new developments",
            monitor_context="Retain this context",
            check_interval_seconds=3600,
        ),
        owner.id,
        test_db,
    )
    initial = await test_db.scalar(
        select(MonitorRun).where(MonitorRun.monitor_id == monitor.id)
    )
    initial.status = "succeeded"
    initial.completed_at = DUE - timedelta(hours=1)
    monitor.next_run_at = DUE
    await test_db.commit()
    return owner, monitor, initial


async def tick(sessions, *, now=NOW, limit=100) -> int:
    """Use the same short transaction as the API's scheduling loop."""
    async with sessions() as session, session.begin():
        return await manager.queue_due_monitors(session, now=now, limit=limit)


async def runs_for(session, monitor) -> Sequence[MonitorRun]:
    """Read only this scenario's runs, including the initial baseline."""
    return (
        await session.scalars(
            select(MonitorRun)
            .where(MonitorRun.monitor_id == monitor.id)
            .order_by(MonitorRun.created_at, MonitorRun.id)
        )
    ).all()


async def test_due_boundary_and_frozen_schedule_inputs(
    scheduled, test_db, test_sessionmaker
) -> None:
    """Wait until due, then preserve the intended slot and enqueue snapshots."""
    _, monitor, initial = scheduled
    monitor.next_run_at = NOW
    original_version, updated = monitor.version, monitor.updated_at
    await test_db.commit()
    assert (
        await tick(test_sessionmaker, now=NOW - timedelta(microseconds=1)) == 0
    )
    assert await tick(test_sessionmaker) == 1
    runs = await runs_for(test_db, monitor)
    run = next(row for row in runs if row.id != initial.id)
    assert run.status == "queued"
    assert run.trigger == "scheduled"
    assert run.scheduled_for == NOW
    assert run.idempotency_key == f"schedule:{NOW.isoformat()}"
    assert run.monitor_version == original_version
    assert run.objective_snapshot == monitor.objective
    assert any(
        memory["content"] == "Retain this context"
        for memory in run.context_snapshot["memories"]
    )
    await test_db.refresh(monitor)
    assert monitor.next_run_at == NOW + timedelta(hours=1)
    assert monitor.version == original_version
    assert monitor.updated_at == updated


async def test_downtime_catches_up_once_and_next_interval_can_queue_again(
    scheduled, test_db, test_sessionmaker
) -> None:
    """Missed slots coalesce and durable timers survive new sessions."""
    _, monitor, initial = scheduled
    assert await tick(test_sessionmaker) == 1
    assert await tick(test_sessionmaker) == 0
    runs = await runs_for(test_db, monitor)
    assert len(runs) == 2
    catchup = next(row for row in runs if row.id != initial.id)
    assert catchup.scheduled_for == DUE
    catchup.status = "failed"
    catchup.completed_at = NOW
    await test_db.commit()
    assert await tick(test_sessionmaker, now=NOW + timedelta(minutes=59)) == 0
    assert await tick(test_sessionmaker, now=NOW + timedelta(hours=1)) == 1
    runs = await runs_for(test_db, monitor)
    assert len(runs) == 3
    assert len({row.idempotency_key for row in runs}) == 3
    assert catchup.status == "failed"  # This is a new run, not a replay.


@pytest.mark.parametrize("state", ["queued", "running"])
async def test_busy_monitor_coalesces_instead_of_accumulating_checks(
    scheduled, test_db, test_sessionmaker, state
) -> None:
    """Paused processing and slow research retain at most one active run."""
    _, monitor, initial = scheduled
    initial.status = state
    initial.trigger = "manual"
    initial.completed_at = None
    await test_db.commit()
    assert await tick(test_sessionmaker) == 1
    assert await tick(test_sessionmaker, now=NOW + timedelta(hours=2)) == 1
    runs = await runs_for(test_db, monitor)
    assert len(runs) == 1
    assert runs[0].id == initial.id
    assert runs[0].status == state
    assert runs[0].trigger == "manual"
    await test_db.refresh(monitor)
    assert monitor.next_run_at == NOW + timedelta(hours=3)


@pytest.mark.parametrize(
    "state", ["paused", "archived", "deleted", "unverified", "banned"]
)
async def test_ineligible_monitors_never_schedule(
    scheduled, test_db, test_sessionmaker, state
) -> None:
    """Only active, undeleted monitors of eligible owners get recurring work."""
    owner, monitor, _ = scheduled
    if state in {"paused", "archived"}:
        monitor.status = state
        monitor.next_run_at = None
    elif state == "deleted":
        monitor.deleted_at = NOW
        monitor.next_run_at = None
    elif state == "unverified":
        owner.verified = False
    else:
        owner.banned = True
    await test_db.commit()
    assert await tick(test_sessionmaker) == 0
    assert len(await runs_for(test_db, monitor)) == 1


async def test_queue_paused_still_schedules_and_existing_worker_can_execute(
    scheduled, test_db, test_sessionmaker
) -> None:
    """Scheduling needs no paid-work admission; runs remain executable."""
    _, _, initial = scheduled
    test_db.add(QueueControl(id=1, automatic_enabled=False))
    await test_db.commit()
    assert await tick(test_sessionmaker) == 1
    research = AsyncMock(return_value=ResearchResult(observations=[]))
    merger = AsyncMock()
    run_id = await process_next(test_sessionmaker, research, merger)
    assert run_id is not None
    assert run_id != initial.id
    research.assert_awaited_once()
    merger.assert_not_awaited()
    run = await test_db.get(MonitorRun, run_id, populate_existing=True)
    assert run.status == "succeeded"
    assert run.trigger == "scheduled"
    control = await test_db.get(QueueControl, 1)
    assert not control.automatic_enabled


async def test_interval_change_and_pause_apply_to_recurring_checks(
    scheduled, test_db, test_sessionmaker, monkeypatch
) -> None:
    """Configuration edits reset due time; pause cancels scheduled work."""
    _, monitor, _ = scheduled
    monkeypatch.setattr(manager, "utc_now", lambda: NOW)
    await manager.update_monitor(
        monitor,
        MonitorUpdate(expected_version=1, check_interval_seconds=600),
        test_db,
    )
    await test_db.commit()
    assert await tick(test_sessionmaker) == 0
    assert await tick(test_sessionmaker, now=NOW + timedelta(minutes=10)) == 1
    await test_db.refresh(monitor)
    assert monitor.version == 2
    await manager.update_monitor(
        monitor, MonitorUpdate(expected_version=2, status="paused"), test_db
    )
    await test_db.commit()
    assert await tick(test_sessionmaker, now=NOW + timedelta(days=1)) == 0
    runs = await runs_for(test_db, monitor)
    assert not any(row.status == "queued" for row in runs)
    assert monitor.next_run_at is None


async def test_scheduling_batch_is_bounded_and_does_not_starve_other_monitors(
    scheduled, test_db, test_sessionmaker
) -> None:
    """Oldest due monitors are visited across bounded polling transactions."""
    owner, first, _ = scheduled
    other = await manager.create_monitor(
        MonitorCreate(name="Another topic", objective="Watch this too"),
        owner.id,
        test_db,
    )
    other.next_run_at = DUE + timedelta(minutes=1)
    await test_db.commit()
    assert await tick(test_sessionmaker, limit=1) == 1
    await test_db.refresh(first)
    await test_db.refresh(other)
    assert first.next_run_at > NOW
    assert other.next_run_at < NOW
    assert await tick(test_sessionmaker, limit=1) == 1
    assert await tick(test_sessionmaker, limit=1) == 0


async def test_failed_tick_rolls_back_runs_and_all_timer_advances(
    scheduled, test_db, test_sessionmaker, monkeypatch
) -> None:
    """A failure midway through the batch cannot lose a due slot."""
    owner, first, _ = scheduled
    second = await manager.create_monitor(
        MonitorCreate(name="Second due topic", objective="Watch it"),
        owner.id,
        test_db,
    )
    second.next_run_at = DUE + timedelta(minutes=1)
    await test_db.commit()
    original = manager._enqueue
    calls = 0

    async def fail_second(
        monitor: Monitor,
        session: AsyncSession,
        *,
        trigger: str,
        key: str,
        scheduled_for: datetime | None = None,
    ) -> MonitorRun:
        nonlocal calls
        calls += 1
        if calls == 2:
            message = "Synthetic scheduling failure"
            raise RuntimeError(message)
        return await original(
            monitor,
            session,
            trigger=trigger,
            key=key,
            scheduled_for=scheduled_for,
        )

    monkeypatch.setattr(manager, "_enqueue", fail_second)
    with pytest.raises(RuntimeError, match="Synthetic"):
        await tick(test_sessionmaker)
    await test_db.refresh(first)
    await test_db.refresh(second)
    assert first.next_run_at == DUE
    assert second.next_run_at == DUE + timedelta(minutes=1)
    assert len(await runs_for(test_db, first)) == 1
    assert len(await runs_for(test_db, second)) == 1


@pytest.mark.parametrize("manual_first", [True, False])
async def test_independent_schedulers_skip_locked_monitor_and_never_duplicate(
    scheduler_sessions,
    manual_first,
) -> None:
    """Separate PostgreSQL transactions serialize with manual requests too."""
    sessions = scheduler_sessions
    async with sessions() as session, session.begin():
        owner = User(
            email=f"{uuid4().hex}@example.test",
            password="unused-scheduler-concurrency",  # noqa: S106
            first_name="Concurrent",
            last_name="Owner",
            verified=True,
            banned=False,
        )
        session.add(owner)
        await session.flush()
        monitor = await manager.create_monitor(
            MonitorCreate(
                name="Concurrent schedule", objective="Watch changes"
            ),
            owner.id,
            session,
        )
        initial = await session.scalar(
            select(MonitorRun).where(MonitorRun.monitor_id == monitor.id)
        )
        initial.status = "succeeded"
        monitor.next_run_at = DUE
    try:
        if manual_first:
            async with sessions() as first, first.begin():
                locked = await manager.owned_monitor(
                    monitor.id, owner.id, first, lock=True
                )
                # A manual request holds the same parent lock as scheduling.
                assert await asyncio.wait_for(tick(sessions), timeout=2) == 0
                manual = await manager.queue_manual_check(
                    locked, uuid4(), first
                )
        async with sessions() as first, first.begin():
            assert await manager.queue_due_monitors(first, now=NOW) == 1
            assert await asyncio.wait_for(tick(sessions), timeout=2) == 0
        assert await tick(sessions) == 0
        async with sessions() as session:
            runs = await runs_for(session, monitor)
            assert len(runs) == 2
            assert sum(row.status == "queued" for row in runs) == 1
            if manual_first:
                assert any(row.id == manual.id for row in runs)
            assert await session.scalar(
                select(func.count(MonitorRun.id)).where(
                    MonitorRun.monitor_id == monitor.id,
                    MonitorRun.trigger == "scheduled",
                )
            ) == int(not manual_first)
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(Monitor).where(Monitor.id == monitor.id)
            )
            await session.execute(delete(User).where(User.id == owner.id))
