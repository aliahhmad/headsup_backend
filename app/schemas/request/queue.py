"""Administrator controls for continuous processing."""

from pydantic import BaseModel, ConfigDict, StrictBool


class QueueSettingsUpdate(BaseModel):
    """A saved switch, not a new monitor schedule."""

    model_config = ConfigDict(extra="forbid")
    automatic_enabled: StrictBool
