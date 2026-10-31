"""Validated administrator filters for execution history."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator


class RunFilters(BaseModel):
    """Apply the same scope to the directory and summary counts."""

    q: str = Field(default="", max_length=256)
    status: (
        Literal["queued", "running", "succeeded", "failed", "cancelled"] | None
    ) = None
    trigger: Literal["initial", "manual", "scheduled"] | None = None
    alert_status: (
        Literal[
            "pending",
            "processing",
            "published",
            "skipped",
            "failed",
            "cancelled",
            "not_evaluated",
        ]
        | None
    ) = None
    user_id: int | None = Field(default=None, ge=1)
    monitor_id: UUID | None = None
    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None
    order: Literal["newest", "oldest"] = "newest"

    @model_validator(mode="after")
    def validate_dates(self) -> "RunFilters":
        """Reject inverted windows rather than silently showing an empty page."""
        if (
            self.created_from
            and self.created_to
            and self.created_from > self.created_to
        ):
            message = "created_from must precede created_to"
            raise ValueError(message)
        return self
