"""Durable queue policy and background dispatch shared by API and CLI workers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config.settings import get_settings
from app.logs import logger
from app.managers.monitor_worker import process_next
from app.models.monitor import AlertJob, MonitorRun, utc_now
from app.models.queue import QueueControl, QueueExecution
from app.schemas.response.queue import (
    QueueExecutionResponse,
    QueueStateResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )

    from app.managers.alert import AlertComposer
    from app.managers.monitor_worker import KnowledgeMerger, Researcher

POLL_SECONDS = 5
# A database-scoped session lock, held on a dedicated connection, serializes all
# updated API/CLI dispatchers. No business transaction spans a provider request.
DISPATCH_LOCK = 723004180216


def provider_configured() -> bool:
    """Report only key presence; credentials never leave server settings."""
    return bool(get_settings().openai_api_key.get_secret_value().strip())


async def control_row(
    session: AsyncSession, *, lock: bool = False
) -> QueueControl:
    """Initialize atomically; serialize policy changes and manual admissions."""
    await session.execute(
        insert(QueueControl)
        .values(id=1, automatic_enabled=False)
        .on_conflict_do_nothing()
    )
    statement = (
        select(QueueControl)
        .where(QueueControl.id == 1)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    return (await session.scalars(statement)).one()


async def outstanding_execution(session: AsyncSession) -> QueueExecution | None:
    """Only one manual request may be admitted while processing is paused."""
    return await session.scalar(
        select(QueueExecution)
        .where(QueueExecution.status.in_(["pending", "processing"]))
        .order_by(QueueExecution.created_at)
        .limit(1)
    )


def require_provider() -> None:
    """Reject a paid-work request before accepting it if unconfigured."""
    if not provider_configured():
        raise HTTPException(
            503,
            "Configure OPENAI_API_KEY on the server before processing the queue.",
        )


async def set_automatic(
    session: AsyncSession, *, enabled: bool, user_id: int
) -> None:
    """Pause future claims without interrupting work already admitted."""
    control = await control_row(session, lock=True)
    if enabled:
        require_provider()
        execution = await outstanding_execution(session)
        if execution is not None and execution.mode == "manual":
            raise HTTPException(
                409,
                "Wait for the single-step request to finish before enabling automatic processing.",
            )
    control.automatic_enabled = enabled
    control.updated_at = utc_now()
    control.updated_by_user_id = user_id


async def request_single_step(
    session: AsyncSession, *, user_id: int
) -> QueueExecution:
    """Persist one request; HTTP never waits for research/model completion."""
    control = await control_row(session, lock=True)
    if control.automatic_enabled:
        raise HTTPException(
            409, "Turn off automatic processing before running one item."
        )
    if await outstanding_execution(session) is not None:
        raise HTTPException(
            409,
            "A queue item is already pending or processing. Wait for it to finish.",
        )
    require_provider()
    execution = QueueExecution(
        mode="manual", status="pending", requested_by_user_id=user_id
    )
    session.add(execution)
    await session.flush()
    return execution


async def queue_state(session: AsyncSession) -> QueueStateResponse:
    """Show liveness separately from enabled policy, work counts and outcomes."""
    control = await session.get(QueueControl, 1)
    now = utc_now()
    research = {
        state: count
        for state, count in (
            await session.execute(
                select(MonitorRun.status, func.count())
                .where(MonitorRun.status.in_(["queued", "running"]))
                .group_by(MonitorRun.status)
            )
        ).all()
    }
    due = or_(
        and_(AlertJob.status == "pending", AlertJob.next_attempt_at <= now),
        and_(AlertJob.status == "processing", AlertJob.lease_expires_at <= now),
    )
    counts = (
        await session.execute(
            select(
                func.count().filter(due),
                func.count().filter(
                    and_(
                        AlertJob.status == "pending",
                        AlertJob.next_attempt_at > now,
                    )
                ),
                func.count().filter(
                    and_(
                        AlertJob.status == "processing",
                        AlertJob.lease_expires_at > now,
                    )
                ),
            ).select_from(AlertJob)
        )
    ).one()
    recent = (
        await session.scalars(
            select(QueueExecution)
            .order_by(
                QueueExecution.created_at.desc(), QueueExecution.id.desc()
            )
            .limit(20)
        )
    ).all()
    outstanding = await outstanding_execution(session)
    heartbeat = control.heartbeat_at if control else None
    return QueueStateResponse(
        automatic_enabled=bool(control and control.automatic_enabled),
        provider_configured=provider_configured(),
        updated_at=control.updated_at if control else None,
        updated_by_user_id=control.updated_by_user_id if control else None,
        heartbeat_at=heartbeat,
        worker_online=bool(
            heartbeat and heartbeat > now - timedelta(seconds=20)
        ),
        worker_error_code=control.worker_error_code if control else None,
        queued_research=research.get("queued", 0),
        running_research=research.get("running", 0),
        due_alerts=counts[0],
        waiting_alerts=counts[1],
        processing_alerts=counts[2],
        outstanding=QueueExecutionResponse.model_validate(outstanding)
        if outstanding
        else None,
        recent=[QueueExecutionResponse.model_validate(item) for item in recent],
    )


@asynccontextmanager
async def dispatch_lock(engine: AsyncEngine) -> AsyncIterator[bool]:
    """Use a session lock without keeping an idle database transaction open."""
    async with engine.connect() as connection:
        connection = await connection.execution_options(
            isolation_level="AUTOCOMMIT"
        )
        acquired = False
        try:
            acquired = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": DISPATCH_LOCK},
                )
            )
        except BaseException:
            # An interrupted acquisition has an unknown server-side result.
            # Closing the physical connection releases any session lock.
            await asyncio.shield(connection.invalidate())
            raise
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await asyncio.shield(
                        connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": DISPATCH_LOCK},
                        )
                    )
                except BaseException:
                    # Never return a potentially locked physical connection to
                    # the pool, even during task cancellation/connection failure.
                    await asyncio.shield(connection.invalidate())
                    raise


async def finish_execution(
    sessions: async_sessionmaker[AsyncSession],
    execution_id: UUID,
    *,
    run_id: UUID | None = None,
    error: str | None = None,
    interrupted: bool = False,
) -> None:
    """Keep controller outcome distinct from research and composition statuses."""
    async with sessions() as session, session.begin():
        execution = await session.get(QueueExecution, execution_id)
        if execution is None:
            return
        if run_id is None and error is None and execution.mode == "automatic":
            await session.delete(execution)
            return
        execution.monitor_run_id = run_id or execution.monitor_run_id
        execution.completed_at = utc_now()
        execution.status = (
            "interrupted"
            if interrupted
            else "failed"
            if error
            else "completed"
            if run_id
            else "empty"
        )
        execution.error_code = error
        if execution.monitor_run_id:
            run = await session.get(MonitorRun, execution.monitor_run_id)
            job = await session.scalar(
                select(AlertJob).where(
                    AlertJob.monitor_run_id == execution.monitor_run_id
                )
            )
            execution.run_status = run.status if run else None
            execution.alert_status = job.status if job else None
            execution.error_code = (
                error
                or (run.error_code if run else None)
                or (job.error_code if job else None)
            )


async def dispatch_next(
    sessions: async_sessionmaker[AsyncSession],
    researcher: Researcher,
    knowledge_merger: KnowledgeMerger,
    alert_composer: AlertComposer | None = None,
    *,
    once: bool = False,
    resume_alerts: bool = True,
) -> UUID | None:
    """Admit at most one work item under shared policy and a global lock."""
    bind = sessions.kw["bind"]
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    async with dispatch_lock(engine) as acquired:
        if not acquired:
            return None
        async with sessions() as session, session.begin():
            control = await control_row(session, lock=True)
            # Owning the session lock proves no other managed dispatcher holds
            # these attempts. Do not replay paid research after a hard crash.
            await session.execute(
                update(QueueExecution)
                .where(QueueExecution.status == "processing")
                .values(
                    status="interrupted",
                    completed_at=utc_now(),
                    error_code="queue_worker_interrupted",
                )
            )
            execution = await outstanding_execution(session)
            if execution is None:
                if not once and not control.automatic_enabled:
                    return None
                execution = QueueExecution(
                    mode="manual" if once else "automatic", status="pending"
                )
                session.add(execution)
            execution.status = "processing"
            execution.started_at = utc_now()
            await session.flush()
            execution_id = execution.id

        async def on_claim(run_id: UUID) -> None:
            async with sessions() as session, session.begin():
                await session.execute(
                    update(QueueExecution)
                    .where(QueueExecution.id == execution_id)
                    .values(monitor_run_id=run_id)
                )

        try:
            run_id = await process_next(
                sessions,
                researcher,
                knowledge_merger,
                alert_composer,
                resume_alerts=resume_alerts,
                on_claim=on_claim,
            )
            await finish_execution(sessions, execution_id, run_id=run_id)
            return run_id
        except asyncio.CancelledError:
            await asyncio.shield(
                finish_execution(
                    sessions,
                    execution_id,
                    error="queue_worker_interrupted",
                    interrupted=True,
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001
            await finish_execution(
                sessions, execution_id, error="queue_execution_failed"
            )
            logger.error("Queue dispatch failed ({})", type(exc).__name__)
            return None


async def heartbeat(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Keep liveness visible even while a model call is in progress."""
    while True:
        try:
            async with sessions() as session, session.begin():
                row = await control_row(session)
                row.heartbeat_at = utc_now()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Queue heartbeat unavailable ({})", type(exc).__name__
            )
        await asyncio.sleep(POLL_SECONDS)


async def serve_queue(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Observe durable requests continuously; paid work requires explicit action."""
    from app.managers.research import OpenAIResearch

    pulse = asyncio.create_task(heartbeat(sessions))
    try:
        async with httpx.AsyncClient() as client:
            provider = OpenAIResearch(client, get_settings())
            while True:
                try:
                    if provider_configured():
                        await dispatch_next(
                            sessions,
                            provider,
                            provider.merge_knowledge,
                            provider.compose_alert,
                        )
                    async with sessions() as session, session.begin():
                        row = await control_row(session)
                        row.worker_error_code = (
                            None
                            if provider_configured()
                            else "openai_key_missing"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Queue service unavailable ({})", type(exc).__name__
                    )
                    with suppress(Exception):
                        async with sessions() as session, session.begin():
                            row = await control_row(session)
                            row.worker_error_code = "queue_service_unavailable"
                await asyncio.sleep(POLL_SECONDS)
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse


@asynccontextmanager
async def queue_service(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Attach controller lifetime to the API, including graceful cancellation."""
    task = asyncio.create_task(serve_queue(sessions))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
