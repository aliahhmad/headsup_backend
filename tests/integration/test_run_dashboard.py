"""Verify admin access, historical fidelity, filters and real request tracing."""

import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import select

from app.config.settings import get_settings
from app.managers.auth import AuthManager
from app.managers.monitor import create_monitor
from app.managers.monitor_worker import process_next
from app.models.enums import RoleType
from app.models.monitor import (
    Alert,
    Chat,
    Memory,
    Message,
    MonitorRun,
    RunModelCall,
    utc_now,
)
from app.models.user import User
from app.schemas.request.monitor import MonitorCreate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def envelope(payload: dict, index: int) -> dict:
    """Return a realistic Responses message with safe usage metadata."""
    return {
        "id": f"response-{index}",
        "status": "completed",
        "model": "test-model",
        "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": json.dumps(payload)}
                ],
            }
        ],
    }


@pytest_asyncio.fixture
async def dashboard_data(test_db, test_sessionmaker) -> dict:
    """Run the full provider pipeline over MockTransport, with separate accounts."""
    from app.managers.research import OpenAIResearch

    accounts = []
    for role in (RoleType.admin, RoleType.user, RoleType.user):
        account = User(
            email=f"{uuid4().hex}@example.test",
            password="PASSWORD-MUST-NOT-LEAK",
            first_name="Run",
            last_name="Owner",
            role=role,
            verified=True,
            banned=False,
        )
        test_db.add(account)
        accounts.append(account)
    await test_db.flush()
    admin, owner, other = accounts
    monitor = await create_monitor(
        MonitorCreate(
            name="NYC policy updates",
            objective="Track meaningful city policy changes",
            monitor_context="Only adopted changes",
        ),
        owner.id,
        test_db,
    )
    await test_db.commit()
    search = envelope({}, 1)
    search["output"] = [
        {"type": "web_search_call", "status": "completed"},
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "A sourced policy development.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://example.com/policy",
                            "title": "Synthetic official source",
                        }
                    ],
                }
            ],
        },
    ]
    responses = [
        search,
        envelope(
            {
                "observations": [
                    {
                        "summary": "A meaningful policy development.",
                        "source_ids": ["s1"],
                    }
                ]
            },
            2,
        ),
        envelope(
            {
                "content": "Accepted knowledge from this run.",
                "change_reason": "Added a sourced change.",
                "alert_decisions": [
                    {
                        "observation_ref": "finding_1",
                        "should_alert": True,
                        "what_changed": "A new rule was adopted.",
                        "why_it_matters": "The rule affects residents.",
                        "development_key": "nyc:synthetic:rule-adopted",
                    }
                ],
            },
            3,
        ),
        envelope(
            {
                "should_alert": True,
                "reason": "Actionable development.",
                "title": "Synthetic NYC update",
                "body": "A sourced rule changed.",
                "observation_refs": ["finding_1"],
            },
            4,
        ),
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        assert (
            request.headers["authorization"]
            == "Bearer TRACE-SECRET-MUST-NOT-LEAK"
        )
        return httpx.Response(
            200,
            json=responses.pop(0),
            headers={"x-request-id": "safe-provider-request-id"},
        )

    settings = get_settings().model_copy(
        update={"openai_api_key": SecretStr("TRACE-SECRET-MUST-NOT-LEAK")}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        provider = OpenAIResearch(client, settings)
        run_id = await process_next(
            test_sessionmaker,
            provider,
            provider.merge_knowledge,
            provider.compose_alert,
        )
    assert not responses
    other_monitor = await create_monitor(
        MonitorCreate(name="Other monitor", objective="Different topic"),
        other.id,
        test_db,
    )
    knowledge = await test_db.scalar(
        select(Memory).where(
            Memory.monitor_id == monitor.id, Memory.kind == "monitor_knowledge"
        )
    )
    knowledge.content = "A later current-memory value."
    knowledge.version += 1
    test_db.add(
        Memory(
            user_id=owner.id,
            kind="user_profile",
            content="PROFILE-MUST-NOT-BE-SENT",
        )
    )
    chat = await test_db.scalar(
        select(Chat).where(Chat.monitor_id == monitor.id)
    )
    chat.summary = "CHAT-MUST-NOT-BE-SENT"
    test_db.add(
        Message(
            chat_id=chat.id,
            sequence_number=1,
            role="user",
            status="completed",
            content="Private chat message",
        )
    )
    await test_db.commit()
    return {
        "admin": admin,
        "owner": owner,
        "other": other,
        "monitor": monitor,
        "other_monitor": other_monitor,
        "run_id": run_id,
        "headers": {
            "Authorization": f"Bearer {AuthManager.encode_token(admin)}"
        },
    }


@pytest.mark.parametrize("endpoint", ["", "/summary", "/detail"])
async def test_dashboard_requires_admin(
    dashboard_data, client, endpoint
) -> None:
    """Cross-account execution inputs require actual administrator authorization."""
    base = "/admin/monitor-runs"
    path = (
        f"{base}/{dashboard_data['run_id']}"
        if endpoint == "/detail"
        else base + endpoint
    )
    assert (await client.get(path)).status_code == 401
    headers = {
        "Authorization": f"Bearer {AuthManager.encode_token(dashboard_data['owner'])}"
    }
    assert (await client.get(path, headers=headers)).status_code == 403


async def test_directory_summary_and_owner_projection(
    dashboard_data, client
) -> None:
    """List runs across accounts, with exact counts and no account credentials."""
    response = await client.get(
        "/admin/monitor-runs?size=1", headers=dashboard_data["headers"]
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["pages"] == 2
    assert "password" not in response.text
    summary = (
        await client.get(
            "/admin/monitor-runs/summary", headers=dashboard_data["headers"]
        )
    ).json()
    assert summary["total"] == 2
    assert summary["queued"] == 1
    assert summary["succeeded"] == 1
    assert summary["alerts_published"] == 1
    assert isinstance(summary["average_duration_seconds"], (int, float))


@pytest.mark.parametrize(
    "filter_name",
    ["q", "status", "user_id", "monitor_id", "alert_status", "uuid"],
)
async def test_directory_filters_share_summary_scope(
    dashboard_data, client, filter_name
) -> None:
    """Filtering the table and cards must produce the same subset."""
    values = {
        "q": "NYC",
        "status": "succeeded",
        "user_id": str(dashboard_data["owner"].id),
        "monitor_id": str(dashboard_data["monitor"].id),
        "alert_status": "published",
        "uuid": str(dashboard_data["run_id"]),
    }
    key = "q" if filter_name == "uuid" else filter_name
    params = {key: values[filter_name]}
    rows = (
        await client.get(
            "/admin/monitor-runs",
            params=params,
            headers=dashboard_data["headers"],
        )
    ).json()
    assert rows["total"] == 1
    item = rows["items"][0]
    assert item["id"] == str(dashboard_data["run_id"])
    assert item["observation_count"] == 1
    assert item["source_count"] == 1
    assert item["alert_count"] == 1
    assert item["trace_count"] == 4
    assert item["latest_stage"] == "composition"
    assert item["owner"]["email"] == dashboard_data["owner"].email
    summary = (
        await client.get(
            "/admin/monitor-runs/summary",
            params=params,
            headers=dashboard_data["headers"],
        )
    ).json()
    assert summary["total"] == rows["total"]


async def test_detail_preserves_snapshots_revisions_and_actual_inputs(
    dashboard_data, client, test_db
) -> None:
    """Historical inputs, run-linked outputs and current memories stay separate."""
    response = await client.get(
        f"/admin/monitor-runs/{dashboard_data['run_id']}",
        headers=dashboard_data["headers"],
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["snapshot_phase"] == "execution"
    before = next(
        memory
        for memory in data["memories_used"]
        if memory["kind"] == "monitor_knowledge"
    )
    assert before["content"] == ""
    assert before["version"] == 1
    assert (
        data["memory_changes"][0]["content"]
        == "Accepted knowledge from this run."
    )
    now = next(
        memory
        for memory in data["current_memories"]
        if memory["kind"] == "monitor_knowledge"
    )
    assert now["content"] == "A later current-memory value."
    assert now["version"] == 3
    assert (
        data["observations"][0]["sources"][0]["source_url"]
        == "https://example.com/policy"
    )
    assert (
        data["alerts"][0]["observations"][0]["id"]
        == data["observations"][0]["id"]
    )
    assert data["candidates"][0]["status"] == "published"
    assert data["alert_job"]["status"] == "published"
    assert [call["stage"] for call in data["model_calls"]] == [
        "research",
        "extraction",
        "knowledge",
        "composition",
    ]
    for call in data["model_calls"]:
        assert call["status"] == "succeeded"
        assert call["duration_ms"] >= 0
        assert call["response_metadata"]["usage"]["input_tokens"] == 12
        assert "authorization" not in call["request_payload"]
        assert "headers" not in call["request_payload"]
    knowledge_request = json.loads(
        data["model_calls"][2]["request_payload"]["input"]
    )
    assert knowledge_request["previous_knowledge"] == ""
    assert knowledge_request["monitor_context"] == ["Only adopted changes"]
    assert (
        knowledge_request["observations"][0]["observation_ref"] == "finding_1"
    )
    assert data["context_policy"]["personal_profile"]["present_now"] is True
    assert data["context_policy"]["chat_messages"]["count_now"] == 1
    for marker in (
        "PASSWORD-MUST-NOT-LEAK",
        "TRACE-SECRET-MUST-NOT-LEAK",
        "PROFILE-MUST-NOT-BE-SENT",
        "CHAT-MUST-NOT-BE-SENT",
    ):
        assert marker not in response.text
    alert = await test_db.scalar(
        select(Alert).where(Alert.monitor_run_id == dashboard_data["run_id"])
    )
    assert alert.read_at is None


async def test_legacy_and_queued_runs_do_not_invent_traces(
    dashboard_data, client, test_db
) -> None:
    """An enqueue snapshot is not evidence of a model call or historical memory use."""
    other = await test_db.scalar(
        select(MonitorRun).where(
            MonitorRun.monitor_id == dashboard_data["other_monitor"].id
        )
    )
    data = (
        await client.get(
            f"/admin/monitor-runs/{other.id}", headers=dashboard_data["headers"]
        )
    ).json()
    assert data["snapshot_phase"] == "enqueue"
    assert data["model_calls"] == []
    assert data["alert_job"] is None
    assert data["alerts"] == []


async def test_soft_deleted_monitor_runs_remain_inspectable_by_admin(
    dashboard_data, client, test_db
) -> None:
    """Operational history survives soft deletion without opening owner routes."""
    monitor = dashboard_data["monitor"]
    monitor.deleted_at = utc_now()
    monitor.next_run_at = None
    await test_db.commit()
    response = await client.get(
        f"/admin/monitor-runs/{dashboard_data['run_id']}",
        headers=dashboard_data["headers"],
    )
    assert response.status_code == 200
    assert response.json()["monitor_deleted_at"] is not None
    owner_headers = {
        "Authorization": f"Bearer {AuthManager.encode_token(dashboard_data['owner'])}"
    }
    assert (
        await client.get(f"/monitors/{monitor.id}", headers=owner_headers)
    ).status_code == 404


async def test_date_and_literal_search_filters(dashboard_data, client) -> None:
    """Dates require timezone-aware ordered boundaries; search wildcards are literal."""
    headers = dashboard_data["headers"]
    start = (utc_now() + timedelta(days=1)).isoformat()
    response = await client.get(
        "/admin/monitor-runs", params={"created_from": start}, headers=headers
    )
    assert response.json()["total"] == 0
    response = await client.get(
        "/admin/monitor-runs",
        params={
            "created_from": start,
            "created_to": (utc_now() - timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert (
        await client.get("/admin/monitor-runs?q=%25", headers=headers)
    ).json()["total"] == 0
    assert (
        await client.get(f"/admin/monitor-runs/{uuid4()}", headers=headers)
    ).status_code == 404


async def test_trace_error_preserves_safe_code_without_provider_body(
    dashboard_data, test_db, test_sessionmaker
) -> None:
    """Failures are captured independently, with no raw secret-bearing error body."""
    from app.managers.research import OpenAIResearch, ResearchError
    from app.managers.run_trace import trace_run

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "PRIVATE-PROVIDER-ERROR"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle)
    ) as client:
        async with trace_run(test_sessionmaker, dashboard_data["run_id"], 2):
            with pytest.raises(ResearchError, match="openai_http_429"):
                await OpenAIResearch(client, get_settings())._request(
                    stage="composition", input="Synthetic attempt"
                )
    calls = list(
        await test_db.scalars(
            select(RunModelCall).where(RunModelCall.attempt_number == 2)
        )
    )
    assert len(calls) == 1
    assert calls[0].status == "failed"
    assert calls[0].http_status == 429
    assert calls[0].error_code == "openai_http_429"
    assert "PRIVATE-PROVIDER-ERROR" not in json.dumps(
        calls[0].response_metadata
    )


async def test_all_false_decisions_record_no_composition_request(
    dashboard_data, test_db, test_sessionmaker, client
) -> None:
    """The run inspector must show three calls and a real skipped outcome."""
    from app.managers.research import OpenAIResearch

    search = envelope({}, 1)
    search["output"] = [
        {"type": "web_search_call", "status": "completed"},
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": "Background information.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://example.com/background",
                            "title": "Source",
                        }
                    ],
                }
            ],
        },
    ]
    responses = [
        search,
        envelope(
            {
                "observations": [
                    {"summary": "Background information.", "source_ids": ["s1"]}
                ]
            },
            2,
        ),
        envelope(
            {
                "content": "Useful background, with no actionable change.",
                "change_reason": "Added background.",
                "alert_decisions": [
                    {
                        "observation_ref": "finding_1",
                        "should_alert": False,
                        "what_changed": "Background only",
                        "why_it_matters": "No alert needed",
                        "development_key": None,
                    }
                ],
            },
            3,
        ),
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = OpenAIResearch(http, get_settings())
        run_id = await process_next(
            test_sessionmaker,
            provider,
            provider.merge_knowledge,
            provider.compose_alert,
        )
    detail = (
        await client.get(
            f"/admin/monitor-runs/{run_id}", headers=dashboard_data["headers"]
        )
    ).json()
    assert [call["stage"] for call in detail["model_calls"]] == [
        "research",
        "extraction",
        "knowledge",
    ]
    assert detail["alert_job"]["status"] == "skipped"
    assert detail["alert_job"]["reason_code"] == "all_decisions_false"
    assert detail["alerts"] == []
