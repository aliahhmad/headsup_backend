"""Recurring scheduling must recover from failures and stop with the API."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.managers import monitor_scheduler as scheduler

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_scheduler_service_cancels_its_background_loop(
    monkeypatch,
) -> None:
    """Stopping the API cannot leave an enqueueing task behind."""
    started, stopped = asyncio.Event(), asyncio.Event()

    async def serve(sessions) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(scheduler, "serve_scheduler", serve)
    async with scheduler.monitor_scheduler_service(AsyncMock()):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not stopped.is_set()
    assert stopped.is_set()


async def test_scheduler_retries_after_database_failure_without_logging_content(
    monkeypatch,
) -> None:
    """Database errors must not permanently disable future scheduling."""
    sessions, session = MagicMock(), MagicMock()
    sessions.return_value.__aenter__ = AsyncMock(return_value=session)
    sessions.return_value.__aexit__ = AsyncMock(return_value=False)
    transaction = AsyncMock()
    transaction.__aexit__.return_value = False
    session.begin.return_value = transaction
    enqueue = AsyncMock(side_effect=[RuntimeError("PRIVATE-CONTENT"), 0])
    log = MagicMock()

    async def finish_after_retry(seconds) -> None:
        if enqueue.await_count > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "queue_due_monitors", enqueue)
    monkeypatch.setattr(scheduler.logger, "error", log)
    monkeypatch.setattr(scheduler.asyncio, "sleep", finish_after_retry)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.serve_scheduler(sessions)
    assert enqueue.await_count == 2  # noqa: PLR2004
    assert transaction.__aexit__.await_args_list[0].args[0] is RuntimeError
    assert transaction.__aexit__.await_args_list[1].args[0] is None
    log.assert_called_once_with(
        "Monitor scheduler unavailable ({})", "RuntimeError"
    )
