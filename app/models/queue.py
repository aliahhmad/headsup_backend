"""Shared processing controls and administrator-requested queue attempts."""

from datetime import datetime
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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base
from app.models.monitor import Identity, utc_now


class QueueControl(Base):
    """One durable pause switch, shared by API and CLI workers."""

    __tablename__ = "queue_control"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    automatic_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    worker_error_code: Mapped[str | None] = mapped_column(Text)


class QueueExecution(Identity, Base):
    """One durable single-step request or nonempty automatic processing attempt."""

    __tablename__ = "queue_executions"
    __table_args__ = (
        Index("ix_queue_executions_created", "created_at", "id"),
        CheckConstraint("mode IN ('manual', 'automatic')", name="valid_mode"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'empty', 'failed', 'interrupted')",
            name="valid_status",
        ),
    )

    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    monitor_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("monitor_runs.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(Text)
    run_status: Mapped[str | None] = mapped_column(String(16))
    alert_status: Mapped[str | None] = mapped_column(String(16))
