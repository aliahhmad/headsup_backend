"""Validated monitor configuration and manual check requests."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MonitorStatus = Literal["active", "paused", "archived"]


class MonitorCreate(BaseModel):
    """Create an active monitor, with optional user-supplied task context."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=10000)
    check_interval_seconds: int = Field(
        default=86400, gt=0, le=31536000, strict=True
    )
    monitor_context: str = Field(default="", max_length=20000)


class MonitorUpdate(BaseModel):
    """Change configuration only if the client has its current version."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    objective: str | None = Field(default=None, min_length=1, max_length=10000)
    status: MonitorStatus | None = None
    check_interval_seconds: int | None = Field(
        default=None, gt=0, le=31536000, strict=True
    )

    @model_validator(mode="after")
    def validate_changes(self) -> "MonitorUpdate":
        """Reject empty edits and explicit nulls for required configuration."""
        changes = self.model_dump(
            exclude_unset=True, exclude={"expected_version"}
        )
        if not changes or any(value is None for value in changes.values()):
            message = "Provide at least one non-null configuration field."
            raise ValueError(message)
        return self


class MonitorCheckRequest(BaseModel):
    """A client identifier reused when retrying the same Check now action."""

    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
