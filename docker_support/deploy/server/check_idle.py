"""Reject an update while research or email work could be interrupted."""

import asyncio

from sqlalchemy import func, select

from app.database.db import async_engine, async_session
from app.models.monitor import MonitorRun
from app.models.notification_delivery import NotificationDelivery
from app.models.queue import QueueControl, QueueExecution


async def main() -> None:
    async with async_session() as session:
        control = await session.get(QueueControl, 1)
        if control and control.automatic_enabled:
            raise SystemExit(
                "Turn automatic processing off in Administration → Queue before updating."
            )
        checks = (
            (
                QueueExecution,
                QueueExecution.status.in_(["pending", "processing"]),
                "Wait for the current queue request to finish.",
            ),
            (
                MonitorRun,
                MonitorRun.status == "running",
                "A research run is still marked running; inspect it before updating.",
            ),
            (
                NotificationDelivery,
                NotificationDelivery.status.in_(
                    ["pending", "processing", "sending"]
                ),
                "Wait for alert emails to finish, or cancel waiting emails through the profile opt-out.",
            ),
        )
        for model, predicate, message in checks:
            count = await session.scalar(
                select(func.count()).select_from(model).where(predicate)
            )
            if count:
                raise SystemExit(message)
    await async_engine.dispose()
    print("Research and email work are idle.")


if __name__ == "__main__":
    asyncio.run(main())
