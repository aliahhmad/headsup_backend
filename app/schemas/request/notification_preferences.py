"""An account can change only its own alert email preference."""

from pydantic import BaseModel, ConfigDict, StrictBool


class NotificationPreferencesUpdate(BaseModel):
    """Explicit boolean only; no recipient or account overrides."""

    model_config = ConfigDict(extra="forbid")
    email_alerts_enabled: StrictBool
