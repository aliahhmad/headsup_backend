"""Account inbox projections with exact type/payload discrimination."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter

from app.schemas.notification import (
    AlertPublishedEvent,
    MonitorActivatedEvent,
    MonitorPausedEvent,
)


class NotificationDisplay(BaseModel):
    """Content and navigation are resolved from currently accessible owned data."""

    id: UUID
    created_at: datetime
    read_at: datetime | None
    seen_at: datetime | None
    title: str
    message: str
    target: str


class AlertNotificationResponse(NotificationDisplay, AlertPublishedEvent):
    """An alert publication with its rendered inbox preview."""


class ActivatedNotificationResponse(NotificationDisplay, MonitorActivatedEvent):
    """An activation with its rendered inbox preview."""


class PausedNotificationResponse(NotificationDisplay, MonitorPausedEvent):
    """A pause event with its rendered inbox preview."""


NotificationResponse = Annotated[
    AlertNotificationResponse
    | ActivatedNotificationResponse
    | PausedNotificationResponse,
    Field(discriminator="type"),
]
notification_response: TypeAdapter[NotificationResponse] = TypeAdapter(
    NotificationResponse
)


class NotificationCounts(BaseModel):
    """Unread and unseen are distinct; neither is a delivery status."""

    unread: int
    unseen: int
    total: int
