"""Typed notifications, atomic event publication, ownership and read consistency."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.managers import alert as alert_manager
from app.managers import monitor as monitor_manager
from app.managers import notification as manager
from app.managers.auth import AuthManager
from app.managers.monitor_worker import process_next
from app.models.enums import RoleType
from app.models.monitor import Alert, AlertJob, Monitor, MonitorRun
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import (
    AlertPublishedEvent,
    AlertPublishedPayload,
    MonitorActivatedEvent,
    MonitorActivatedPayload,
)
from app.schemas.request.monitor import MonitorCreate
from app.schemas.research import (
    AlertComposition,
    KnowledgeUpdate,
    ResearchResult,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def inbox(test_db):
    """Use ordinary owner and unrelated admin; no live model calls."""
    users = []
    for role in (RoleType.user, RoleType.admin):
        user = User(
            email=f"{uuid4().hex}@example.test",
            password="SECRET-MUST-NOT-LEAK",
            first_name="Inbox",
            last_name="Owner",
            role=role,
            verified=True,
            banned=False,
        )
        test_db.add(user)
        users.append(user)
    await test_db.flush()
    owner, other = users
    monitor = await monitor_manager.create_monitor(
        MonitorCreate(
            name="NYC policies", objective="Follow adopted city policies"
        ),
        owner.id,
        test_db,
    )
    await test_db.commit()
    return {
        "owner": owner,
        "other": other,
        "monitor": monitor,
        "headers": {
            "Authorization": f"Bearer {AuthManager.encode_token(owner)}"
        },
        "other_headers": {
            "Authorization": f"Bearer {AuthManager.encode_token(other)}"
        },
    }


def providers(*, positive=True, compose=True):
    """One supported finding and a deterministic final digest or quiet outcome."""
    research = AsyncMock(
        return_value=ResearchResult.model_validate(
            {
                "observations": [
                    {
                        "summary": "A city policy was adopted",
                        "sources": [
                            {"source_url": "https://example.com/policy"}
                        ],
                    }
                ]
            }
        )
    )
    merger = AsyncMock(
        return_value=KnowledgeUpdate.model_validate(
            {
                "content": "A city policy was adopted",
                "change_reason": "Recorded adopted policy",
                "alert_decisions": [
                    {
                        "observation_ref": "finding_1",
                        "should_alert": positive,
                        "what_changed": "Policy adopted",
                        "why_it_matters": "Affects city residents",
                        "development_key": "nyc:policy:adopted"
                        if positive
                        else None,
                    }
                ],
            }
        )
    )
    composer = AsyncMock(
        return_value=AlertComposition(
            should_alert=compose,
            reason="An actionable change"
            if compose
            else "Not worth interrupting",
            title="A city policy update" if compose else None,
            body="A new policy was adopted. See the supporting evidence."
            if compose
            else None,
            observation_refs=["finding_1"] if compose else [],
        )
    )
    return research, merger, composer


async def rows(client, inbox, **params):
    response = await client.get(
        "/notifications", headers=inbox["headers"], params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/notifications", None),
        ("GET", "/notifications/count", None),
        (
            "PATCH",
            "/notifications/00000000-0000-0000-0000-000000000001",
            {"read": True},
        ),
        (
            "POST",
            "/notifications/seen",
            {"ids": ["00000000-0000-0000-0000-000000000001"]},
        ),
    ],
)
async def test_authentication_required(client, method, path, body):
    assert (await client.request(method, path, json=body)).status_code == 401


async def test_active_creation_emits_exact_typed_event(inbox, client, test_db):
    data = await rows(client, inbox)
    assert data["total"] == 1
    item = data["items"][0]
    assert item["type"] == "monitor.activated"
    assert item["payload"] == {
        "monitor_id": str(inbox["monitor"].id),
        "monitor_version": 1,
        "previous_status": "new",
    }
    assert item["title"] == "Monitor activated"
    assert item["target"] == f"/monitors/{inbox['monitor'].id}"
    assert item["read_at"] is None and item["seen_at"] is None
    assert "SECRET-MUST-NOT-LEAK" not in str(data)
    assert "alert_id" not in Notification.__table__.columns
    counts = await client.get("/notifications/count", headers=inbox["headers"])
    assert counts.headers["cache-control"] == "no-store"
    assert counts.json() == {"total": 1, "unread": 1, "unseen": 1}
    await rows(client, inbox)
    notification = await test_db.scalar(select(Notification))
    assert notification.seen_at is None
    assert notification.read_at is None


async def test_real_transitions_only_and_each_later_activation_is_distinct(
    inbox, client
):
    path = f"/monitors/{inbox['monitor'].id}"
    version = 1
    changes = [
        ({"status": "paused"}, 2),
        ({"status": "paused"}, 2),
        ({"name": "Renamed monitor"}, 3),
        ({"status": "active"}, 4),
        ({"status": "archived"}, 5),
        ({"status": "active"}, 6),
    ]
    for change, expected in changes:
        response = await client.patch(
            path,
            headers=inbox["headers"],
            json={"expected_version": version, **change},
        )
        assert response.status_code == 200, response.text
        version = response.json()["version"]
        assert version == expected
    data = await rows(client, inbox)
    assert data["total"] == 4
    assert [item["type"] for item in data["items"]] == [
        "monitor.activated",
        "monitor.activated",
        "monitor.paused",
        "monitor.activated",
    ]
    assert [item["payload"]["monitor_version"] for item in data["items"]] == [
        6,
        4,
        2,
        1,
    ]
    assert (
        await client.patch(
            path,
            headers=inbox["headers"],
            json={"expected_version": 1, "status": "paused"},
        )
    ).status_code == 409
    assert (await rows(client, inbox))["total"] == 4


async def test_retrying_same_event_deduplicates(inbox, test_db):
    monitor = inbox["monitor"]
    event = MonitorActivatedEvent(
        payload=MonitorActivatedPayload(
            monitor_id=monitor.id, monitor_version=1, previous_status="new"
        )
    )
    await manager.publish_notification(test_db, monitor, event)
    await manager.publish_notification(test_db, monitor, event)
    assert await test_db.scalar(select(func.count(Notification.id))) == 1


async def test_lifecycle_failure_rolls_back_transition_and_notifications(
    inbox, client, monkeypatch
):
    monkeypatch.setattr(
        monitor_manager,
        "publish_notification",
        AsyncMock(side_effect=RuntimeError("Synthetic write failure")),
    )
    with pytest.raises(RuntimeError, match="Synthetic write failure"):
        await client.patch(
            f"/monitors/{inbox['monitor'].id}",
            headers=inbox["headers"],
            json={"expected_version": 1, "status": "paused"},
        )
    response = await client.get(
        f"/monitors/{inbox['monitor'].id}", headers=inbox["headers"]
    )
    assert response.json()["status"] == "active"
    assert response.json()["version"] == 1
    assert response.json()["latest_run"]["status"] == "queued"
    assert (await rows(client, inbox))["total"] == 1


async def test_creation_rollback_leaves_no_inbox_copy(inbox, test_sessionmaker):
    async with test_sessionmaker() as session:
        with pytest.raises(RuntimeError, match="rollback"):
            async with session.begin():
                monitor = await monitor_manager.create_monitor(
                    MonitorCreate(name="Rollback", objective="Not committed"),
                    inbox["owner"].id,
                    session,
                )
                monitor_id = monitor.id
                raise RuntimeError("rollback")
    async with test_sessionmaker() as session:
        assert await session.get(Monitor, monitor_id) is None
        assert (
            await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.scope_monitor_id == monitor_id
                )
            )
            == 0
        )


async def test_alert_publishes_one_notification_with_direct_update_link(
    inbox, client, test_db, test_sessionmaker
):
    run_id = await process_next(test_sessionmaker, *providers())
    alert = await test_db.scalar(
        select(Alert).where(Alert.monitor_run_id == run_id)
    )
    assert alert is not None
    data = await rows(client, inbox, type="alert.published")
    assert data["total"] == 1
    item = data["items"][0]
    assert item["payload"] == {"alert_id": str(alert.id)}
    assert item["title"] == alert.title
    assert item["target"] == f"/monitors/{alert.monitor_id}?alert={alert.id}"
    await manager.publish_notification(
        test_db,
        inbox["monitor"],
        AlertPublishedEvent(payload=AlertPublishedPayload(alert_id=alert.id)),
    )
    await test_db.commit()
    assert (await rows(client, inbox, type="alert.published"))["total"] == 1


@pytest.mark.parametrize(
    ("positive", "compose"), [(False, True), (True, False)]
)
async def test_quiet_runs_do_not_generate_alert_notifications(
    inbox, client, test_sessionmaker, positive, compose
):
    research, merger, composer = providers(positive=positive, compose=compose)
    await process_next(test_sessionmaker, research, merger, composer)
    if not positive:
        composer.assert_not_awaited()
    assert (await rows(client, inbox, type="alert.published"))["total"] == 0
    assert (await rows(client, inbox))["total"] == 1


async def test_notification_failure_rolls_back_publication_then_retry_emits_once(
    inbox, client, test_db, test_sessionmaker, monkeypatch
):
    original = alert_manager.publish_notification
    monkeypatch.setattr(
        alert_manager,
        "publish_notification",
        AsyncMock(side_effect=RuntimeError("Synthetic notification failure")),
    )
    run_id = await process_next(test_sessionmaker, *providers())
    assert await test_db.scalar(select(func.count(Alert.id))) == 0
    assert (await rows(client, inbox, type="alert.published"))["total"] == 0
    job = await test_db.scalar(
        select(AlertJob).where(AlertJob.monitor_run_id == run_id)
    )
    assert job.status == "pending"
    run = await test_db.get(MonitorRun, run_id)
    assert run.status == "succeeded"
    monkeypatch.setattr(alert_manager, "publish_notification", original)
    from app.models.monitor import utc_now

    job.next_attempt_at = utc_now()
    await test_db.commit()
    composer = providers()[2]
    assert (
        await alert_manager.process_next_alert(test_sessionmaker, composer)
        == run_id
    )
    assert (await rows(client, inbox, type="alert.published"))["total"] == 1


async def test_other_account_including_admin_cannot_access_or_mutate(
    inbox, client
):
    notification_id = (await rows(client, inbox))["items"][0]["id"]
    assert (
        await client.get("/notifications", headers=inbox["other_headers"])
    ).json()["total"] == 0
    assert (
        await client.get("/notifications/count", headers=inbox["other_headers"])
    ).json()["unread"] == 0
    assert (
        await client.patch(
            f"/notifications/{notification_id}",
            headers=inbox["other_headers"],
            json={"read": True},
        )
    ).status_code == 404
    assert (
        await client.post(
            "/notifications/seen",
            headers=inbox["other_headers"],
            json={"ids": [notification_id]},
        )
    ).json()["updated"] == 0
    assert (await rows(client, inbox))["items"][0]["read_at"] is None
    assert (await rows(client, inbox))["items"][0]["seen_at"] is None


async def test_read_and_seen_are_distinct_and_idempotent(inbox, client):
    item = (await rows(client, inbox))["items"][0]
    seen = await client.post(
        "/notifications/seen",
        headers=inbox["headers"],
        json={"ids": [item["id"], str(uuid4())]},
    )
    assert seen.json()["updated"] == 1
    assert (
        await client.post(
            "/notifications/seen",
            headers=inbox["headers"],
            json={"ids": [item["id"]]},
        )
    ).json()["updated"] == 0
    item = (await rows(client, inbox))["items"][0]
    assert item["seen_at"] is not None and item["read_at"] is None
    first = await client.patch(
        f"/notifications/{item['id']}",
        headers=inbox["headers"],
        json={"read": True},
    )
    assert first.status_code == 200, first.text
    second = await client.patch(
        f"/notifications/{item['id']}",
        headers=inbox["headers"],
        json={"read": True},
    )
    assert first.json()["read_at"] == second.json()["read_at"]
    assert (await rows(client, inbox, unread="true"))["total"] == 0
    assert (await rows(client, inbox))["total"] == 1
    assert (
        await client.patch(
            f"/notifications/{item['id']}",
            headers=inbox["headers"],
            json={"read": False},
        )
    ).json()["read_at"] is None


async def test_alert_read_sync_works_in_both_directions(
    inbox, client, test_sessionmaker
):
    await process_next(test_sessionmaker, *providers())
    item = (await rows(client, inbox, type="alert.published"))["items"][0]
    alert_path = (
        f"/monitors/{inbox['monitor'].id}/alerts/{item['payload']['alert_id']}"
    )
    notification_path = f"/notifications/{item['id']}"
    response = await client.patch(
        alert_path, headers=inbox["headers"], json={"read": True}
    )
    assert response.status_code == 200
    notification = (await rows(client, inbox, type="alert.published"))["items"][
        0
    ]
    assert notification["read_at"] == response.json()["read_at"]
    assert notification["seen_at"] is not None
    await client.patch(
        notification_path, headers=inbox["headers"], json={"read": False}
    )
    assert (await client.get(alert_path, headers=inbox["headers"])).json()[
        "read_at"
    ] is None
    response = await client.patch(
        notification_path, headers=inbox["headers"], json={"read": True}
    )
    assert response.status_code == 200
    assert (await client.get(alert_path, headers=inbox["headers"])).json()[
        "read_at"
    ] == response.json()["read_at"]
    await client.patch(
        alert_path, headers=inbox["headers"], json={"read": False}
    )
    assert (await rows(client, inbox, type="alert.published"))["items"][0][
        "read_at"
    ] is None


async def test_soft_delete_removes_inbox_entries_and_links(
    inbox, client, test_db
):
    response = await client.delete(
        f"/monitors/{inbox['monitor'].id}?expected_version=1",
        headers=inbox["headers"],
    )
    assert response.status_code == 204
    assert (await rows(client, inbox))["total"] == 0
    assert await test_db.scalar(select(func.count(Notification.id))) == 0


async def test_hard_purge_cascades_to_notifications(inbox, test_db):
    await test_db.execute(
        delete(Monitor).where(Monitor.id == inbox["monitor"].id)
    )
    await test_db.flush()
    assert await test_db.scalar(select(func.count(Notification.id))) == 0


async def test_orphaned_alert_reference_is_hidden_from_history_and_counts(
    inbox, client, test_db, test_sessionmaker
):
    await process_next(test_sessionmaker, *providers())
    item = (await rows(client, inbox, type="alert.published"))["items"][0]
    await test_db.execute(
        delete(Alert).where(Alert.id == UUID(item["payload"]["alert_id"]))
    )
    await test_db.commit()
    assert (await rows(client, inbox, type="alert.published"))["total"] == 0
    assert (
        await client.get("/notifications/count", headers=inbox["headers"])
    ).json()["total"] == 1
    assert (
        await client.patch(
            f"/notifications/{item['id']}",
            headers=inbox["headers"],
            json={"read": True},
        )
    ).status_code == 404


async def test_payload_scope_and_owner_are_checked(inbox, test_db):
    with pytest.raises(ValueError, match="does not belong"):
        await manager.publish_notification(
            test_db,
            inbox["monitor"],
            AlertPublishedEvent(
                payload=AlertPublishedPayload(alert_id=uuid4())
            ),
        )
    with pytest.raises(ValueError, match="does not match"):
        await manager.publish_notification(
            test_db,
            inbox["monitor"],
            MonitorActivatedEvent(
                payload=MonitorActivatedPayload(
                    monitor_id=uuid4(), monitor_version=1, previous_status="new"
                )
            ),
        )
    async with test_db.begin_nested():
        with pytest.raises(IntegrityError):
            async with test_db.begin_nested():
                test_db.add(
                    Notification(
                        user_id=inbox["other"].id,
                        scope_monitor_id=inbox["monitor"].id,
                        type="alert.published",
                        payload={"alert_id": str(uuid4())},
                        deduplication_key="invalid-owner",
                    )
                )
                await test_db.flush()


async def test_type_filter_and_pagination_are_consistent(inbox, client):
    await client.patch(
        f"/monitors/{inbox['monitor'].id}",
        headers=inbox["headers"],
        json={"expected_version": 1, "status": "paused"},
    )
    newest = await rows(client, inbox, size=1, page=1)
    oldest = await rows(client, inbox, size=1, page=2)
    assert newest["total"] == oldest["total"] == 2
    assert newest["items"][0]["type"] == "monitor.paused"
    assert oldest["items"][0]["type"] == "monitor.activated"
    assert (await rows(client, inbox, type="monitor.paused"))["total"] == 1
    assert (
        await client.get(
            "/notifications?type=alert.whatever", headers=inbox["headers"]
        )
    ).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"read": None},
        {"read": "true"},
        {"seen": False},
        {"type": "alert.published"},
    ],
)
async def test_invalid_state_changes_are_rejected(inbox, client, body):
    item = (await rows(client, inbox))["items"][0]
    assert (
        await client.patch(
            f"/notifications/{item['id']}", headers=inbox["headers"], json=body
        )
    ).status_code == 422


async def test_notification_creation_is_not_a_public_api(inbox, client):
    assert (
        await client.post(
            "/notifications",
            headers=inbox["headers"],
            json={
                "type": "alert.published",
                "payload": {"alert_id": str(uuid4())},
            },
        )
    ).status_code == 405
