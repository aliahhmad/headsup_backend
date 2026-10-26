"""Initialize monitor configuration and its workspace records.

Revision ID: add_monitor_foundation
Revises: add_registration_settings
Create Date: 2026-10-25 20:57:40.333079

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "add_monitor_foundation"
down_revision: str | None = "add_registration_settings"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add monitor tables without altering existing accounts or API keys."""
    op.create_table(
        "monitors",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'active' AND deleted_at IS NULL AND next_run_at IS NOT NULL) OR ((status <> 'active' OR deleted_at IS NOT NULL) AND next_run_at IS NULL)",
            name=op.f("ck_monitors_schedule_matches_state"),
        ),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f("ck_monitors_nonblank_name")
        ),
        sa.CheckConstraint(
            "objective ~ '[^[:space:]]'",
            name=op.f("ck_monitors_nonblank_objective"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name=op.f("ck_monitors_valid_status"),
        ),
        sa.CheckConstraint(
            "check_interval_seconds > 0",
            name=op.f("ck_monitors_positive_interval"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_monitors_positive_version")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_monitors_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitors")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_monitors_id")),
    )
    op.create_index(
        "ix_monitors_due",
        "monitors",
        ["next_run_at"],
        unique=False,
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_monitors_user_created",
        "monitors",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "chats",
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "summarized_through_sequence", sa.BigInteger(), nullable=False
        ),
        sa.Column("summary_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "summarized_through_sequence >= 0",
            name=op.f("ck_chats_nonnegative_coverage"),
        ),
        sa.CheckConstraint(
            "summary_version >= 1", name=op.f("ck_chats_positive_version")
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_chats_monitor_id_monitors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chats")),
        sa.UniqueConstraint("monitor_id", name=op.f("uq_chats_monitor_id")),
    )
    op.create_table(
        "memories",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'user_profile' AND monitor_id IS NULL) OR (kind IN ('monitor_context', 'monitor_knowledge') AND monitor_id IS NOT NULL)",
            name=op.f("ck_memories_valid_scope"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_memories_positive_version")
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id", "user_id"],
            ["monitors.id", "monitors.user_id"],
            name=op.f("fk_memories_monitor_id_monitors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memories_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
        sa.UniqueConstraint(
            "monitor_id", "kind", name=op.f("uq_memories_monitor_id")
        ),
    )
    op.create_index(
        "uq_memories_user_profile",
        "memories",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'user_profile'"),
    )
    op.create_table(
        "monitor_runs",
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("monitor_version", sa.BigInteger(), nullable=False),
        sa.Column("objective_snapshot", sa.Text(), nullable=False),
        sa.Column(
            "context_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_monitor_runs_valid_status"),
        ),
        sa.CheckConstraint(
            "trigger IN ('initial', 'scheduled', 'manual')",
            name=op.f("ck_monitor_runs_valid_trigger"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_monitor_runs_nonnegative_attempts"),
        ),
        sa.CheckConstraint(
            "monitor_version >= 1",
            name=op.f("ck_monitor_runs_positive_version"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            name=op.f("fk_monitor_runs_monitor_id_monitors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitor_runs")),
        sa.UniqueConstraint(
            "id", "monitor_id", name=op.f("uq_monitor_runs_id")
        ),
        sa.UniqueConstraint(
            "monitor_id",
            "idempotency_key",
            name=op.f("uq_monitor_runs_monitor_id"),
        ),
    )
    op.create_index(
        "ix_monitor_runs_monitor_created",
        "monitor_runs",
        ["monitor_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_monitor_runs_in_flight",
        "monitor_runs",
        ["monitor_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "messages",
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("client_message_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name=op.f("ck_messages_valid_role")
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR content ~ '[^[:space:]]'",
            name=op.f("ck_messages_completed_content"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name=op.f("ck_messages_valid_status"),
        ),
        sa.CheckConstraint(
            "sequence_number >= 1", name=op.f("ck_messages_positive_sequence")
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            name=op.f("fk_messages_chat_id_chats"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint(
            "chat_id",
            "client_message_id",
            name=op.f("uq_messages_chat_submission"),
        ),
        sa.UniqueConstraint(
            "chat_id", "sequence_number", name=op.f("uq_messages_chat_sequence")
        ),
    )
    op.create_table(
        "memory_revisions",
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(source_message_id IS NULL OR source_kind = 'message') AND (source_run_id IS NULL OR source_kind = 'run')",
            name=op.f("ck_memory_revisions_source_matches_kind"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('initialization', 'user_edit', 'message', 'run')",
            name=op.f("ck_memory_revisions_valid_source_kind"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_memory_revisions_positive_version")
        ),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["memories.id"],
            name=op.f("fk_memory_revisions_memory_id_memories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            name=op.f("fk_memory_revisions_source_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["monitor_runs.id"],
            name=op.f("fk_memory_revisions_source_run_id_monitor_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_revisions")),
        sa.UniqueConstraint(
            "memory_id", "version", name=op.f("uq_memory_revisions_memory_id")
        ),
    )


def downgrade() -> None:
    """Remove monitor records while preserving existing account tables."""
    op.drop_table("memory_revisions")
    op.drop_table("messages")
    op.drop_index(
        "uq_monitor_runs_in_flight",
        table_name="monitor_runs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index("ix_monitor_runs_monitor_created", table_name="monitor_runs")
    op.drop_table("monitor_runs")
    op.drop_index(
        "uq_memories_user_profile",
        table_name="memories",
        postgresql_where=sa.text("kind = 'user_profile'"),
    )
    op.drop_table("memories")
    op.drop_table("chats")
    op.drop_index("ix_monitors_user_created", table_name="monitors")
    op.drop_index(
        "ix_monitors_due",
        table_name="monitors",
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )
    op.drop_table("monitors")
