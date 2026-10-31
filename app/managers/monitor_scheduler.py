"""Queue due monitor checks independently of paid-work processing controls."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from app.logs import logger
from app.managers.monitor import queue_due_monitors

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

POLL_SECONDS = 5


async def serve_scheduler(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Poll persisted due times; database locks coordinate API replicas."""
    while True:
        try:
            async with sessions() as session, session.begin():
                await queue_due_monitors(session)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Monitor scheduler unavailable ({})", type(exc).__name__
            )
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def monitor_scheduler_service(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    """Own the scheduling loop and roll back interrupted ticks."""
    task = asyncio.create_task(serve_scheduler(sessions))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
