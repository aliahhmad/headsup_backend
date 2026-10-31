"""Shared lifecycle and memory checks for research and alert publication."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitor import Memory, Monitor, MonitorRun


def can_execute(monitor: Monitor, run: MonitorRun) -> bool:
    """Paused monitors permit manual checks; archived/deleted ones do not."""
    return monitor.deleted_at is None and (
        monitor.status == "active"
        or (monitor.status == "paused" and run.trigger == "manual")
    )


async def memory_snapshot(
    monitor: Monitor, session: AsyncSession
) -> dict[str, Any]:
    """Read owned memory consistently while the parent monitor is locked."""
    memories = await session.scalars(
        select(Memory)
        .where(
            Memory.monitor_id == monitor.id, Memory.user_id == monitor.user_id
        )
        .order_by(Memory.id)
        .with_for_update()
    )
    return {
        "memories": [
            {
                "memory_id": str(memory.id),
                "kind": memory.kind,
                "version": memory.version,
                "content": memory.content,
            }
            for memory in memories
        ]
    }
