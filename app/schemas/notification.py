"""Discriminated event shapes: each exact type owns its payload requirements."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

NotificationType = Literal[
    "alert.published", "monitor.activated", "monitor.paused"
]


class EventPayload(BaseModel):
    """Reject extra fields instead of accepting ambiguous event data."""

    model_config = ConfigDict(extra="forbid")


class AlertPublishedPayload(EventPayload):
    """The linked alert supplies digest content and evidence."""

    alert_id: UUID


class MonitorActivatedPayload(EventPayload):
    """Version identifies a particular activation, including initial creation."""

    monitor_id: UUID
    monitor_version: Annotated[int, Field(strict=True, ge=1)]
    previous_status: Literal["new", "paused", "archived"]


class MonitorPausedPayload(EventPayload):
    """A real transition to paused, not a repeated save."""

    monitor_id: UUID
    monitor_version: Annotated[int, Field(strict=True, ge=1)]
    previous_status: Literal["active", "archived"]


class AlertPublishedEvent(EventPayload):
    """An alert has actually been published, not merely proposed."""

    type: Literal["alert.published"] = "alert.published"
    payload: AlertPublishedPayload


class MonitorActivatedEvent(EventPayload):
    """A monitor was created active or reactivated."""

    type: Literal["monitor.activated"] = "monitor.activated"
    payload: MonitorActivatedPayload


class MonitorPausedEvent(EventPayload):
    """A monitor transitioned to paused."""

    type: Literal["monitor.paused"] = "monitor.paused"
    payload: MonitorPausedPayload


NotificationEvent = Annotated[
    AlertPublishedEvent | MonitorActivatedEvent | MonitorPausedEvent,
    Field(discriminator="type"),
]
notification_event: TypeAdapter[NotificationEvent] = TypeAdapter(
    NotificationEvent
)
