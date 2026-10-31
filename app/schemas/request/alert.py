"""Idempotent user-state changes; published content is not editable."""

from pydantic import BaseModel, ConfigDict, model_validator


class AlertFilters(BaseModel):
    """Query filters alongside the existing pagination parameters."""

    unread: bool = False
    include_dismissed: bool = False


class AlertStateUpdate(BaseModel):
    """Allow independently setting or clearing read and dismissed state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    read: bool | None = None
    dismissed: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> "AlertStateUpdate":
        """Require at least one explicit boolean change."""
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            message = "Supply read or dismissed as a boolean"
            raise ValueError(message)
        return self
