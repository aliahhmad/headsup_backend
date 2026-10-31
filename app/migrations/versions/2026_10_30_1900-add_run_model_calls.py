"""Add administrator-visible request traces for future monitor executions.

Revision ID: add_run_model_calls
Revises: add_alert_digests
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "add_run_model_calls"
down_revision = "add_alert_digests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create independently committed request diagnostics, scoped to a run."""
    op.create_table(
        "run_model_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_payload", JSONB(), nullable=False),
        sa.Column("response_metadata", JSONB(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_model_calls")),
        sa.ForeignKeyConstraint(
            ["monitor_run_id"],
            ["monitor_runs.id"],
            ondelete="CASCADE",
            name=op.f("fk_run_model_calls_monitor_run_id_monitor_runs"),
        ),
        sa.CheckConstraint(
            "stage IN ('research', 'extraction', 'knowledge', 'composition')",
            name=op.f("ck_run_model_calls_valid_stage"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_run_model_calls_valid_status"),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name=op.f("ck_run_model_calls_positive_attempt"),
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name=op.f("ck_run_model_calls_nonnegative_duration"),
        ),
    )
    op.create_index(
        "ix_run_model_calls_run_created",
        "run_model_calls",
        ["monitor_run_id", "created_at"],
    )


def downgrade() -> None:
    """Remove request diagnostics without changing research outputs."""
    op.drop_table("run_model_calls")
