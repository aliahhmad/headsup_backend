"""Exercise durable queue controls, real single steps and dispatcher exclusion."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.managers import queue_control as queue
from app.managers.auth import AuthManager
from app.managers.monitor import create_monitor
from app.managers.research import ResearchError
from app.models.enums import RoleType
from app.models.monitor import AlertJob, MonitorRun, utc_now
from app.models.queue import QueueControl, QueueExecution
from app.models.user import User
from app.schemas.request.monitor import MonitorCreate
from app.schemas.research import (
    AlertComposition,
    KnowledgeUpdate,
    ResearchResult,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def queue_data(test_db, monkeypatch):
    """Create isolated accounts/runs; provider mocks never make paid calls."""
    monkeypatch.setattr(queue, "provider_configured", lambda: True)
    # Different tests can execute in parallel, but contenders within a test
    # always share the same real PostgreSQL advisory lock.
    monkeypatch.setattr(queue, "DISPATCH_LOCK", uuid4().int % (2**62))
    accounts = []
    for role in (RoleType.admin, RoleType.user):
        user = User(
            email=f"{uuid4().hex}@example.test",
            password="NOT-A-REAL-PASSWORD",
            first_name="Queue",
            last_name="Owner",
            role=role,
            verified=True,
            banned=False,
        )
        test_db.add(user)
        accounts.append(user)
    await test_db.flush()
    admin, owner = accounts
    monitors = []
    for index in range(2):
        monitor = await create_monitor(
            MonitorCreate(
                name=f"Queue check {index}",
                objective="Watch meaningful developments",
            ),
            owner.id,
            test_db,
        )
        monitors.append(monitor)
    await test_db.commit()
    return {
        "admin": admin,
        "owner": owner,
        "monitors": monitors,
        "headers": {
            "Authorization": f"Bearer {AuthManager.encode_token(admin)}"
        },
    }


def empty_provider():
    """An empty research result exercises real persistence without a model."""
    return AsyncMock(return_value=ResearchResult(observations=[])), AsyncMock()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/admin/queue", None),
        ("PATCH", "/admin/queue/settings", {"automatic_enabled": True}),
        ("POST", "/admin/queue/run-next", None),
    ],
)
async def test_queue_endpoints_require_admin(
    queue_data, client, method, path, body
):
    """Ordinary accounts cannot observe or trigger cross-account paid work."""
    assert (await client.request(method, path, json=body)).status_code == 401
    headers = {
        "Authorization": f"Bearer {AuthManager.encode_token(queue_data['owner'])}"
    }
    assert (
        await client.request(method, path, json=body, headers=headers)
    ).status_code == 403


async def test_defaults_paused_and_observation_does_not_start_work(
    queue_data, client, test_db
):
    """Opening the panel neither claims a run nor creates processing requests."""
    response = await client.get("/admin/queue", headers=queue_data["headers"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    state = response.json()
    assert state["automatic_enabled"] is False
    assert state["queued_research"] == 2
    assert state["running_research"] == 0
    assert state["outstanding"] is None
    assert state["recent"] == []
    assert not state["worker_online"]
    assert "NOT-A-REAL-PASSWORD" not in response.text
    assert (
        await test_db.scalar(select(func.count()).select_from(QueueExecution))
        == 0
    )


async def test_enable_and_pause_are_persisted(
    queue_data, client, test_sessionmaker
):
    """The mode and administrator identity are durable, not per-process flags."""
    for enabled in (True, False):
        response = await client.patch(
            "/admin/queue/settings",
            json={"automatic_enabled": enabled},
            headers=queue_data["headers"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["automatic_enabled"] is enabled
        async with test_sessionmaker() as session:
            row = await session.get(QueueControl, 1)
            assert row.automatic_enabled is enabled
            assert row.updated_by_user_id == queue_data["admin"].id


async def test_paused_dispatch_does_not_claim_work(
    queue_data, test_sessionmaker
):
    """The managed loop must not turn on paid work just because API starts."""
    research, merge = empty_provider()
    assert await queue.dispatch_next(test_sessionmaker, research, merge) is None
    research.assert_not_awaited()
    async with test_sessionmaker() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(QueueExecution)
            )
            == 0
        )


async def test_manual_request_is_async_and_only_runs_one(
    queue_data, client, test_sessionmaker
):
    """One click admits one request, then processing remains paused."""
    response = await client.post(
        "/admin/queue/run-next", headers=queue_data["headers"]
    )
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "pending"
    assert response.json()["monitor_run_id"] is None
    assert (
        await client.post(
            "/admin/queue/run-next", headers=queue_data["headers"]
        )
    ).status_code == 409
    assert (
        await client.patch(
            "/admin/queue/settings",
            json={"automatic_enabled": True},
            headers=queue_data["headers"],
        )
    ).status_code == 409
    research, merge = empty_provider()
    run_id = await queue.dispatch_next(test_sessionmaker, research, merge)
    assert run_id is not None
    research.assert_awaited_once()
    merge.assert_not_awaited()
    assert await queue.dispatch_next(test_sessionmaker, research, merge) is None
    state = (
        await client.get("/admin/queue", headers=queue_data["headers"])
    ).json()
    assert state["outstanding"] is None
    assert state["queued_research"] == 1
    assert state["recent"][0]["monitor_run_id"] == str(run_id)
    assert state["recent"][0]["status"] == "completed"
    assert state["recent"][0]["run_status"] == "succeeded"
    assert state["recent"][0]["alert_status"] == "skipped"
    assert not state["automatic_enabled"]


async def test_manual_requires_pause(queue_data, client):
    """Manual admission cannot add hidden extra steps while automatic is on."""
    await client.patch(
        "/admin/queue/settings",
        json={"automatic_enabled": True},
        headers=queue_data["headers"],
    )
    response = await client.post(
        "/admin/queue/run-next", headers=queue_data["headers"]
    )
    assert response.status_code == 409


async def test_missing_provider_rejects_start_but_allows_pause(
    queue_data, client, monkeypatch
):
    """Missing key is reported without ever exposing its value."""
    await client.patch(
        "/admin/queue/settings",
        json={"automatic_enabled": True},
        headers=queue_data["headers"],
    )
    monkeypatch.setattr(queue, "provider_configured", lambda: False)
    assert (
        await client.patch(
            "/admin/queue/settings",
            json={"automatic_enabled": True},
            headers=queue_data["headers"],
        )
    ).status_code == 503
    assert (
        await client.patch(
            "/admin/queue/settings",
            json={"automatic_enabled": False},
            headers=queue_data["headers"],
        )
    ).status_code == 200
    assert (
        await client.post(
            "/admin/queue/run-next", headers=queue_data["headers"]
        )
    ).status_code == 503


async def test_empty_single_step_is_recorded(
    queue_data, client, test_db, test_sessionmaker
):
    """No work is a normal result; it does not trigger a polling chain."""
    await test_db.execute(
        MonitorRun.__table__.update().values(
            status="cancelled", completed_at=utc_now()
        )
    )
    await test_db.commit()
    assert (
        await client.post(
            "/admin/queue/run-next", headers=queue_data["headers"]
        )
    ).status_code == 202
    research, merge = empty_provider()
    assert await queue.dispatch_next(test_sessionmaker, research, merge) is None
    research.assert_not_awaited()
    state = (
        await client.get("/admin/queue", headers=queue_data["headers"])
    ).json()
    assert state["recent"][0]["status"] == "empty"
    assert state["outstanding"] is None


async def test_auto_consumes_next_then_obeys_pause(
    queue_data, client, test_sessionmaker
):
    """The next iteration rereads durable policy, rather than cached state."""
    await client.patch(
        "/admin/queue/settings",
        json={"automatic_enabled": True},
        headers=queue_data["headers"],
    )
    research, merge = empty_provider()
    assert (
        await queue.dispatch_next(test_sessionmaker, research, merge)
        is not None
    )
    await client.patch(
        "/admin/queue/settings",
        json={"automatic_enabled": False},
        headers=queue_data["headers"],
    )
    assert await queue.dispatch_next(test_sessionmaker, research, merge) is None
    research.assert_awaited_once()


async def test_pause_during_research_finishes_current_and_prevents_overlap(
    queue_data, client, test_sessionmaker, test_engine
):
    """A real PostgreSQL session lock excludes another worker during model I/O."""
    await client.patch(
        "/admin/queue/settings",
        json={"automatic_enabled": True},
        headers=queue_data["headers"],
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def research(objective, context):
        started.set()
        await release.wait()
        return ResearchResult(observations=[])

    first = asyncio.create_task(
        queue.dispatch_next(test_sessionmaker, research, AsyncMock())
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        state = (
            await client.get("/admin/queue", headers=queue_data["headers"])
        ).json()
        assert state["outstanding"]["status"] == "processing"
        assert state["outstanding"]["monitor_run_id"]
        assert state["running_research"] == 1
        async with queue.dispatch_lock(test_engine) as acquired:
            assert acquired is False
        response = await client.patch(
            "/admin/queue/settings",
            json={"automatic_enabled": False},
            headers=queue_data["headers"],
        )
        assert response.status_code == 200
        assert (
            await client.post(
                "/admin/queue/run-next", headers=queue_data["headers"]
            )
        ).status_code == 409
        release.set()
        assert await first is not None
        assert (
            await queue.dispatch_next(test_sessionmaker, research, AsyncMock())
            is None
        )
        async with queue.dispatch_lock(test_engine) as acquired:
            assert acquired is True
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)


async def test_cancellation_records_interruption_and_unlocks(
    queue_data, client, test_sessionmaker, test_engine
):
    """Graceful worker cancellation never leaves a falsely active controller."""
    await client.post("/admin/queue/run-next", headers=queue_data["headers"])
    started = asyncio.Event()

    async def research(objective, context):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        queue.dispatch_next(test_sessionmaker, research, AsyncMock())
    )
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = (
        await client.get("/admin/queue", headers=queue_data["headers"])
    ).json()
    assert state["outstanding"] is None
    assert state["recent"][0]["status"] == "interrupted"
    assert state["recent"][0]["run_status"] == "failed"
    async with queue.dispatch_lock(test_engine) as acquired:
        assert acquired


async def test_research_failure_is_distinct_from_controller_completion(
    queue_data, client, test_sessionmaker
):
    """A returned run ID must not be presented as successful research."""
    await client.post("/admin/queue/run-next", headers=queue_data["headers"])
    research = AsyncMock(side_effect=ResearchError("openai_request_failed"))
    assert await queue.dispatch_next(test_sessionmaker, research, AsyncMock())
    state = (
        await client.get("/admin/queue", headers=queue_data["headers"])
    ).json()
    assert state["recent"][0]["status"] == "completed"
    assert state["recent"][0]["run_status"] == "failed"
    assert state["recent"][0]["error_code"] == "openai_request_failed"


async def test_stale_controller_is_interrupted_without_replaying_research(
    queue_data, test_db, test_sessionmaker
):
    """After a hard crash, claim serialization does not mean paid work is safe to replay."""
    run = await test_db.scalar(
        select(MonitorRun).order_by(MonitorRun.created_at)
    )
    run.status = "running"
    run.attempt_count = 1
    run.started_at = utc_now()
    attempt = QueueExecution(
        mode="manual",
        status="processing",
        monitor_run_id=run.id,
        started_at=utc_now(),
    )
    test_db.add(attempt)
    await test_db.commit()
    research, merge = empty_provider()
    assert await queue.dispatch_next(test_sessionmaker, research, merge) is None
    research.assert_not_awaited()
    await test_db.refresh(attempt)
    await test_db.refresh(run)
    assert attempt.status == "interrupted"
    assert run.status == "running"


async def test_alert_work_priority_and_single_step_resume(
    queue_data, client, test_db, test_sessionmaker
):
    """One step handles research+composition; the next may resume a due digest."""
    finding = ResearchResult.model_validate(
        {
            "observations": [
                {
                    "summary": "Policy adopted",
                    "sources": [{"source_url": "https://example.com/rule"}],
                }
            ]
        }
    )
    knowledge = KnowledgeUpdate.model_validate(
        {
            "content": "Policy was adopted",
            "change_reason": "New policy",
            "alert_decisions": [
                {
                    "observation_ref": "finding_1",
                    "should_alert": True,
                    "what_changed": "Policy adopted",
                    "why_it_matters": "Affects residents",
                    "development_key": "policy:adopted",
                }
            ],
        }
    )
    await client.post("/admin/queue/run-next", headers=queue_data["headers"])
    composer = AsyncMock(side_effect=ResearchError("openai_request_failed"))
    research = AsyncMock(return_value=finding)
    run_id = await queue.dispatch_next(
        test_sessionmaker, research, AsyncMock(return_value=knowledge), composer
    )
    async with test_sessionmaker() as session, session.begin():
        job = await session.scalar(
            select(AlertJob).where(AlertJob.monitor_run_id == run_id)
        )
        assert job.status == "pending"
        job.next_attempt_at = utc_now() - timedelta(seconds=1)
    response = await client.get("/admin/queue", headers=queue_data["headers"])
    assert response.json()["due_alerts"] == 1
    await client.post("/admin/queue/run-next", headers=queue_data["headers"])
    composer = AsyncMock(
        return_value=AlertComposition(
            should_alert=True,
            reason="Useful new policy",
            title="Policy update",
            body="New rule adopted",
            observation_refs=["finding_1"],
        )
    )
    research = AsyncMock()
    assert (
        await queue.dispatch_next(
            test_sessionmaker, research, AsyncMock(), composer
        )
        == run_id
    )
    research.assert_not_awaited()
    composer.assert_awaited_once()
    state = (
        await client.get("/admin/queue", headers=queue_data["headers"])
    ).json()
    assert state["recent"][0]["alert_status"] == "published"
    assert state["queued_research"] == 1


async def test_heartbeat_and_delayed_alerts_are_truthful(queue_data, test_db):
    """Heartbeat age is liveness, independent from saved automatic mode."""
    row = await queue.control_row(test_db)
    row.heartbeat_at = utc_now()
    await test_db.flush()
    assert (await queue.queue_state(test_db)).worker_online
    row.heartbeat_at = utc_now() - timedelta(seconds=21)
    await test_db.flush()
    assert not (await queue.queue_state(test_db)).worker_online


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"automatic_enabled": "false"},
        {"automatic_enabled": True, "extra": True},
    ],
)
async def test_setting_validation_is_strict(queue_data, client, body):
    """Do not silently coerce strings or accept accidental control fields."""
    response = await client.patch(
        "/admin/queue/settings", json=body, headers=queue_data["headers"]
    )
    assert response.status_code == 422


async def test_auto_drains_existing_work_without_empty_history_spam(
    queue_data, client, test_sessionmaker
):
    """Automatic mode stays on but empty polls do not fill the audit history."""
    await client.patch(
        "/admin/queue/settings",
        json={"automatic_enabled": True},
        headers=queue_data["headers"],
    )
    research, merge = empty_provider()
    ids = [
        await queue.dispatch_next(test_sessionmaker, research, merge)
        for _ in range(2)
    ]
    assert len(set(ids)) == 2
    assert None not in ids
    assert await queue.dispatch_next(test_sessionmaker, research, merge) is None
    state = (
        await client.get("/admin/queue", headers=queue_data["headers"])
    ).json()
    assert state["automatic_enabled"]
    assert state["queued_research"] == 0
    assert len(state["recent"]) == 2
    assert all(item["mode"] == "automatic" for item in state["recent"])


async def test_cli_once_is_an_explicit_single_step_while_paused(
    queue_data, test_sessionmaker
):
    """Keep the existing CLI one-shot behavior under the shared dispatch lock."""
    research, merge = empty_provider()
    assert await queue.dispatch_next(
        test_sessionmaker, research, merge, once=True
    )
    assert await queue.dispatch_next(test_sessionmaker, research, merge) is None
    async with test_sessionmaker() as session:
        state = await queue.queue_state(session)
        assert not state.automatic_enabled
        assert len(state.recent) == 1
        assert state.recent[0].mode == "manual"
        assert state.recent[0].requested_by_user_id is None


async def test_controller_failure_is_safe_and_releases_lock(
    queue_data, client, test_sessionmaker, test_engine, monkeypatch
):
    """Internal failure reports a safe code, not exception text or credentials."""
    await client.post("/admin/queue/run-next", headers=queue_data["headers"])
    monkeypatch.setattr(
        queue,
        "process_next",
        AsyncMock(side_effect=ValueError("SECRET-MUST-NOT-LEAK")),
    )
    assert (
        await queue.dispatch_next(test_sessionmaker, *empty_provider()) is None
    )
    response = await client.get("/admin/queue", headers=queue_data["headers"])
    assert response.json()["recent"][0]["status"] == "failed"
    assert (
        response.json()["recent"][0]["error_code"] == "queue_execution_failed"
    )
    assert "SECRET-MUST-NOT-LEAK" not in response.text
    async with queue.dispatch_lock(test_engine) as acquired:
        assert acquired


async def test_api_managed_service_picks_up_durable_step(
    queue_data, client, test_sessionmaker, monkeypatch
):
    """The real background loop connects HTTP admission to actual processing."""
    import app.managers.research as research_module

    requested = await client.post(
        "/admin/queue/run-next", headers=queue_data["headers"]
    )
    assert requested.status_code == 202
    processed = asyncio.Event()
    original_dispatch = queue.dispatch_next

    class OfflineProvider:
        def __init__(self, http_client, settings):
            pass

        async def __call__(self, objective, context):
            return ResearchResult(observations=[])

        async def merge_knowledge(self, *args, **kwargs):
            raise AssertionError("No merge expected for empty findings")

        async def compose_alert(self, *args, **kwargs):
            raise AssertionError("No composition expected for empty findings")

    async def dispatch(*args, **kwargs):
        await original_dispatch(*args, **kwargs)
        processed.set()
        await asyncio.Event().wait()

    async def pulse(sessions):
        await asyncio.Event().wait()

    monkeypatch.setattr(research_module, "OpenAIResearch", OfflineProvider)
    monkeypatch.setattr(queue, "dispatch_next", dispatch)
    monkeypatch.setattr(queue, "heartbeat", pulse)
    async with queue.queue_service(test_sessionmaker):
        await asyncio.wait_for(processed.wait(), 5)
        state = (
            await client.get("/admin/queue", headers=queue_data["headers"])
        ).json()
        assert state["outstanding"] is None
        assert state["queued_research"] == 1
        assert state["recent"][0]["id"] == requested.json()["id"]
        assert state["recent"][0]["status"] == "completed"
