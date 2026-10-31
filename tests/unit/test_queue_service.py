"""Background service lifecycle must not leak tasks or imply work was enabled."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.managers import queue_control as queue

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_queue_service_starts_and_cancels_its_task(monkeypatch):
    """The API owns and cleans up its managed queue service."""
    started, stopped = asyncio.Event(), asyncio.Event()

    async def serve(sessions):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(queue, "serve_queue", serve)
    async with queue.queue_service(AsyncMock()):
        await asyncio.wait_for(started.wait(), 1)
        assert not stopped.is_set()
    assert stopped.is_set()
