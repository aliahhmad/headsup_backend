"""Add durable queue processing controls and execution audit records.

Revision ID: add_queue_controls
Revises: add_run_model_calls
"""

import sqlalchemy as sa
from alembic import op

revision = "add_queue_controls"
down_revision = "add_run_model_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Start with automatic processing disabled; preserve all existing runs."""
    op.create_table(
        "queue_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("automatic_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("worker_error_code", sa.Text()),
        sa.CheckConstraint("id = 1", name="singleton"),
    )
    op.create_table(
        "queue_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("monitor_run_id", sa.Uuid(), sa.ForeignKey("monitor_runs.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.Text()),
        sa.Column("run_status", sa.String(16)),
        sa.Column("alert_status", sa.String(16)),
        sa.CheckConstraint("mode IN ('manual', 'automatic')", name="valid_mode"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'completed', 'empty', 'failed', 'interrupted')", name="valid_status"),
    )
    op.create_index("ix_queue_executions_status", "queue_executions", ["status"])
    op.create_index("ix_queue_executions_created", "queue_executions", ["created_at", "id"])
    op.execute("INSERT INTO queue_control (id, automatic_enabled) VALUES (1, false)")


def downgrade() -> None:
    """Remove controller state without modifying research or alert records."""
    op.drop_table("queue_executions")
    op.drop_table("queue_control")
