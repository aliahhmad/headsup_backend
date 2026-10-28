"""Exercise queue execution, atomic evidence, and stale-input rejection."""

# ruff: noqa: PLR2004

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.commands.worker import FixtureResearch
from app.managers.auth import AuthManager
from app.managers.monitor import create_monitor, queue_manual_check
from app.managers.monitor_worker import claim_next, process_next, save_result
from app.managers.research import ResearchError
from app.models.monitor import (
    Memory,
    MemoryRevision,
    Monitor,
    MonitorRun,
    Observation,
    ObservationSource,
    utc_now,
)
from app.models.user import User
from app.schemas.request.monitor import MonitorCreate
from app.schemas.research import KnowledgeUpdate, ResearchResult

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def queued_run(test_db: AsyncSession) -> MonitorRun:
    """Persist a real owned monitor with its initial queued check."""
    user = User(
        email=f"{uuid4().hex}@example.test",
        password="unused-worker-test-password",  # noqa: S106
        first_name="Worker",
        last_name="Owner",
        verified=True,
        banned=False,
    )
    test_db.add(user)
    await test_db.flush()
    monitor = await create_monitor(
        MonitorCreate(
            name="Internships",
            objective="Find backend internships",
            monitor_context="Summer 2027 in New York",
        ),
        user.id,
        test_db,
    )
    await test_db.commit()
    return (
        await test_db.scalars(
            select(MonitorRun).where(MonitorRun.monitor_id == monitor.id)
        )
    ).one()


def findings() -> ResearchResult:
    """Two developments, with duplicate reports consolidated by persistence."""
    return ResearchResult.model_validate(
        {
            "observations": [
                {
                    "summary": "Company A opened internship applications.",
                    "sources": [
                        {
                            "source_url": "https://example.com/a",
                            "source_title": "A",
                        },
                        {
                            "source_url": "https://example.com/a",
                            "source_title": "A",
                        },
                        {"source_url": "https://example.com/report"},
                    ],
                },
                {
                    "summary": "Company B changed its application deadline.",
                    "sources": [{"source_url": "https://example.com/b"}],
                },
            ]
        }
    )


def knowledge_update() -> KnowledgeUpdate:
    """A full merged snapshot supplied by the isolated model mock."""
    return KnowledgeUpdate(
        content=(
            "Company A applications are open. Company B changed its deadline."
        ),
        change_reason="Incorporated supported internship developments.",
    )


@pytest.fixture
def knowledge_merger(mocker: MockerFixture) -> AsyncMock:
    """Keep worker persistence tests independent of paid model requests."""
    return mocker.AsyncMock(return_value=knowledge_update())


async def test_success_saves_findings_and_knowledge(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    client: AsyncClient,
) -> None:
    """Evidence is readable and accepted knowledge has run-linked history."""
    research = AsyncMock(return_value=findings())
    assert (
        await process_next(test_sessionmaker, research, knowledge_merger)
        == queued_run.id
    )
    await test_db.refresh(queued_run)
    assert queued_run.status == "succeeded"
    assert queued_run.attempt_count == 1
    assert queued_run.started_at is not None
    assert queued_run.completed_at is not None
    saved = list(await test_db.scalars(select(Observation)))
    assert len(saved) == 2
    assert await test_db.scalar(select(func.count(ObservationSource.id))) == 3
    for source in await test_db.scalars(select(ObservationSource)):
        assert source.retrieved_at.tzinfo is not None
        assert source.source_published_at is None
        assert source.evidence_excerpt is None
        assert source.content_hash is None
    memories = list(await test_db.scalars(select(Memory)))
    by_kind = {memory.kind: memory for memory in memories}
    assert by_kind["monitor_context"].version == 1
    assert by_kind["monitor_context"].content == "Summer 2027 in New York"
    knowledge = by_kind["monitor_knowledge"]
    assert knowledge.version == 2
    assert knowledge.content == knowledge_update().content
    revision = (
        await test_db.scalars(
            select(MemoryRevision).where(
                MemoryRevision.source_run_id == queued_run.id
            )
        )
    ).one()
    assert revision.memory_id == knowledge.id
    assert revision.version == 2
    assert revision.content == knowledge.content
    assert revision.change_reason == knowledge_update().change_reason
    assert revision.source_kind == "run"
    assert revision.source_message_id is None
    assert await test_db.scalar(select(func.count(MemoryRevision.id))) == 3
    research.assert_awaited_once()
    assert (
        await process_next(test_sessionmaker, research, knowledge_merger)
        is None
    )

    monitor = await test_db.get(Monitor, queued_run.monitor_id)
    owner = await test_db.get(User, monitor.user_id)
    headers = {"Authorization": f"Bearer {AuthManager.encode_token(owner)}"}
    response = await client.get(
        f"/monitors/{monitor.id}/runs/{queued_run.id}", headers=headers
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(response.json()["observations"]) == 2
    assert sum(len(o["sources"]) for o in response.json()["observations"]) == 3
    assert (
        await client.get(f"/monitors/{monitor.id}/runs/{queued_run.id}")
    ).status_code == 401


@pytest.mark.parametrize(
    "previous_knowledge", ["", "Earlier relevant finding."]
)
async def test_no_findings_is_successful(
    previous_knowledge: str,
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
) -> None:
    """An empty successful research result does not fabricate observations."""
    knowledge = (
        await test_db.scalars(
            select(Memory).where(Memory.kind == "monitor_knowledge")
        )
    ).one()
    knowledge.content = previous_knowledge
    initial_revision = (
        await test_db.scalars(
            select(MemoryRevision).where(
                MemoryRevision.memory_id == knowledge.id
            )
        )
    ).one()
    initial_revision.content = previous_knowledge
    await test_db.commit()
    updated_at = knowledge.updated_at
    research = AsyncMock(return_value=ResearchResult(observations=[]))
    await process_next(test_sessionmaker, research, knowledge_merger)
    await test_db.refresh(queued_run)
    await test_db.refresh(knowledge)
    assert queued_run.status == "succeeded"
    assert knowledge.content == previous_knowledge
    assert knowledge.version == 1
    assert knowledge.updated_at == updated_at
    assert await test_db.scalar(select(func.count(Observation.id))) == 0
    knowledge_merger.assert_not_awaited()
    assert await test_db.scalar(select(func.count(MemoryRevision.id))) == 2


async def test_offline_fixture_finishes_a_real_queued_run(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The documented fixture works through the queue-to-database pipeline."""
    fixture = Path(__file__).parents[2] / "docs/examples/research-result.json"
    provider = FixtureResearch(fixture)
    await process_next(test_sessionmaker, provider, provider.merge_knowledge)
    await test_db.refresh(queued_run)
    assert queued_run.status == "succeeded"
    observation = (await test_db.scalars(select(Observation))).one()
    assert "synthetic test data" in observation.summary
    source = (await test_db.scalars(select(ObservationSource))).one()
    assert source.observation_id == observation.id
    assert source.source_url == "https://example.com/careers/example-internship"
    knowledge = (
        await test_db.scalars(
            select(Memory).where(Memory.kind == "monitor_knowledge")
        )
    ).one()
    assert knowledge.content == provider.fixture.knowledge_update.content
    assert knowledge.version == 2


async def test_graceful_cancellation_fails_running_check(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """Graceful shutdown does not strand the claimed run or publish outputs."""
    with pytest.raises(asyncio.CancelledError):
        await process_next(
            test_sessionmaker,
            mocker.AsyncMock(side_effect=asyncio.CancelledError),
            knowledge_merger,
        )
    await test_db.refresh(queued_run)
    assert queued_run.status == "failed"
    assert queued_run.error_code == "worker_interrupted"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0


async def test_provider_failure_is_terminal_without_evidence(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """The next poll does not silently repeat a failed paid research request."""
    research = mocker.AsyncMock(side_effect=ResearchError("openai_http_429"))
    await process_next(test_sessionmaker, research, knowledge_merger)
    await test_db.refresh(queued_run)
    assert queued_run.status == "failed"
    assert queued_run.error_code == "openai_http_429"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0
    assert (
        await process_next(test_sessionmaker, research, knowledge_merger)
        is None
    )
    research.assert_awaited_once()


@pytest.mark.parametrize(
    "change", ["configuration", "memory", "archive", "delete"]
)
async def test_changes_during_research_discard_outputs(
    change: str,
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
) -> None:
    """Results cannot overwrite changes made while OpenAI was running."""

    async def research(
        objective: str, context: dict[str, Any]
    ) -> ResearchResult:
        async with test_sessionmaker() as session, session.begin():
            monitor = await session.get(Monitor, queued_run.monitor_id)
            if change == "configuration":
                monitor.objective = "A different objective"
                monitor.version += 1
            elif change == "memory":
                memory = (await session.scalars(select(Memory))).first()
                memory.content = "Updated while research was running"
                memory.version += 1
            else:
                monitor.status = "archived"
                monitor.next_run_at = None
                if change == "delete":
                    monitor.deleted_at = utc_now()
        return findings()

    await process_next(test_sessionmaker, research, knowledge_merger)
    await test_db.refresh(queued_run)
    assert queued_run.status == "cancelled"
    assert queued_run.error_code == "superseded_inputs"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0


async def test_source_failure_rolls_back_observation(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """Fail after an observation flush and prove the parent is rolled back."""
    mocker.patch(
        "app.managers.monitor_worker.ObservationSource",
        side_effect=RuntimeError("simulated source persistence failure"),
    )
    await process_next(
        test_sessionmaker,
        mocker.AsyncMock(return_value=findings()),
        knowledge_merger,
    )
    await test_db.refresh(queued_run)
    assert queued_run.status == "failed"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0
    assert await test_db.scalar(select(func.count(ObservationSource.id))) == 0


async def test_finalization_is_idempotent(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Duplicate completion cannot publish a second set of evidence records."""
    claimed = await claim_next(test_sessionmaker)
    assert claimed is not None
    assert await claim_next(test_sessionmaker) is None
    await save_result(
        test_sessionmaker, claimed, findings(), knowledge_update()
    )
    await save_result(
        test_sessionmaker, claimed, findings(), knowledge_update()
    )
    assert await test_db.scalar(select(func.count(Observation.id))) == 2
    assert await test_db.scalar(select(func.count(ObservationSource.id))) == 3
    assert await test_db.scalar(select(func.count(MemoryRevision.id))) == 3


@pytest.mark.parametrize("manual", [False, True])
async def test_paused_monitor_only_runs_manual_checks(
    *,
    manual: bool,
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
) -> None:
    """Respect paused state if a queued record escaped cancellation."""
    monitor = await test_db.get(Monitor, queued_run.monitor_id)
    monitor.status = "paused"
    monitor.next_run_at = None
    if manual:
        queued_run.trigger = "manual"
    await test_db.commit()
    research = AsyncMock(return_value=findings())
    await process_next(test_sessionmaker, research, knowledge_merger)
    await test_db.refresh(queued_run)
    assert queued_run.status == ("succeeded" if manual else "cancelled")
    assert research.await_count == int(manual)


async def test_concurrent_workers_claim_only_once(
    test_engine: AsyncEngine,
) -> None:
    """Separate PostgreSQL transactions cannot claim the same queued check."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as session:
        owner = User(
            email=f"{uuid4().hex}@example.test",
            password="unused-concurrency-password",  # noqa: S106
            first_name="Worker",
            last_name="Concurrency",
            verified=True,
        )
        session.add(owner)
        await session.flush()
        monitor = await create_monitor(
            MonitorCreate(name="Concurrent", objective="Find internships"),
            owner.id,
            session,
        )
        await session.commit()
    try:
        claims = await asyncio.gather(
            claim_next(sessions), claim_next(sessions)
        )
        assert sum(claim is not None for claim in claims) == 1
        async with sessions() as session:
            run = (await session.scalars(select(MonitorRun))).one()
            assert run.status == "running"
            assert run.attempt_count == 1
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(Monitor).where(Monitor.id == monitor.id)
            )
            await session.execute(delete(User).where(User.id == owner.id))


async def test_next_run_merges_previous_knowledge(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """Later checks receive accepted knowledge and preserve full revisions."""
    research = mocker.AsyncMock(return_value=findings())
    await process_next(test_sessionmaker, research, knowledge_merger)
    monitor = await test_db.get(Monitor, queued_run.monitor_id)
    second = await queue_manual_check(monitor, uuid4(), test_db)
    await test_db.commit()
    updated = KnowledgeUpdate(
        content=knowledge_update().content + " Company C is also hiring.",
        change_reason="Added Company C while retaining earlier findings.",
    )
    knowledge_merger.return_value = updated
    await process_next(test_sessionmaker, research, knowledge_merger)
    knowledge_merger.assert_awaited_with(
        monitor.objective, knowledge_update().content, findings()
    )
    research_context = research.await_args.args[1]
    assert any(
        item["kind"] == "monitor_knowledge"
        and item["content"] == knowledge_update().content
        and item["version"] == 2
        for item in research_context["memories"]
    )
    knowledge = (
        await test_db.scalars(
            select(Memory).where(Memory.kind == "monitor_knowledge")
        )
    ).one()
    assert knowledge.version == 3
    assert knowledge.content == updated.content
    revisions = list(
        await test_db.scalars(
            select(MemoryRevision)
            .where(MemoryRevision.memory_id == knowledge.id)
            .order_by(MemoryRevision.version)
        )
    )
    assert [r.content for r in revisions] == [
        "",
        knowledge_update().content,
        updated.content,
    ]
    assert revisions[-1].source_run_id == second.id
    await test_db.refresh(second)
    assert any(
        item["kind"] == "monitor_knowledge" and item["version"] == 2
        for item in second.context_snapshot["memories"]
    )


async def test_repeated_knowledge_does_not_create_revision(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """A no-change merge still saves new evidence without rewriting memory."""
    research = mocker.AsyncMock(return_value=findings())
    await process_next(test_sessionmaker, research, knowledge_merger)
    knowledge = (
        await test_db.scalars(
            select(Memory).where(Memory.kind == "monitor_knowledge")
        )
    ).one()
    updated_at = knowledge.updated_at
    monitor = await test_db.get(Monitor, queued_run.monitor_id)
    second = await queue_manual_check(monitor, uuid4(), test_db)
    await test_db.commit()
    await process_next(test_sessionmaker, research, knowledge_merger)
    await test_db.refresh(knowledge)
    await test_db.refresh(second)
    assert second.status == "succeeded"
    assert knowledge.version == 2
    assert knowledge.updated_at == updated_at
    assert await test_db.scalar(select(func.count(MemoryRevision.id))) == 3
    assert await test_db.scalar(select(func.count(Observation.id))) == 4


async def test_merge_failure_publishes_nothing(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """Knowledge is required for successful checks with findings."""
    knowledge_merger.side_effect = ResearchError(
        "openai_knowledge_invalid_response"
    )
    await process_next(
        test_sessionmaker,
        mocker.AsyncMock(return_value=findings()),
        knowledge_merger,
    )
    await test_db.refresh(queued_run)
    assert queued_run.status == "failed"
    assert queued_run.error_code == "openai_knowledge_invalid_response"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0
    assert await test_db.scalar(select(func.count(MemoryRevision.id))) == 2
    assert set(await test_db.scalars(select(Memory.version))) == {1}


async def test_revision_failure_rolls_back_evidence_and_memory(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """A revision failure rolls back evidence and current memory."""
    mocker.patch(
        "app.managers.monitor_worker.MemoryRevision",
        side_effect=RuntimeError("simulated revision failure"),
    )
    await process_next(
        test_sessionmaker,
        mocker.AsyncMock(return_value=findings()),
        knowledge_merger,
    )
    await test_db.refresh(queued_run)
    assert queued_run.status == "failed"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0
    assert await test_db.scalar(select(func.count(ObservationSource.id))) == 0
    assert await test_db.scalar(select(func.count(MemoryRevision.id))) == 2
    assert set(await test_db.scalars(select(Memory.version))) == {1}


async def test_memory_change_during_merge_discards_all_outputs(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    mocker: MockerFixture,
) -> None:
    """Final validation covers changes during the additional model request."""

    async def merge(
        objective: str, previous_knowledge: str, result: ResearchResult
    ) -> KnowledgeUpdate:
        async with test_sessionmaker() as session, session.begin():
            knowledge = (
                await session.scalars(
                    select(Memory).where(Memory.kind == "monitor_knowledge")
                )
            ).one()
            knowledge.content = "A newer accepted update"
            knowledge.version += 1
        return knowledge_update()

    await process_next(
        test_sessionmaker, mocker.AsyncMock(return_value=findings()), merge
    )
    await test_db.refresh(queued_run)
    assert queued_run.status == "cancelled"
    assert queued_run.error_code == "superseded_inputs"
    assert await test_db.scalar(select(func.count(Observation.id))) == 0
    assert await test_db.scalar(select(func.count(MemoryRevision.id))) == 2
    assert (
        await test_db.scalars(
            select(Memory.content).where(Memory.kind == "monitor_knowledge")
        )
    ).one() == "A newer accepted update"


async def test_other_memories_are_untouched(
    queued_run: MonitorRun,
    test_db: AsyncSession,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_merger: AsyncMock,
    mocker: MockerFixture,
) -> None:
    """Knowledge writes cannot alter profile, context, or another monitor."""
    monitor = await test_db.get(Monitor, queued_run.monitor_id)
    other = await create_monitor(
        MonitorCreate(name="Other", objective="Other objective"),
        monitor.user_id,
        test_db,
    )
    profile = Memory(
        user_id=monitor.user_id,
        kind="user_profile",
        content="Profile unchanged",
    )
    test_db.add(profile)
    await test_db.commit()
    await process_next(
        test_sessionmaker,
        mocker.AsyncMock(return_value=findings()),
        knowledge_merger,
    )
    await test_db.refresh(profile)
    assert profile.content == "Profile unchanged"
    assert profile.version == 1
    others = list(
        await test_db.scalars(
            select(Memory).where(Memory.monitor_id == other.id)
        )
    )
    assert {m.version for m in others} == {1}
    assert {m.content for m in others} == {""}
    assert (
        await test_db.scalar(
            select(Memory.content).where(
                Memory.monitor_id == monitor.id,
                Memory.kind == "monitor_context",
            )
        )
        == "Summer 2027 in New York"
    )
