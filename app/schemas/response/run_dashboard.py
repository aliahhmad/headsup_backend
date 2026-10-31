"""Explicit administrator projections; never serialize account credentials."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import RoleType

from app.schemas.response.monitor import (
    AlertDetailResponse,
    MonitorResponse,
    MonitorRunResponse,
    ObservationResponse,
)


class RunAccount(BaseModel):
    """The current account associated with the parent monitor."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    first_name: str
    last_name: str
    role: RoleType
    verified: bool
    banned: bool


class AdminRunSummary(BaseModel):
    """Directory rows are centered on execution, not monitor configuration."""

    id: UUID
    monitor_id: UUID
    monitor_name: str
    monitor_status: str
    monitor_deleted_at: datetime | None
    owner: RunAccount
    status: str
    trigger: str
    attempt_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    alert_status: str | None
    alert_error_code: str | None
    observation_count: int
    source_count: int
    alert_count: int
    trace_count: int
    latest_stage: str | None
    latest_request_status: str | None
    elapsed_seconds: float | None
    queue_seconds: float | None


class ModelCallResponse(BaseModel):
    """Captured request body and safe response metadata for one actual call."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    stage: str
    attempt_number: int
    model: str
    status: str
    request_payload: dict[str, Any]
    response_metadata: dict[str, Any] | None
    http_status: int | None
    error_code: str | None
    duration_ms: int | None
    created_at: datetime
    completed_at: datetime | None


class RunCandidateResponse(BaseModel):
    """Include positive/negative decisions and their publication disposition."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    observation_id: UUID
    observation_ref: str
    should_alert: bool
    what_changed: str
    why_it_matters: str
    development_key: str | None
    status: str


class RunJobResponse(BaseModel):
    """Composition state and accepted post-merge inputs, including active leases."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    monitor_version: int
    input_snapshot: dict[str, Any]
    attempt_count: int
    next_attempt_at: datetime
    lease_expires_at: datetime | None
    completed_at: datetime | None
    reason_code: str | None
    error_code: str | None


class AdminRunDetail(BaseModel):
    """Historical snapshots, current state and outputs are explicitly separated."""

    run: MonitorRunResponse
    idempotency_key: str
    monitor: MonitorResponse
    monitor_deleted_at: datetime | None
    owner: RunAccount
    snapshot_phase: str
    observations: list[ObservationResponse]
    memories_used: list[dict[str, Any]]
    memory_changes: list[dict[str, Any]]
    current_memories: list[dict[str, Any]]
    context_policy: dict[str, Any]
    alerts: list[AlertDetailResponse]
    candidates: list[RunCandidateResponse]
    alert_job: RunJobResponse | None
    model_calls: list[ModelCallResponse]
