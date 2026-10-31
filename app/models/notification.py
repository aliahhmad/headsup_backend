"""Typed event notifications with generic payloads and owned lifecycle scope."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.models.monitor import Identity


class Notification(Identity, Base):
    """An account inbox event; alert identifiers live only in typed JSON payloads."""

    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_monitor_id", "user_id"],
            ["monitors.id", "monitors.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "deduplication_key"),
        CheckConstraint(
            "type IN ('alert.published', 'monitor.activated', 'monitor.paused')",
            name="valid_type",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="object_payload"
        ),
        CheckConstraint(
            "deduplication_key ~ '[^[:space:]]'", name="nonblank_key"
        ),
        Index("ix_notifications_user_created", "user_id", "created_at", "id"),
        Index(
            "ix_notifications_user_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_notifications_monitor", "scope_monitor_id"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    # Ownership/deletion scope, not an event-specific reference. Future account-
    # scoped notification types may leave it null; all three current types set it.
    scope_monitor_id: Mapped[UUID | None] = mapped_column(Uuid)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    deduplication_key: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
