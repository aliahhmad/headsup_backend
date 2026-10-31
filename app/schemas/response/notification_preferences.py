"""Uncached recipient-owned email settings."""

from datetime import datetime

from pydantic import BaseModel


class NotificationPreferencesResponse(BaseModel):
    """Missing saved preferences use disabled defaults."""

    email_alerts_enabled: bool
    updated_at: datetime | None
