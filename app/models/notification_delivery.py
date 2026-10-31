"""Recipient preferences and durable delivery attempts, separate from read state."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.models.monitor import Identity, utc_now


class NotificationPreferences(Base):
    """One explicit opt-in per account; absent rows also mean disabled."""

    __tablename__ = "notification_preferences"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    email_alerts_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class NotificationDelivery(Identity, Base):
    """One email per notification, with retries only for known non-delivery."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("notification_id", "channel"),
        CheckConstraint("channel = 'email'", name="valid_channel"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'sending', 'sent', 'skipped', 'failed', 'uncertain')",
            name="valid_status",
        ),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
        CheckConstraint(
            "((status IN ('processing', 'sending')) AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR ((status NOT IN ('processing', 'sending')) AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="lease_matches_state",
        ),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
    )
    notification_id: Mapped[UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(16), default="email")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    monitor_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    recipient_email: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
