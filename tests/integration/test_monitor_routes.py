"""Exercise monitor ownership, initialization, lifecycle, and retry behavior."""

# Expected response codes, versions, and counts stay literal in scenario tests.
# ruff: noqa: PLR2004

import asyncio
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.managers import monitor as manager
from app.managers.auth import AuthManager
from app.models.enums import RoleType
from app.models.monitor import Chat, Memory, MemoryRevision, Monitor, MonitorRun
from app.models.user import User
from app.schemas.request.monitor import MonitorCreate, MonitorUpdate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
TEST_PASSWORD = "unused-token-authentication-password"  # noqa: S105
CONFIG = {
    "name": "Internships",
    "objective": "Follow backend internship openings",
}


@pytest_asyncio.fixture
async def monitor_users(test_db: AsyncSession) -> tuple[User, User]:
    """Create a regular owner and an unrelated administrator."""
    users = tuple(
        User(
            email=f"{uuid4().hex}@example.test",
            password=TEST_PASSWORD,
            first_name="Monitor",
            last_name="Owner",
            verified=True,
            banned=False,
            role=role,
        )
        for role in (RoleType.user, RoleType.admin)
    )
    test_db.add_all(users)
    await test_db.commit()
    return users[0], users[1]


@pytest.fixture
def monitor_headers(monitor_users: tuple[User, User]) -> dict[str, str]:
    """Authenticate as the monitor owner using the existing JWT flow."""
    return {
        "Authorization": f"Bearer {AuthManager.encode_token(monitor_users[0])}"
    }


async def create(
    client: AsyncClient, headers: dict[str, str], **changes: str | int
) -> dict[str, Any]:
    """Create a monitor through the API and return its workspace."""
    response = await client.post(
        "/monitors", headers=headers, json={**CONFIG, **changes}
    )
    assert response.status_code == 201, response.text
    return cast("dict[str, Any]", response.json())


async def test_creation_initializes_workspace(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    monitor_users: tuple[User, User],
    test_db: AsyncSession,
) -> None:
    """One creation persists chat, memories/revisions, and an initial run."""
    data = await create(
        client, monitor_headers, monitor_context="  Summer 2027  "
    )
    assert data["user_id"] == monitor_users[0].id
    assert data["status"] == "active"
    assert data["version"] == 1
    assert data["check_interval_seconds"] == 86400
    created_at = datetime.fromisoformat(data["created_at"])
    next_run = datetime.fromisoformat(data["next_run_at"])
    assert 86390 < (next_run - created_at).total_seconds() <= 86400
    assert data["chat"]["summary"] is None
    assert data["chat"]["summarized_through_sequence"] == 0
    memories = {item["kind"]: item for item in data["memories"]}
    assert memories["monitor_context"]["content"] == "Summer 2027"
    assert memories["monitor_knowledge"]["content"] == ""
    for memory in memories.values():
        revision = (
            await test_db.scalars(
                select(MemoryRevision).where(
                    MemoryRevision.memory_id == UUID(memory["id"])
                )
            )
        ).one()
        assert revision.version == memory["version"] == 1
        assert revision.content == memory["content"]
        assert revision.source_kind == "initialization"
    run = data["latest_run"]
    assert run["trigger"] == "initial"
    assert run["status"] == "queued"
    assert run["attempt_count"] == 0
    assert run["started_at"] is None
    assert run["objective_snapshot"] == CONFIG["objective"]
    assert {
        item["memory_id"] for item in run["context_snapshot"]["memories"]
    } == {item["id"] for item in data["memories"]}
    assert "summary" not in run["context_snapshot"]
    fetched = await client.get(
        f"/monitors/{data['id']}", headers=monitor_headers
    )
    assert fetched.status_code == 200
    assert fetched.json() == data
    assert fetched.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "invalid",
    [
        {"name": " \t\n"},
        {"objective": " \n"},
        {"name": None},
        {"check_interval_seconds": 0},
        {"check_interval_seconds": -1},
        {"check_interval_seconds": True},
        {"check_interval_seconds": 31536001},
        {"status": "paused"},
        {"user_id": 999},
        {"version": 4},
        {"monitor_knowledge": "invented"},
    ],
)
async def test_invalid_creation_is_rejected(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    invalid: dict[str, Any],
) -> None:
    """Reject invalid configuration and attempts to set server-owned fields."""
    response = await client.post(
        "/monitors", headers=monitor_headers, json={**CONFIG, **invalid}
    )
    assert response.status_code == 422


async def test_list_filters_and_paginates_only_owned_monitors(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    monitor_users: tuple[User, User],
) -> None:
    """An unrelated administrator's records stay out of the owner's list."""
    first = await create(client, monitor_headers, name="First")
    await create(client, monitor_headers, name="Second")
    other = {
        "Authorization": f"Bearer {AuthManager.encode_token(monitor_users[1])}"
    }
    await create(client, other, name="Someone else's topic")
    response = await client.get(
        "/monitors?size=1&page=1", headers=monitor_headers
    )
    assert response.json()["total"] == 2
    assert response.json()["pages"] == 2
    assert response.json()["items"][0]["name"] == "Second"
    await client.patch(
        f"/monitors/{first['id']}",
        headers=monitor_headers,
        json={"expected_version": 1, "status": "paused"},
    )
    paused = await client.get(
        "/monitors?status=paused", headers=monitor_headers
    )
    assert paused.json()["total"] == 1
    assert paused.json()["items"][0]["id"] == first["id"]
    invalid = await client.get(
        "/monitors?status=draft", headers=monitor_headers
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/monitors", None),
        ("POST", "/monitors", CONFIG),
        ("GET", "/monitors/{id}", None),
        ("PATCH", "/monitors/{id}", {"expected_version": 1, "name": "Changed"}),
        ("DELETE", "/monitors/{id}?expected_version=1", None),
        ("POST", "/monitors/{id}/checks", {"client_request_id": str(uuid4())}),
        ("GET", "/monitors/{id}/runs", None),
    ],
)
async def test_authentication_required(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """Every management and run-history endpoint requires authentication."""
    response = await client.request(method, path.format(id=uuid4()), json=body)
    assert response.status_code == 401


async def test_foreign_monitor_and_runs_are_hidden_even_from_admin(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    monitor_users: tuple[User, User],
) -> None:
    """Knowing a UUID or having admin role does not grant monitor ownership."""
    data = await create(client, monitor_headers)
    other = {
        "Authorization": f"Bearer {AuthManager.encode_token(monitor_users[1])}"
    }
    root = f"/monitors/{data['id']}"
    for method, path, body in (
        ("GET", root, None),
        ("GET", root + "/runs", None),
        ("GET", root + f"/runs/{data['latest_run']['id']}", None),
        ("PATCH", root, {"expected_version": 1, "name": "Taken"}),
        ("DELETE", root + "?expected_version=1", None),
        ("POST", root + "/checks", {"client_request_id": str(uuid4())}),
    ):
        response = await client.request(method, path, headers=other, json=body)
        assert response.status_code == 404
    fetched = await client.get(root, headers=monitor_headers)
    assert fetched.json()["version"] == 1


async def test_update_version_noop_and_queued_snapshot(
    client: AsyncClient, monitor_headers: dict[str, str]
) -> None:
    """Edits reject stale writers and preserve enqueue-time input history."""
    data = await create(client, monitor_headers)
    path = f"/monitors/{data['id']}"
    response = await client.patch(
        path,
        headers=monitor_headers,
        json={
            "expected_version": 1,
            "objective": "  Follow research internships  ",
        },
    )
    assert response.status_code == 200
    assert response.json()["objective"] == "Follow research internships"
    assert response.json()["version"] == 2
    assert (
        response.json()["latest_run"]["objective_snapshot"]
        == CONFIG["objective"]
    )
    noop = await client.patch(
        path,
        headers=monitor_headers,
        json={
            "expected_version": 2,
            "objective": "Follow research internships",
        },
    )
    assert noop.json()["version"] == 2
    stale = await client.patch(
        path,
        headers=monitor_headers,
        json={"expected_version": 1, "name": "Stale"},
    )
    assert stale.status_code == 409
    stale_delete = await client.delete(
        path + "?expected_version=1", headers=monitor_headers
    )
    assert stale_delete.status_code == 409


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"name": None},
        {"objective": "  "},
        {"status": "draft"},
        {"status": None},
        {"check_interval_seconds": None},
        {"user_id": 12},
    ],
)
async def test_invalid_updates_are_rejected(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    changes: dict[str, Any],
) -> None:
    """An edit cannot clear required configuration or reassign ownership."""
    data = await create(client, monitor_headers)
    response = await client.patch(
        f"/monitors/{data['id']}",
        headers=monitor_headers,
        json={"expected_version": 1, **changes},
    )
    assert response.status_code == 422


async def test_pause_resume_and_archive_preserve_workspace(
    client: AsyncClient, monitor_headers: dict[str, str]
) -> None:
    """Status changes clear scheduling, cancel work, and reuse the same chat."""
    data = await create(client, monitor_headers)
    path = f"/monitors/{data['id']}"
    paused = await client.patch(
        path,
        headers=monitor_headers,
        json={"expected_version": 1, "status": "paused"},
    )
    assert paused.json()["next_run_at"] is None
    assert paused.json()["latest_run"]["status"] == "cancelled"
    resumed = await client.patch(
        path,
        headers=monitor_headers,
        json={"expected_version": 2, "status": "active"},
    )
    assert resumed.json()["version"] == 3
    assert resumed.json()["next_run_at"] is not None
    assert resumed.json()["chat"]["id"] == data["chat"]["id"]
    assert resumed.json()["memories"] == data["memories"]
    assert resumed.json()["latest_run"]["trigger"] == "scheduled"
    assert resumed.json()["latest_run"]["monitor_version"] == 3
    archived = await client.patch(
        path,
        headers=monitor_headers,
        json={"expected_version": 3, "status": "archived"},
    )
    assert archived.json()["next_run_at"] is None
    assert archived.json()["latest_run"]["status"] == "cancelled"
    blocked = await client.post(
        path + "/checks",
        headers=monitor_headers,
        json={"client_request_id": str(uuid4())},
    )
    assert blocked.status_code == 409


async def test_manual_checks_coalesce_without_chat_summary(
    client: AsyncClient, monitor_headers: dict[str, str], test_db: AsyncSession
) -> None:
    """Manual checks work while paused and do not reset scheduling."""
    data = await create(
        client, monitor_headers, monitor_context="Paid roles only"
    )
    path = f"/monitors/{data['id']}"
    await client.patch(
        path,
        headers=monitor_headers,
        json={"expected_version": 1, "status": "paused"},
    )
    await test_db.execute(
        update(Chat)
        .where(Chat.id == UUID(data["chat"]["id"]))
        .values(summary="Conversation-only information")
    )
    await test_db.commit()
    body = {"client_request_id": str(uuid4())}
    checked = await client.post(
        path + "/checks", headers=monitor_headers, json=body
    )
    assert checked.status_code == 202
    assert checked.json()["trigger"] == "manual"
    assert checked.json()["status"] == "queued"
    assert "Conversation-only information" not in checked.text
    repeated = await client.post(
        path + "/checks", headers=monitor_headers, json=body
    )
    overlapping = await client.post(
        path + "/checks",
        headers=monitor_headers,
        json={"client_request_id": str(uuid4())},
    )
    assert (
        repeated.json()["id"]
        == overlapping.json()["id"]
        == checked.json()["id"]
    )
    fetched = await client.get(path, headers=monitor_headers)
    assert fetched.json()["next_run_at"] is None
    # Changing other configuration while paused must not cancel a manual check.
    changed = await client.patch(
        path,
        headers=monitor_headers,
        json={"expected_version": 2, "name": "Renamed"},
    )
    assert changed.json()["latest_run"]["status"] == "queued"


async def test_manual_retry_after_completion_and_cooldown(
    client: AsyncClient, monitor_headers: dict[str, str], test_db: AsyncSession
) -> None:
    """Completed retries retain identity; distinct rapid checks are limited."""
    data = await create(client, monitor_headers)
    path = f"/monitors/{data['id']}"
    await client.patch(
        path,
        headers=monitor_headers,
        json={"expected_version": 1, "status": "paused"},
    )
    body = {"client_request_id": str(uuid4())}
    checked = await client.post(
        path + "/checks", headers=monitor_headers, json=body
    )
    await test_db.execute(
        update(MonitorRun)
        .where(MonitorRun.id == UUID(checked.json()["id"]))
        .values(status="succeeded", completed_at=datetime.now(timezone.utc))
    )
    await test_db.commit()
    retried = await client.post(
        path + "/checks", headers=monitor_headers, json=body
    )
    assert retried.json()["id"] == checked.json()["id"]
    assert retried.json()["status"] == "succeeded"
    limited = await client.post(
        path + "/checks",
        headers=monitor_headers,
        json={"client_request_id": str(uuid4())},
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


async def test_soft_delete_hides_children_and_cancels_queued_work(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    monitor_users: tuple[User, User],
    test_db: AsyncSession,
) -> None:
    """Deletion revokes access without erasing history or shared profile."""
    data = await create(client, monitor_headers)
    profile = Memory(
        user_id=monitor_users[0].id,
        kind="user_profile",
        content="Shared context",
    )
    test_db.add(profile)
    await test_db.commit()
    path = f"/monitors/{data['id']}"
    deleted = await client.delete(
        path + "?expected_version=1", headers=monitor_headers
    )
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert (await client.get(path, headers=monitor_headers)).status_code == 404
    assert (
        await client.get(path + "/runs", headers=monitor_headers)
    ).status_code == 404
    assert (
        await client.post(
            path + "/checks",
            headers=monitor_headers,
            json={"client_request_id": str(uuid4())},
        )
    ).status_code == 404
    assert (await client.get("/monitors", headers=monitor_headers)).json()[
        "total"
    ] == 0
    saved = await test_db.get(Monitor, UUID(data["id"]))
    assert saved is not None
    assert saved.deleted_at is not None
    assert saved.next_run_at is None
    run = await test_db.get(MonitorRun, UUID(data["latest_run"]["id"]))
    assert run is not None
    assert run.status == "cancelled"
    assert await test_db.get(Chat, UUID(data["chat"]["id"])) is not None
    assert await test_db.get(Memory, profile.id) is not None


async def test_run_cannot_be_read_through_another_monitor(
    client: AsyncClient, monitor_headers: dict[str, str]
) -> None:
    """Even two monitors of the same owner have separate run namespaces."""
    first = await create(client, monitor_headers)
    second = await create(client, monitor_headers)
    wrong = await client.get(
        f"/monitors/{second['id']}/runs/{first['latest_run']['id']}",
        headers=monitor_headers,
    )
    assert wrong.status_code == 404
    history = await client.get(
        f"/monitors/{first['id']}/runs?size=1", headers=monitor_headers
    )
    assert history.status_code == 200
    assert history.json()["items"][0]["id"] == first["latest_run"]["id"]


async def test_failed_initialization_rolls_back_every_record(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    monitor_users: tuple[User, User],
    test_db: AsyncSession,
    mocker: MockerFixture,
) -> None:
    """A failure after child inserts cannot leave a partial monitor."""
    mocker.patch(
        "app.managers.monitor._enqueue",
        side_effect=HTTPException(503, "Test queue failure"),
    )
    response = await client.post(
        "/monitors", headers=monitor_headers, json=CONFIG
    )
    assert response.status_code == 503
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(Monitor)
            .where(Monitor.user_id == monitor_users[0].id)
        )
        == 0
    )
    assert (
        await test_db.scalar(
            select(func.count())
            .select_from(Memory)
            .where(Memory.user_id == monitor_users[0].id)
        )
        == 0
    )


async def test_database_rejects_foreign_memory_and_overlapping_runs(
    client: AsyncClient,
    monitor_headers: dict[str, str],
    monitor_users: tuple[User, User],
    test_db: AsyncSession,
) -> None:
    """Constraints back up application ownership and coalescing rules."""
    data = await create(client, monitor_headers)
    bare = Monitor(
        user_id=monitor_users[0].id,
        name="Foreign memory target",
        objective="An objective",
        next_run_at=datetime.now(timezone.utc),
    )
    test_db.add(bare)
    await test_db.flush()
    with pytest.raises(IntegrityError) as error:  # noqa: PT012
        async with test_db.begin_nested():
            test_db.add(
                Memory(
                    user_id=monitor_users[1].id,
                    monitor_id=bare.id,
                    kind="monitor_context",
                    content="Foreign",
                )
            )
            await test_db.flush()
    assert "fk_memories_monitor_id_monitors" in str(error.value)
    with pytest.raises(IntegrityError) as error:  # noqa: PT012
        async with test_db.begin_nested():
            test_db.add(
                MonitorRun(
                    monitor_id=UUID(data["id"]),
                    trigger="manual",
                    idempotency_key=str(uuid4()),
                    monitor_version=1,
                    objective_snapshot="Duplicate",
                    context_snapshot={},
                )
            )
            await test_db.flush()
    assert "uq_monitor_runs_in_flight" in str(error.value)


async def test_concurrent_requests_coalesce_and_reject_stale_edits(
    test_engine: AsyncEngine,
) -> None:
    """Separate committed connections exercise real PostgreSQL row locking."""
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        user = User(
            email=f"concurrent-{uuid4().hex}@example.test",
            password=TEST_PASSWORD,
            first_name="Concurrent",
            last_name="Owner",
            role=RoleType.user,
            verified=True,
            banned=False,
        )
        session.add(user)
        await session.flush()
        monitor = await manager.create_monitor(
            MonitorCreate.model_validate(CONFIG), user.id, session
        )
        await manager.update_monitor(
            monitor, MonitorUpdate(expected_version=1, status="paused"), session
        )
        user_id, monitor_id = user.id, monitor.id
    try:

        async def check() -> UUID:
            async with sessions() as session, session.begin():
                owned = await manager.owned_monitor(
                    monitor_id, user_id, session, lock=True
                )
                return (
                    await manager.queue_manual_check(owned, uuid4(), session)
                ).id

        runs = await asyncio.gather(check(), check())
        assert runs[0] == runs[1]

        async def edit(name: str) -> int:
            async with sessions() as session, session.begin():
                owned = await manager.owned_monitor(
                    monitor_id, user_id, session, lock=True
                )
                try:
                    await manager.update_monitor(
                        owned,
                        MonitorUpdate(expected_version=2, name=name),
                        session,
                    )
                except HTTPException as error:
                    return error.status_code
                return 200

        results = await asyncio.gather(
            edit("First writer"), edit("Second writer")
        )
        assert sorted(results) == [200, 409]
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(User).where(User.id == user_id))
