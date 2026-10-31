"""Inbox filters and acknowledgement, never client-created notifications."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.schemas.notification import NotificationType


class NotificationFilters(BaseModel):
    """List newest first, optionally by exact event type or unread state."""

    unread: bool = False
    type: NotificationType | None = None


class NotificationStateUpdate(BaseModel):
    """Mark read/unread or seen while retaining the event in history."""

    model_config = ConfigDict(extra="forbid")
    read: StrictBool | None = None
    seen: StrictBool | None = None

    @model_validator(mode="after")
    def valid_change(self) -> NotificationStateUpdate:
        """Require a meaningful state change; seen acknowledgements are one-way."""
        if (
            not self.model_fields_set
            or any(
                getattr(self, field) is None for field in self.model_fields_set
            )
            or self.seen is False
        ):
            raise ValueError(
                "Supply read=true/false or seen=true; null states are not allowed"
            )
        return self


class NotificationsSeen(BaseModel):
    """Only items rendered in the inbox should be acknowledged as seen."""

    model_config = ConfigDict(extra="forbid")
    ids: Annotated[list[UUID], Field(min_length=1, max_length=50)]
