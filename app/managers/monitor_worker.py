"""Research outside transactions; atomically save evidence and knowledge."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select

from app.logs import logger
from app.managers.alert import (
    AlertComposer,
    alert_history,
    persist_decisions,
    process_next_alert,
)
from app.managers.monitor_state import can_execute, memory_snapshot
from app.managers.research import ResearchError
from app.models.monitor import (
    AlertJob,
    Memory,
    MemoryRevision,
    Monitor,
    MonitorRun,
    Observation,
    ObservationSource,
    utc_now,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.schemas.research import KnowledgeUpdate, ResearchResult

RESEARCH_TIMEOUT_SECONDS = 300


class Researcher(Protocol):
    """Allow offline fixtures and mocked research without paid API calls."""

    async def __call__(
        self, objective: str, context: dict[str, Any]
    ) -> ResearchResult:
        """Return supported findings for the frozen run inputs."""
        ...


class KnowledgeMerger(Protocol):
    """Produce full knowledge snapshots without writing database state."""

    async def __call__(
        self,
        objective: str,
        previous_knowledge: str,
        result: ResearchResult,
        *,
        context: dict[str, Any] | None = None,
    ) -> KnowledgeUpdate:
        """Preserve useful prior knowledge and merge evidence-backed changes."""
        ...


@dataclass(frozen=True)
class ClaimedRun:
    """Detached inputs and attempt identity for one execution."""

    id: UUID
    monitor_id: UUID
    monitor_version: int
    attempt: int
    objective: str
    context: dict[str, Any]


async def claim_next(
    sessions: async_sessionmaker[AsyncSession],
) -> ClaimedRun | None:
    """Lock parent before child, skip busy monitors, and freeze first inputs."""
    async with sessions() as session, session.begin():
        row = (
            await session.execute(
                select(MonitorRun, Monitor)
                .join(Monitor, Monitor.id == MonitorRun.monitor_id)
                .where(
                    MonitorRun.status == "queued",
                    ~select(AlertJob.id)
                    .where(
                        AlertJob.monitor_id == Monitor.id,
                        AlertJob.status.in_(["pending", "processing"]),
                    )
                    .exists(),
                )
                .order_by(MonitorRun.created_at, MonitorRun.id)
                .limit(1)
                .with_for_update(of=Monitor, skip_locked=True)
            )
        ).first()
        if row is None:
            return None
        run, monitor = row
        await session.refresh(run, with_for_update=True)
        if run.status != "queued":
            return None
        if not can_execute(monitor, run):
            run.status = "cancelled"
            run.completed_at = utc_now()
            return None
        current_context = await memory_snapshot(monitor, session)
        if run.attempt_count == 0:
            run.objective_snapshot = monitor.objective
            run.monitor_version = monitor.version
            run.context_snapshot = {
                **current_context,
                "history": await alert_history(monitor.id, session),
                "is_initial": run.trigger == "initial",
            }
        elif (
            run.monitor_version != monitor.version
            or run.context_snapshot.get("memories")
            != current_context["memories"]
        ):
            run.status = "cancelled"
            run.error_code = "superseded_inputs"
            run.completed_at = utc_now()
            return None
        run.status = "running"
        run.attempt_count += 1
        run.started_at = utc_now()
        run.completed_at = None
        run.error_code = None
        return ClaimedRun(
            id=run.id,
            monitor_id=monitor.id,
            monitor_version=run.monitor_version,
            attempt=run.attempt_count,
            objective=run.objective_snapshot,
            context=run.context_snapshot,
        )


async def fail_run(
    sessions: async_sessionmaker[AsyncSession],
    claimed: ClaimedRun,
    code: str,
) -> None:
    """Record a safe error without touching evidence or newer attempts."""
    async with sessions() as session, session.begin():
        run = await session.get(MonitorRun, claimed.id, with_for_update=True)
        if (
            run is not None
            and run.status == "running"
            and run.attempt_count == claimed.attempt
        ):
            run.status = "failed"
            run.error_code = code
            run.completed_at = utc_now()


def stable_key(value: str) -> str:
    """Use deterministic keys for identical normalized output on retries."""
    return hashlib.sha256(value.encode()).hexdigest()


async def save_result(
    sessions: async_sessionmaker[AsyncSession],
    claimed: ClaimedRun,
    result: ResearchResult,
    knowledge_update: KnowledgeUpdate | None,
) -> None:
    """Reject stale inputs; commit evidence, knowledge, and success together."""
    async with sessions() as session, session.begin():
        monitor = await session.get(
            Monitor, claimed.monitor_id, with_for_update=True
        )
        run = await session.get(MonitorRun, claimed.id, with_for_update=True)
        if (
            monitor is None
            or run is None
            or run.status != "running"
            or run.attempt_count != claimed.attempt
        ):
            return
        if (
            not can_execute(monitor, run)
            or monitor.version != claimed.monitor_version
            or (await memory_snapshot(monitor, session))["memories"]
            != claimed.context.get("memories")
        ):
            run.status = "cancelled"
            run.error_code = "superseded_inputs"
            run.completed_at = utc_now()
            return

        if bool(result.observations) != (knowledge_update is not None):
            code = "invalid_knowledge_update"
            raise ResearchError(code)

        retrieved_at = utc_now()
        findings: dict[str, Observation] = {}
        references: dict[str, Observation] = {}
        evidence_keys: set[tuple[str, str]] = set()
        for index, finding in enumerate(result.observations, start=1):
            key = stable_key(" ".join(finding.summary.split()))
            if key not in findings:
                observation = Observation(
                    monitor_run_id=run.id,
                    item_key=key,
                    summary=finding.summary,
                )
                session.add(observation)
                await session.flush()
                findings[key] = observation
            references[f"finding_{index}"] = findings[key]
            for source in finding.sources:
                url = str(source.source_url)
                source_key = stable_key(url)
                if (key, source_key) in evidence_keys:
                    continue
                evidence_keys.add((key, source_key))
                session.add(
                    ObservationSource(
                        observation_id=findings[key].id,
                        source_key=source_key,
                        source_url=url,
                        source_title=source.source_title,
                        retrieved_at=retrieved_at,
                    )
                )
        if knowledge_update is not None:
            knowledge = (
                await session.scalars(
                    select(Memory).where(
                        Memory.monitor_id == monitor.id,
                        Memory.user_id == monitor.user_id,
                        Memory.kind == "monitor_knowledge",
                    )
                )
            ).one()
            if knowledge.content.strip() != knowledge_update.content:
                knowledge.content = knowledge_update.content
                knowledge.version += 1
                knowledge.updated_at = utc_now()
                session.add(
                    MemoryRevision(
                        memory_id=knowledge.id,
                        version=knowledge.version,
                        content=knowledge.content,
                        change_reason=knowledge_update.change_reason,
                        source_kind="run",
                        source_run_id=run.id,
                    )
                )
        # Accept knowledge before recording post-merge versions for composition.
        await session.flush()
        await persist_decisions(
            session, monitor, run, knowledge_update, references
        )
        # Evidence, knowledge, decisions, and durable composition work
        # commit together.
        await session.flush()
        run.status = "succeeded"
        run.completed_at = utc_now()
        run.error_code = None


async def research_and_merge(
    claimed: ClaimedRun,
    researcher: Researcher,
    knowledge_merger: KnowledgeMerger,
) -> tuple[ResearchResult, KnowledgeUpdate | None]:
    """Keep every provider call outside transactions and within one timeout."""
    result = await researcher(claimed.objective, claimed.context)
    if not result.observations:
        return result, None
    knowledge = [
        item
        for item in claimed.context.get("memories", [])
        if item["kind"] == "monitor_knowledge"
    ]
    if len(knowledge) != 1:
        code = "monitor_knowledge_missing"
        raise ResearchError(code)
    update = await knowledge_merger(
        claimed.objective,
        knowledge[0]["content"],
        result,
        context=claimed.context,
    )
    return result, update


async def process_next(
    sessions: async_sessionmaker[AsyncSession],
    researcher: Researcher,
    knowledge_merger: KnowledgeMerger,
    alert_composer: AlertComposer | None = None,
    *,
    resume_alerts: bool = True,
) -> UUID | None:
    """Execute at most one queued check; failed checks are not auto-retried."""
    if alert_composer is not None and resume_alerts:
        alert_run_id = await process_next_alert(sessions, alert_composer)
        if alert_run_id is not None:
            return alert_run_id
    claimed = await claim_next(sessions)
    if claimed is None:
        return None
    logger.info("Researching monitor run {}", claimed.id)
    try:
        result, knowledge_update = await asyncio.wait_for(
            research_and_merge(claimed, researcher, knowledge_merger),
            timeout=RESEARCH_TIMEOUT_SECONDS,
        )
        await save_result(sessions, claimed, result, knowledge_update)
        if alert_composer is not None:
            await process_next_alert(
                sessions, alert_composer, run_id=claimed.id
            )
    except asyncio.CancelledError:
        await asyncio.shield(fail_run(sessions, claimed, "worker_interrupted"))
        raise
    except ResearchError as exc:
        await fail_run(sessions, claimed, str(exc))
        logger.warning("Monitor run {} failed: {}", claimed.id, str(exc))
    except Exception as exc:  # noqa: BLE001
        # Never log exception text: provider/DB errors can contain secrets.
        await fail_run(sessions, claimed, "research_execution_failed")
        logger.error(
            "Monitor run {} failed ({})", claimed.id, type(exc).__name__
        )
    return claimed.id
