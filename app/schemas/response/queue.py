"""Safe queue state projections for administrators."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class QueueExecutionResponse(BaseModel):
    """An execution attempt can finish even when its research fails."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    mode: str
    status: str
    requested_by_user_id: int | None
    monitor_run_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    run_status: str | None
    alert_status: str | None


class QueueStateResponse(BaseModel):
    """Processing policy, worker liveness, outstanding work and recent attempts."""

    automatic_enabled: bool
    provider_configured: bool
    updated_at: datetime | None
    updated_by_user_id: int | None
    heartbeat_at: datetime | None
    worker_online: bool
    worker_error_code: str | None
    queued_research: int
    running_research: int
    due_alerts: int
    waiting_alerts: int
    processing_alerts: int
    outstanding: QueueExecutionResponse | None
    recent: list[QueueExecutionResponse]
