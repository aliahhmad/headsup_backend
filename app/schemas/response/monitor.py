"""Monitor configuration, initialized workspace, and queued check responses."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.request.monitor import MonitorStatus


class MonitorResponse(BaseModel):
    """Public fields of an owned, non-deleted monitor."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: int
    name: str
    objective: str
    status: MonitorStatus
    version: int
    check_interval_seconds: int
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MonitorChatResponse(BaseModel):
    """The initialized conversation; chat messaging is not available yet."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    summary: str | None
    summarized_through_sequence: int
    summary_version: int


class MonitorMemoryResponse(BaseModel):
    """Current task memory without loading historical revisions."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: Literal["monitor_context", "monitor_knowledge"]
    content: str
    version: int
    updated_at: datetime


class MonitorRunResponse(BaseModel):
    """Saved check state; queued does not imply that research has executed."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    monitor_id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    trigger: Literal["initial", "scheduled", "manual"]
    scheduled_for: datetime | None
    monitor_version: int
    objective_snapshot: str
    context_snapshot: dict[str, Any]
    attempt_count: int
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    created_at: datetime


class MonitorDetailResponse(MonitorResponse):
    """A monitor and the records created with it in one transaction."""

    chat: MonitorChatResponse
    memories: list[MonitorMemoryResponse]
    latest_run: MonitorRunResponse | None


class ObservationSourceResponse(BaseModel):
    """Source metadata belonging to a saved observation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    observation_id: UUID
    source_key: str
    source_url: str
    source_title: str | None
    source_published_at: datetime | None
    retrieved_at: datetime
    evidence_excerpt: str | None
    content_hash: str | None
    created_at: datetime


class ObservationResponse(BaseModel):
    """A consolidated finding with its supporting evidence."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    monitor_run_id: UUID
    item_key: str
    summary: str
    created_at: datetime
    sources: list[ObservationSourceResponse]


class MonitorRunDetailResponse(MonitorRunResponse):
    """Execution state and evidence-backed findings from this run."""

    observations: list[ObservationResponse]
