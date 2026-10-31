"""Capture run requests independently of research/publication transactions."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from app.logs import logger
from app.models.monitor import RunModelCall, utc_now

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True)
class TraceScope:
    """Task-local association prevents crossing accounts or concurrent runs."""

    sessions: async_sessionmaker[AsyncSession]
    run_id: UUID
    attempt_number: int


_scope: ContextVar[TraceScope | None] = ContextVar("run_trace", default=None)


@asynccontextmanager
async def trace_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
    attempt_number: int,
) -> AsyncIterator[None]:
    """Associate provider calls with this execution attempt only."""
    token = _scope.set(TraceScope(sessions, run_id, attempt_number))
    try:
        yield
    finally:
        _scope.reset(token)


@dataclass
class ResponseRecord:
    """Keep safe response identifiers, status and usage, never response bodies."""

    http_status: int | None = None
    metadata: dict[str, Any] | None = None

    def capture(
        self, http_status: int, data: dict[str, Any], request_id: str | None
    ) -> None:
        """Extract diagnostic metadata without provider text or headers."""
        self.http_status = http_status
        self.metadata = {
            "response_id": data.get("id"),
            "request_id": request_id,
            "provider_status": data.get("status"),
            "model": data.get("model"),
            "usage": data.get("usage"),
            "output_types": dict(
                Counter(
                    item.get("type", "unknown")
                    for item in (data.get("output") or [])
                    if isinstance(item, dict)
                )
            ),
        }


async def start_record(
    scope: TraceScope, stage: str, payload: dict[str, Any]
) -> UUID | None:
    """Trace storage is best effort and cannot invalidate successful research."""
    record_id = uuid4()
    try:
        async with scope.sessions() as session, session.begin():
            session.add(
                RunModelCall(
                    id=record_id,
                    monitor_run_id=scope.run_id,
                    stage=stage,
                    attempt_number=scope.attempt_number,
                    model=str(payload.get("model", "unknown")),
                    request_payload=payload,
                    status="running",
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not start request trace ({})", type(exc).__name__)
        return None
    return record_id


async def finish_record(
    scope: TraceScope,
    record_id: UUID,
    response: ResponseRecord,
    outcome: dict[str, Any],
) -> None:
    """Commit trace completion without persisting arbitrary exception messages."""
    try:
        async with scope.sessions() as session, session.begin():
            record = await session.get(RunModelCall, record_id)
            if record is not None:
                record.status = outcome["status"]
                record.error_code = outcome["error_code"]
                record.duration_ms = outcome["duration_ms"]
                record.completed_at = utc_now()
                record.http_status = response.http_status
                record.response_metadata = response.metadata
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not finish request trace ({})", type(exc).__name__
        )


@asynccontextmanager
async def trace_request(
    stage: str, payload: dict[str, Any]
) -> AsyncIterator[ResponseRecord]:
    """Record the exact JSON request body; authentication headers never enter."""
    scope = _scope.get()
    response = ResponseRecord()
    record_id = await start_record(scope, stage, payload) if scope else None
    started = perf_counter()
    status, error_code = "succeeded", None
    try:
        yield response
        if (
            response.metadata
            and response.metadata.get("provider_status") == "incomplete"
        ):
            status, error_code = "failed", "openai_incomplete"
    except BaseException as exc:
        status = (
            "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        )
        candidate = str(exc.args[0]) if exc.args else ""
        error_code = (
            candidate
            if re.fullmatch(r"openai_[a-z0-9_]+", candidate)
            else "request_interrupted"
        )
        raise
    finally:
        if scope is not None and record_id is not None:
            await asyncio.shield(
                finish_record(
                    scope,
                    record_id,
                    response,
                    {
                        "status": status,
                        "error_code": error_code,
                        "duration_ms": max(
                            0, round((perf_counter() - started) * 1000)
                        ),
                    },
                )
            )
