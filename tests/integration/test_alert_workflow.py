"""Exercise quiet runs, durable retries, deduplication and owned alert APIs."""

# ruff: noqa: PLR2004

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.managers.alert import claim_alert, process_next_alert, save_composition
from app.managers.auth import AuthManager
from app.managers.monitor import create_monitor
from app.managers.monitor_worker import process_next
from app.managers.research import ResearchError
from app.models.monitor import (
    Alert,
    AlertCandidate,
    AlertJob,
    AlertObservation,
    Memory,
    MemoryRevision,
    Monitor,
    MonitorRun,
    Observation,
    utc_now,
)
from app.models.user import User
from app.schemas.request.monitor import MonitorCreate
from app.schemas.research import (
    AlertComposition,
    KnowledgeUpdate,
    ResearchResult,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def findings() -> ResearchResult:
    """Two distinct sourced developments from one research run."""
    return ResearchResult.model_validate(
        {
            "observations": [
                {
                    "summary": f"Development {index}",
                    "sources": [{"source_url": f"https://example.com/{index}"}],
                }
                for index in (1, 2)
            ]
        }
    )


def assessment(
    flags=(True, True), keys=("event-a", "event-b")
) -> KnowledgeUpdate:
    """Return compact decisions in addition to a full knowledge snapshot."""
    return KnowledgeUpdate.model_validate(
        {
            "content": "Accepted knowledge with two developments.",
            "change_reason": "Merged supported findings.",
            "alert_decisions": [
                {
                    "observation_ref": f"finding_{index}",
                    "should_alert": flag,
                    "what_changed": f"Development {index}",
                    "why_it_matters": "Relevant actionable change.",
                    "development_key": key if flag else None,
                }
                for index, (flag, key) in enumerate(
                    zip(flags, keys, strict=True), start=1
                )
            ],
        }
    )


def digest(refs=("finding_1", "finding_2")) -> AlertComposition:
    """A positive composition referring exclusively to supplied candidates."""
    return AlertComposition(
        should_alert=True,
        reason="Actionable updates.",
        title="Two developments",
        body="Here are your sourced changes.",
        observation_refs=list(refs),
    )


@pytest_asyncio.fixture
async def monitor(test_db) -> Monitor:
    """Create an owned monitor and a real initial queued run."""
    owner = User(
        email=f"{uuid4().hex}@example.test",
        password="unused-password",  # noqa: S106
        first_name="Alert",
        last_name="Owner",
        verified=True,
        banned=False,
    )
    test_db.add(owner)
    await test_db.flush()
    monitor = await create_monitor(
        MonitorCreate(name="Alerts", objective="Track material developments"),
        owner.id,
        test_db,
    )
    await test_db.commit()
    return monitor


async def next_run(test_db, monitor) -> MonitorRun:
    """Queue a subsequent run without depending on manual HTTP cooldowns."""
    run = MonitorRun(
        monitor_id=monitor.id,
        trigger="manual",
        idempotency_key=uuid4().hex,
        monitor_version=monitor.version,
        objective_snapshot=monitor.objective,
        context_snapshot={},
    )
    test_db.add(run)
    await test_db.commit()
    return run


@pytest.mark.parametrize("flags", [(), (False, False)])
async def test_no_positive_decisions_never_calls_composer(
    monitor, test_db, test_sessionmaker, flags
) -> None:
    """Empty/all-false decisions save research but create no pending work."""
    update = assessment(flags, ("event-a", "event-b") if flags else ())
    composer = AsyncMock()
    run_id = await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=update),
        composer,
    )
    composer.assert_not_awaited()
    run = await test_db.get(MonitorRun, run_id)
    assert run.status == "succeeded"
    job = await test_db.scalar(select(AlertJob))
    assert job.status == "skipped"
    assert job.attempt_count == 0
    assert job.reason_code == (
        "all_decisions_false" if flags else "no_alert_decisions"
    )
    assert await test_db.scalar(select(func.count(Alert.id))) == 0
    assert await test_db.scalar(select(func.count(Observation.id))) == 2
    knowledge = await test_db.scalar(
        select(Memory).where(Memory.kind == "monitor_knowledge")
    )
    assert knowledge.content == update.content
    assert knowledge.version == 2


async def test_multiple_developments_publish_one_digest(
    monitor, test_db, test_sessionmaker
) -> None:
    """Both pieces of evidence belong to one published alert."""
    composer = AsyncMock(return_value=digest())
    run_id = await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
        composer,
    )
    composer.assert_awaited_once()
    alert = (await test_db.scalars(select(Alert))).one()
    assert alert.monitor_run_id == run_id
    assert await test_db.scalar(select(func.count(AlertObservation.id))) == 2
    assert (await test_db.scalar(select(AlertJob))).status == "published"
    assert set(await test_db.scalars(select(AlertCandidate.status))) == {
        "published"
    }


async def test_duplicates_removed_before_composition(
    monitor, test_db, test_sessionmaker
) -> None:
    """A/B yesterday and B/C today publishes only C; all duplicates skip."""
    research = AsyncMock(return_value=findings())
    await process_next(
        test_sessionmaker,
        research,
        AsyncMock(return_value=assessment()),
        AsyncMock(return_value=digest()),
    )
    run = await next_run(test_db, monitor)
    composer = AsyncMock(return_value=digest(("finding_2",)))
    await process_next(
        test_sessionmaker,
        research,
        AsyncMock(return_value=assessment(keys=("EVENT-B", "event-c"))),
        composer,
    )
    assert [
        item["observation_ref"] for item in composer.await_args.args[1]
    ] == ["finding_2"]
    assert await test_db.scalar(select(func.count(Alert.id))) == 2
    assert set(
        await test_db.scalars(select(AlertObservation.development_key))
    ) == {"event-a", "event-b", "event-c"}
    assert (
        await test_db.scalar(
            select(AlertJob).where(AlertJob.monitor_run_id == run.id)
        )
    ).status == "published"
    await next_run(test_db, monitor)
    no_call = AsyncMock()
    await process_next(
        test_sessionmaker,
        research,
        AsyncMock(return_value=assessment()),
        no_call,
    )
    no_call.assert_not_awaited()


async def test_final_negative_decision_does_not_mark_notified(
    monitor, test_db, test_sessionmaker
) -> None:
    """Keep rejected candidates out of the publication ledger."""
    composer = AsyncMock(
        return_value=AlertComposition(
            should_alert=False,
            reason="Background only.",
            title=None,
            body=None,
            observation_refs=[],
        )
    )
    await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
        composer,
    )
    assert await test_db.scalar(select(func.count(Alert.id))) == 0
    assert await test_db.scalar(select(func.count(AlertObservation.id))) == 0
    assert set(await test_db.scalars(select(AlertCandidate.status))) == {
        "excluded"
    }
    assert (
        await test_db.scalar(select(AlertJob))
    ).reason_code == "update_not_warranted"


async def test_retry_does_not_repeat_research_or_memory_update(
    monitor, test_db, test_sessionmaker
) -> None:
    """Retry composition without repeating research."""
    research = AsyncMock(return_value=findings())
    merger = AsyncMock(return_value=assessment())
    composer = AsyncMock(side_effect=ResearchError("openai_timeout"))
    run_id = await process_next(test_sessionmaker, research, merger, composer)
    job = await test_db.scalar(select(AlertJob))
    assert job.status == "pending"
    assert job.error_code == "openai_timeout"
    assert (await test_db.get(MonitorRun, run_id)).status == "succeeded"
    revisions = await test_db.scalar(select(func.count(MemoryRevision.id)))
    job.next_attempt_at = utc_now() - timedelta(seconds=1)
    await test_db.commit()
    composer.side_effect = None
    composer.return_value = digest()
    await process_next(test_sessionmaker, research, merger, composer)
    await test_db.refresh(job)
    assert job.status == "published"
    assert job.attempt_count == 2
    research.assert_awaited_once()
    merger.assert_awaited_once()
    assert (
        await test_db.scalar(select(func.count(MemoryRevision.id))) == revisions
    )


async def test_expired_lease_fences_old_completion(
    monitor, test_db, test_sessionmaker
) -> None:
    """Fence late original output after recovery claims a new attempt."""
    await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
    )
    original = await claim_alert(test_sessionmaker)
    assert original is not None
    assert await claim_alert(test_sessionmaker) is None
    job = await test_db.get(AlertJob, original.job_id)
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    await test_db.commit()
    replacement = await claim_alert(test_sessionmaker)
    assert replacement is not None
    assert replacement.token != original.token
    await save_composition(test_sessionmaker, original, digest())
    assert await test_db.scalar(select(func.count(Alert.id))) == 0
    await save_composition(test_sessionmaker, replacement, digest())
    await save_composition(test_sessionmaker, replacement, digest())
    assert await test_db.scalar(select(func.count(Alert.id))) == 1


@pytest.mark.parametrize(
    "change", ["archive", "delete", "configuration", "memory"]
)
async def test_changes_during_composition_cancel_publication(
    monitor, test_db, test_sessionmaker, change
) -> None:
    """Prevent stale publication without losing research."""

    async def compose(objective, candidates, context) -> AlertComposition:
        async with test_sessionmaker() as session, session.begin():
            parent = await session.get(Monitor, monitor.id)
            if change == "configuration":
                parent.version += 1
            elif change == "memory":
                memory = await session.scalar(
                    select(Memory).where(Memory.kind == "monitor_context")
                )
                memory.version += 1
                memory.content = "Changed context"
            else:
                parent.status = "archived"
                parent.next_run_at = None
                if change == "delete":
                    parent.deleted_at = utc_now()
        return digest()

    run_id = await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
        compose,
    )
    assert (await test_db.get(MonitorRun, run_id)).status == "succeeded"
    assert (await test_db.scalar(select(AlertJob))).status == "cancelled"
    assert await test_db.scalar(select(func.count(Alert.id))) == 0


async def test_unknown_decision_rolls_back_research(
    monitor, test_db, test_sessionmaker
) -> None:
    """Invented finding IDs are failures, not negative alert decisions."""
    update = assessment()
    update.alert_decisions[0].observation_ref = "invented"
    composer = AsyncMock()
    run_id = await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=update),
        composer,
    )
    assert (await test_db.get(MonitorRun, run_id)).status == "failed"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0
    composer.assert_not_awaited()


async def test_pending_composition_blocks_overlapping_research(
    monitor, test_db, test_sessionmaker
) -> None:
    """Wait for pending composition before advancing research."""
    await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
    )
    queued = await next_run(test_db, monitor)
    researcher = AsyncMock()
    assert (
        await process_next(test_sessionmaker, researcher, AsyncMock()) is None
    )
    researcher.assert_not_awaited()
    await test_db.refresh(queued)
    assert queued.status == "queued"


async def test_owned_alert_api_and_state(
    monitor, test_db, test_sessionmaker, client
) -> None:
    """Check owned evidence, idempotent state, and private responses."""
    run_id = await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
        AsyncMock(return_value=digest()),
    )
    owner = await test_db.get(User, monitor.user_id)
    headers = {"Authorization": f"Bearer {AuthManager.encode_token(owner)}"}
    base = f"/monitors/{monitor.id}/alerts"
    response = await client.get(base, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    alert_id = response.json()["items"][0]["id"]
    detail = await client.get(f"{base}/{alert_id}", headers=headers)
    assert len(detail.json()["observations"]) == 2
    assert all(item["sources"] for item in detail.json()["observations"])
    assert (await client.get(f"{base}/counts", headers=headers)).json() == {
        "unread": 1
    }
    state = await client.patch(
        f"{base}/{alert_id}", headers=headers, json={"read": True}
    )
    repeated = await client.patch(
        f"{base}/{alert_id}", headers=headers, json={"read": True}
    )
    assert state.json()["read_at"] == repeated.json()["read_at"]
    assert (await client.get(f"{base}/counts", headers=headers)).json() == {
        "unread": 0
    }
    assert (
        await client.patch(
            f"{base}/{alert_id}", headers=headers, json={"title": "Changed"}
        )
    ).status_code == 422
    await client.patch(
        f"{base}/{alert_id}", headers=headers, json={"dismissed": True}
    )
    assert (await client.get(base, headers=headers)).json()["items"] == []
    assert (await client.get(base)).status_code == 401
    assert (
        await client.get(
            f"/monitors/{uuid4()}/alerts/{alert_id}", headers=headers
        )
    ).status_code == 404
    run = (
        await client.get(
            f"/monitors/{monitor.id}/runs/{run_id}", headers=headers
        )
    ).json()
    assert run["alert_processing"]["status"] == "published"
    assert run["alert"]["id"] == alert_id


async def test_actual_cross_user_alert_access_is_rejected(
    monitor, test_db, test_sessionmaker, client
) -> None:
    """Reject another user's reads and state changes."""
    await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
        AsyncMock(return_value=digest()),
    )
    alert = await test_db.scalar(select(Alert))
    stranger = User(
        email=f"{uuid4().hex}@example.test",
        password="unused-password",  # noqa: S106
        first_name="Other",
        last_name="Owner",
        verified=True,
        banned=False,
    )
    test_db.add(stranger)
    await test_db.commit()
    headers = {"Authorization": f"Bearer {AuthManager.encode_token(stranger)}"}
    base = f"/monitors/{monitor.id}/alerts"
    for path in (base, f"{base}/counts", f"{base}/{alert.id}"):
        assert (await client.get(path, headers=headers)).status_code == 404
    assert (
        await client.patch(
            f"{base}/{alert.id}", headers=headers, json={"read": True}
        )
    ).status_code == 404


async def test_publication_failure_rolls_back_digest_and_ledger(
    monitor, test_db, test_sessionmaker
) -> None:
    """Link-insertion failure cannot leave a digest without evidence."""

    def fail_link(mapper, connection, target) -> None:
        message = "Simulated link failure"
        raise RuntimeError(message)

    event.listen(AlertObservation, "before_insert", fail_link)
    try:
        run_id = await process_next(
            test_sessionmaker,
            AsyncMock(return_value=findings()),
            AsyncMock(return_value=assessment()),
            AsyncMock(return_value=digest()),
        )
    finally:
        event.remove(AlertObservation, "before_insert", fail_link)
    assert (await test_db.get(MonitorRun, run_id)).status == "succeeded"
    assert (await test_db.scalar(select(AlertJob))).status == "pending"
    assert await test_db.scalar(select(func.count(Alert.id))) == 0
    assert await test_db.scalar(select(func.count(AlertObservation.id))) == 0
    assert set(await test_db.scalars(select(AlertCandidate.status))) == {
        "pending"
    }


async def test_exhausted_composition_is_visible_without_research_failure(
    monitor, test_db, test_sessionmaker
) -> None:
    """Bound retries and retain a distinct failure state with saved research."""
    researcher = AsyncMock(return_value=findings())
    composer = AsyncMock(side_effect=ResearchError("openai_http_429"))
    merger = AsyncMock(return_value=assessment())
    run_id = await process_next(test_sessionmaker, researcher, merger, composer)
    job = await test_db.scalar(select(AlertJob))
    for _ in range(2):
        job.next_attempt_at = utc_now() - timedelta(seconds=1)
        await test_db.commit()
        await process_next(test_sessionmaker, researcher, merger, composer)
        await test_db.refresh(job)
    assert job.status == "failed"
    assert job.attempt_count == 3
    assert job.error_code == "openai_http_429"
    assert (await test_db.get(MonitorRun, run_id)).status == "succeeded"
    researcher.assert_awaited_once()
    assert await test_db.scalar(select(func.count(Alert.id))) == 0


async def test_two_workers_claim_and_publish_only_once(test_engine) -> None:
    """Independent PostgreSQL transactions cannot publish the same job twice."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = User(
            email=f"{uuid4().hex}@example.test",
            password="unused-password",  # noqa: S106
            first_name="Concurrent",
            last_name="Owner",
            verified=True,
        )
        session.add(owner)
        await session.flush()
        parent = await create_monitor(
            MonitorCreate(
                name="Concurrent alerts", objective="Track material changes"
            ),
            owner.id,
            session,
        )
        await session.commit()
    try:
        await process_next(
            sessions,
            AsyncMock(return_value=findings()),
            AsyncMock(return_value=assessment()),
        )
        composer = AsyncMock(return_value=digest())
        await asyncio.gather(
            process_next_alert(sessions, composer),
            process_next_alert(sessions, composer),
        )
        composer.assert_awaited_once()
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count(Alert.id)).where(
                        Alert.monitor_id == parent.id
                    )
                )
                == 1
            )
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(Monitor).where(Monitor.id == parent.id)
            )
            await session.execute(delete(User).where(User.id == owner.id))


async def test_fixture_execution_cannot_resume_unrelated_pending_jobs(
    monitor, test_db, test_sessionmaker
) -> None:
    """Do not apply offline fixtures to unrelated live composition jobs."""
    await process_next(
        test_sessionmaker,
        AsyncMock(return_value=findings()),
        AsyncMock(return_value=assessment()),
    )
    composer = AsyncMock()
    assert (
        await process_next(
            test_sessionmaker,
            AsyncMock(),
            AsyncMock(),
            composer,
            resume_alerts=False,
        )
        is None
    )
    composer.assert_not_awaited()
    assert (await test_db.scalar(select(AlertJob))).status == "pending"
