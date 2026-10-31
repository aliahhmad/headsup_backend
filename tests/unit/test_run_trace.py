"""Ensure request diagnostics are isolated and cannot break provider calls."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from app.config.settings import get_settings
from app.managers.research import OpenAIResearch
from app.managers.run_trace import trace_request, trace_run

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_trace_scope_is_task_local_and_reset(mocker) -> None:
    """Concurrent runs must never attach each other's request bodies."""
    sessions = mocker.Mock()
    captured = []

    def record(scope, stage, payload):
        captured.append((scope.run_id, payload["marker"]))
        return uuid4()

    mocker.patch(
        "app.managers.run_trace.start_record",
        new_callable=mocker.AsyncMock,
        side_effect=record,
    )
    finish = mocker.patch(
        "app.managers.run_trace.finish_record", new_callable=mocker.AsyncMock
    )
    ready = asyncio.Event()
    entered = 0

    async def invoke(identity):
        nonlocal entered
        async with trace_run(sessions, identity, 1):
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            async with trace_request("research", {"marker": str(identity)}):
                await asyncio.sleep(0)

    first, second = uuid4(), uuid4()
    await asyncio.gather(invoke(first), invoke(second))
    assert set(captured) == {(first, str(first)), (second, str(second))}
    assert finish.await_count == 2
    async with trace_request("research", {"marker": "outside-scope"}):
        pass
    assert len(captured) == 2


async def test_failed_trace_storage_does_not_block_provider() -> None:
    """Observability cannot turn an otherwise successful request into failure."""

    def broken_sessions():
        message = "Database temporarily unavailable"
        raise RuntimeError(message)

    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "completed", "output": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(response)
    ) as client:
        async with trace_run(broken_sessions, uuid4(), 1):
            result = await OpenAIResearch(client, get_settings())._request(
                stage="research", input="Synthetic input"
            )
    assert result["status"] == "completed"
