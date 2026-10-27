"""Monitor configuration and the records required to initialize a monitor."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


def utc_now() -> datetime:
    """Return an aware timestamp when a record is created."""
    return datetime.now(timezone.utc)


class Identity:
    """Common identity and creation time for monitoring records."""

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class Monitor(Identity, Base):
    """An owned monitoring objective and its scheduling configuration."""

    __tablename__ = "monitors"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("name ~ '[^[:space:]]'", name="nonblank_name"),
        CheckConstraint(
            "objective ~ '[^[:space:]]'", name="nonblank_objective"
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')", name="valid_status"
        ),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("check_interval_seconds > 0", name="positive_interval"),
        CheckConstraint(
            "(status = 'active' AND deleted_at IS NULL "
            "AND next_run_at IS NOT NULL) OR "
            "((status <> 'active' OR deleted_at IS NOT NULL) "
            "AND next_run_at IS NULL)",
            name="schedule_matches_state",
        ),
        Index("ix_monitors_user_created", "user_id", "created_at"),
        Index(
            "ix_monitors_due",
            "next_run_at",
            postgresql_where=text("status = 'active' AND deleted_at IS NULL"),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chat(Identity, Base):
    """The persistent conversation belonging to one monitor."""

    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint(
            "summarized_through_sequence >= 0", name="nonnegative_coverage"
        ),
        CheckConstraint("summary_version >= 1", name="positive_version"),
    )

    monitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[str | None] = mapped_column(Text)
    summarized_through_sequence: Mapped[int] = mapped_column(
        BigInteger, default=0
    )
    summary_version: Mapped[int] = mapped_column(BigInteger, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class Message(Identity, Base):
    """Conversation persistence; sending messages is a later API feature."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "sequence_number", name="uq_messages_chat_sequence"
        ),
        UniqueConstraint(
            "chat_id", "client_message_id", name="uq_messages_chat_submission"
        ),
        CheckConstraint("sequence_number >= 1", name="positive_sequence"),
        CheckConstraint("role IN ('user', 'assistant')", name="valid_role"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')", name="valid_status"
        ),
        CheckConstraint(
            "status <> 'completed' OR content ~ '[^[:space:]]'",
            name="completed_content",
        ),
    )

    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE")
    )
    sequence_number: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    client_message_id: Mapped[UUID | None] = mapped_column(Uuid)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class Memory(Identity, Base):
    """Current user profile, task context, or accumulated monitor knowledge."""

    __tablename__ = "memories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["monitor_id", "user_id"],
            ["monitors.id", "monitors.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("monitor_id", "kind"),
        CheckConstraint(
            "(kind = 'user_profile' AND monitor_id IS NULL) OR "
            "(kind IN ('monitor_context', 'monitor_knowledge') "
            "AND monitor_id IS NOT NULL)",
            name="valid_scope",
        ),
        CheckConstraint("version >= 1", name="positive_version"),
        Index(
            "uq_memories_user_profile",
            "user_id",
            unique=True,
            postgresql_where=text("kind = 'user_profile'"),
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    monitor_id: Mapped[UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )


class MonitorRun(Identity, Base):
    """A durable check request claimed and completed by the worker."""

    __tablename__ = "monitor_runs"
    __table_args__ = (
        UniqueConstraint("id", "monitor_id"),
        UniqueConstraint("monitor_id", "idempotency_key"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', "
            "'failed', 'cancelled')",
            name="valid_status",
        ),
        CheckConstraint(
            "trigger IN ('initial', 'scheduled', 'manual')",
            name="valid_trigger",
        ),
        CheckConstraint("monitor_version >= 1", name="positive_version"),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
        Index("ix_monitor_runs_monitor_created", "monitor_id", "created_at"),
        Index(
            "uq_monitor_runs_in_flight",
            "monitor_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    monitor_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(16), default="queued")
    trigger: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    monitor_version: Mapped[int] = mapped_column(BigInteger)
    objective_snapshot: Mapped[str] = mapped_column(Text)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(120))


class Observation(Identity, Base):
    """One immutable development discovered during a check."""

    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("monitor_run_id", "item_key"),
        UniqueConstraint("id", "monitor_run_id"),
        CheckConstraint("summary ~ '[^[:space:]]'", name="nonblank_summary"),
    )

    monitor_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitor_runs.id", ondelete="CASCADE"), index=True
    )
    item_key: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)


class ObservationSource(Identity, Base):
    """Evidence supporting one observation, retained with that finding."""

    __tablename__ = "observation_sources"
    __table_args__ = (UniqueConstraint("observation_id", "source_key"),)

    observation_id: Mapped[UUID] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE")
    )
    source_key: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_excerpt: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)


class MemoryRevision(Identity, Base):
    """A complete accepted memory snapshot with optional source provenance."""

    __tablename__ = "memory_revisions"
    __table_args__ = (
        UniqueConstraint("memory_id", "version"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint(
            "source_kind IN ('initialization', 'user_edit', 'message', 'run')",
            name="valid_source_kind",
        ),
        CheckConstraint(
            "(source_message_id IS NULL OR source_kind = 'message') AND "
            "(source_run_id IS NULL OR source_kind = 'run')",
            name="source_matches_kind",
        ),
    )

    memory_id: Mapped[UUID] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str] = mapped_column(Text)
    change_reason: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(16))
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("monitor_runs.id", ondelete="SET NULL")
    )
