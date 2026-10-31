"""Durable decisions and recoverable digest publication."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select

from app.logs import logger
from app.managers.monitor_state import can_execute, memory_snapshot
from app.managers.research import ResearchError
from app.models.monitor import (
    Alert,
    AlertCandidate,
    AlertJob,
    AlertObservation,
    Monitor,
    MonitorRun,
    Observation,
    ObservationSource,
    utc_now,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.schemas.research import (
        AlertComposition,
        KnowledgeUpdate,
    )

COMPOSITION_TIMEOUT_SECONDS = 150
LEASE_SECONDS = 180
MAX_ATTEMPTS = 3


class AlertComposer(Protocol):
    """Allow offline and live composition through the same durable worker."""

    async def __call__(
        self,
        objective: str,
        candidates: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> AlertComposition:
        """Select supplied findings and return a digest or negative decision."""
        ...


def canonical_key(value: str) -> str:
    """Normalize spelling before matching development identities."""
    return " ".join(value.casefold().split())


async def alert_history(
    monitor_id: UUID, session: AsyncSession
) -> dict[str, Any]:
    """Supply this monitor's evidence and actual publication history only."""
    findings = list(
        await session.scalars(
            select(Observation)
            .join(MonitorRun)
            .where(MonitorRun.monitor_id == monitor_id)
            .order_by(Observation.created_at, Observation.id)
        )
    )
    sources = await session.scalars(
        select(ObservationSource)
        .join(Observation)
        .join(MonitorRun)
        .where(MonitorRun.monitor_id == monitor_id)
    )
    by_finding: dict[UUID, list[dict[str, Any]]] = {}
    for source in sources:
        by_finding.setdefault(source.observation_id, []).append(
            {
                "source_url": source.source_url,
                "source_title": source.source_title,
            }
        )
    alerts = await session.scalars(
        select(Alert)
        .where(Alert.monitor_id == monitor_id)
        .order_by(Alert.created_at, Alert.id)
    )
    links = await session.scalars(
        select(AlertObservation).where(
            AlertObservation.monitor_id == monitor_id
        )
    )
    keys: dict[UUID, list[str]] = {}
    for link in links:
        keys.setdefault(link.alert_id, []).append(
            canonical_key(link.development_key)
        )
    return {
        "observations": [
            {
                "id": str(finding.id),
                "summary": finding.summary,
                "sources": by_finding.get(finding.id, []),
            }
            for finding in findings
        ],
        "published_alerts": [
            {
                "title": alert.title,
                "body": alert.body,
                "created_at": alert.created_at.isoformat(),
                "development_keys": keys.get(alert.id, []),
            }
            for alert in alerts
        ],
    }


async def published_keys(monitor_id: UUID, session: AsyncSession) -> set[str]:
    """Read the ledger of published development identities."""
    return {
        canonical_key(key)
        for key in await session.scalars(
            select(AlertObservation.development_key).where(
                AlertObservation.monitor_id == monitor_id
            )
        )
    }


async def persist_decisions(
    session: AsyncSession,
    monitor: Monitor,
    run: MonitorRun,
    update: KnowledgeUpdate | None,
    references: dict[str, Observation],
) -> None:
    """Save decisions and only schedule composition for eligible positives."""
    decisions = update.alert_decisions if update else []
    refs = [decision.observation_ref for decision in decisions]
    if len(set(refs)) != len(refs) or not set(refs).issubset(references):
        code = "invalid_alert_decisions"
        raise ResearchError(code)
    used_keys = await published_keys(monitor.id, session)
    used_findings: set[UUID] = set()
    eligible = 0
    for decision in decisions:
        finding = references[decision.observation_ref]
        key = (
            canonical_key(decision.development_key)
            if decision.development_key
            else None
        )
        disposition = "rejected"
        if decision.should_alert:
            disposition = "duplicate"
            if key not in used_keys and finding.id not in used_findings:
                disposition = "pending"
                eligible += 1
                if key is not None:
                    used_keys.add(key)
                used_findings.add(finding.id)
        session.add(
            AlertCandidate(
                monitor_id=monitor.id,
                monitor_run_id=run.id,
                observation_id=finding.id,
                observation_ref=decision.observation_ref,
                should_alert=decision.should_alert,
                what_changed=decision.what_changed,
                why_it_matters=decision.why_it_matters,
                development_key=key,
                status=disposition,
            )
        )
    reason = None
    if not eligible:
        reason = (
            "no_alert_decisions" if not decisions else "all_decisions_false"
        )
        if any(decision.should_alert for decision in decisions):
            reason = "already_published"
    session.add(
        AlertJob(
            monitor_id=monitor.id,
            monitor_run_id=run.id,
            monitor_version=monitor.version,
            status="pending" if eligible else "skipped",
            input_snapshot=await memory_snapshot(monitor, session),
            reason_code=reason,
            completed_at=None if eligible else utc_now(),
        )
    )


@dataclass(frozen=True)
class ClaimedAlert:
    """Detached inputs and fencing token for one composition attempt."""

    job_id: UUID
    monitor_id: UUID
    run_id: UUID
    token: UUID
    objective: str
    context: dict[str, Any]
    candidates: list[dict[str, Any]]


def finish_job(job: AlertJob, status: str, reason: str) -> None:
    """Release the claim and record a terminal outcome."""
    job.status = status
    job.reason_code = reason
    job.completed_at = utc_now()
    job.lease_token = None
    job.lease_expires_at = None
    job.error_code = None


async def eligible_candidates(
    job: AlertJob, session: AsyncSession
) -> list[AlertCandidate]:
    """Exclude developments another attempt already published under a lock."""
    candidates = list(
        await session.scalars(
            select(AlertCandidate)
            .where(
                AlertCandidate.monitor_run_id == job.monitor_run_id,
                AlertCandidate.status == "pending",
                AlertCandidate.should_alert.is_(True),
            )
            .order_by(AlertCandidate.created_at, AlertCandidate.observation_ref)
        )
    )
    known = await published_keys(job.monitor_id, session)
    eligible = []
    for candidate in candidates:
        if candidate.development_key in known:
            candidate.status = "duplicate"
        else:
            eligible.append(candidate)
    return eligible


async def job_is_current(
    job: AlertJob, monitor: Monitor, run: MonitorRun, session: AsyncSession
) -> bool:
    """Validate accepted post-merge versions, including the run's own update."""
    return (
        run.status == "succeeded"
        and can_execute(monitor, run)
        and monitor.version == job.monitor_version
        and await memory_snapshot(monitor, session) == job.input_snapshot
    )


async def ready_to_claim(
    job: AlertJob, monitor: Monitor, run: MonitorRun, session: AsyncSession
) -> bool:
    """Recheck a refreshed job before starting or recovering an attempt."""
    now = utc_now()
    if (
        job.status not in {"pending", "processing"}
        or (
            job.status == "processing"
            and job.lease_expires_at is not None
            and job.lease_expires_at > now
        )
        or (job.status == "pending" and job.next_attempt_at > now)
    ):
        return False
    if job.attempt_count >= MAX_ATTEMPTS:
        finish_job(job, "failed", "attempts_exhausted")
        job.error_code = "alert_attempts_exhausted"
        return False
    if not await job_is_current(job, monitor, run, session):
        finish_job(job, "cancelled", "superseded_inputs")
        return False
    return True


def requeue_changed_candidates(job: AlertJob, current_refs: set[str]) -> None:
    """Never publish prose composed for a candidate set that has changed."""
    if not current_refs:
        finish_job(job, "skipped", "already_published")
    else:
        job.status = "pending"
        job.lease_token = None
        job.lease_expires_at = None
        job.next_attempt_at = utc_now()
        job.reason_code = "candidates_changed"


async def claim_alert(
    sessions: async_sessionmaker[AsyncSession], *, run_id: UUID | None = None
) -> ClaimedAlert | None:
    """Claim due/abandoned work under parent-first locks and a bounded lease."""
    async with sessions() as session, session.begin():
        now = utc_now()
        query = (
            select(AlertJob, Monitor, MonitorRun)
            .join(Monitor, Monitor.id == AlertJob.monitor_id)
            .join(MonitorRun, MonitorRun.id == AlertJob.monitor_run_id)
            .where(
                or_(
                    and_(
                        AlertJob.status == "pending",
                        AlertJob.next_attempt_at <= now,
                    ),
                    and_(
                        AlertJob.status == "processing",
                        AlertJob.lease_expires_at <= now,
                    ),
                )
            )
        )
        if run_id is not None:
            query = query.where(AlertJob.monitor_run_id == run_id)
        row = (
            await session.execute(
                query.order_by(AlertJob.created_at, AlertJob.id)
                .limit(1)
                .with_for_update(of=Monitor, skip_locked=True)
            )
        ).first()
        if row is None:
            return None
        job, monitor, run = row
        await session.refresh(job, with_for_update=True)
        if not await ready_to_claim(job, monitor, run, session):
            return None
        candidates = await eligible_candidates(job, session)
        if not candidates:
            finish_job(job, "skipped", "no_eligible_candidates")
            return None
        payload = []
        for candidate in candidates:
            finding = await session.get(Observation, candidate.observation_id)
            sources = list(
                await session.scalars(
                    select(ObservationSource).where(
                        ObservationSource.observation_id
                        == candidate.observation_id
                    )
                )
            )
            if finding is None or not sources:
                finish_job(job, "failed", "missing_evidence")
                return None
            payload.append(
                {
                    "observation_ref": candidate.observation_ref,
                    "development_key": candidate.development_key,
                    "what_changed": candidate.what_changed,
                    "why_it_matters": candidate.why_it_matters,
                    "summary": finding.summary,
                    "sources": [
                        {
                            "source_url": source.source_url,
                            "source_title": source.source_title,
                        }
                        for source in sources
                    ],
                }
            )
        job.status = "processing"
        job.attempt_count += 1
        job.lease_token = uuid4()
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        job.error_code = None
        return ClaimedAlert(
            job.id,
            monitor.id,
            run.id,
            job.lease_token,
            run.objective_snapshot,
            job.input_snapshot,
            payload,
        )


async def save_composition(
    sessions: async_sessionmaker[AsyncSession],
    claimed: ClaimedAlert,
    result: AlertComposition,
) -> None:
    """Fence old attempts and atomically publish the digest."""
    async with sessions() as session, session.begin():
        monitor = await session.get(
            Monitor, claimed.monitor_id, with_for_update=True
        )
        job = await session.get(AlertJob, claimed.job_id, with_for_update=True)
        run = await session.get(MonitorRun, claimed.run_id)
        if not monitor or not job or not run:
            return
        if job.status != "processing" or job.lease_token != claimed.token:
            return
        if not await job_is_current(job, monitor, run, session):
            finish_job(job, "cancelled", "superseded_inputs")
            return
        eligible = await eligible_candidates(job, session)
        current_refs = {candidate.observation_ref for candidate in eligible}
        expected_refs = {
            candidate["observation_ref"] for candidate in claimed.candidates
        }
        if current_refs != expected_refs:
            requeue_changed_candidates(job, current_refs)
            return
        selected = set(result.observation_refs)
        if not selected.issubset(current_refs):
            msg = "openai_alert_invalid_reference"
            raise ResearchError(msg)
        if not result.should_alert:
            for candidate in eligible:
                candidate.status = "excluded"
            finish_job(job, "skipped", "update_not_warranted")
            # Save the non-sensitive selection reason with the job, not in logs.
            job.input_snapshot = {
                **job.input_snapshot,
                "decision_reason": result.reason,
            }
            return
        alert = Alert(
            monitor_id=monitor.id,
            monitor_run_id=run.id,
            title=result.title,
            body=result.body,
        )
        session.add(alert)
        await session.flush()
        for candidate in eligible:
            candidate.status = (
                "published"
                if candidate.observation_ref in selected
                else "excluded"
            )
            if candidate.status == "published":
                session.add(
                    AlertObservation(
                        alert_id=alert.id,
                        monitor_id=monitor.id,
                        monitor_run_id=run.id,
                        observation_id=candidate.observation_id,
                        development_key=candidate.development_key,
                    )
                )
        await session.flush()
        finish_job(job, "published", "digest_published")
        job.input_snapshot = {
            **job.input_snapshot,
            "decision_reason": result.reason,
        }


async def fail_composition(
    sessions: async_sessionmaker[AsyncSession], claimed: ClaimedAlert, code: str
) -> None:
    """Retry composition alone, preserving evidence and accepted knowledge."""
    async with sessions() as session, session.begin():
        # Match the publication lock order to avoid deadlocks with lifecycle
        # edits.
        await session.get(Monitor, claimed.monitor_id, with_for_update=True)
        job = await session.get(AlertJob, claimed.job_id, with_for_update=True)
        if (
            not job
            or job.status != "processing"
            or job.lease_token != claimed.token
        ):
            return
        job.status = "pending" if job.attempt_count < MAX_ATTEMPTS else "failed"
        job.next_attempt_at = utc_now() + timedelta(
            seconds=5 * 3 ** (job.attempt_count - 1)
        )
        job.error_code = code
        job.reason_code = "composition_failed"
        job.completed_at = utc_now() if job.status == "failed" else None
        job.lease_token = None
        job.lease_expires_at = None


async def process_next_alert(
    sessions: async_sessionmaker[AsyncSession],
    composer: AlertComposer,
    *,
    run_id: UUID | None = None,
) -> UUID | None:
    """Execute one due composition attempt with positive candidates."""
    claimed = await claim_alert(sessions, run_id=run_id)
    if claimed is None:
        return None
    try:
        result = await asyncio.wait_for(
            composer(claimed.objective, claimed.candidates, claimed.context),
            timeout=COMPOSITION_TIMEOUT_SECONDS,
        )
        await save_composition(sessions, claimed, result)
    except asyncio.CancelledError:
        await asyncio.shield(
            fail_composition(sessions, claimed, "worker_interrupted")
        )
        raise
    except ResearchError as exc:
        await fail_composition(sessions, claimed, str(exc))
    except Exception as exc:  # noqa: BLE001
        await fail_composition(sessions, claimed, "alert_execution_failed")
        logger.error("Alert composition failed ({})", type(exc).__name__)
    return claimed.run_id
