"""Add typed inbox notifications without alert-specific reference columns.

Revision ID: add_notifications
Revises: add_queue_controls
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "add_notifications"
down_revision = "add_queue_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create an empty inbox table; do not notify for historical events."""
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_monitor_id", sa.Uuid()),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("deduplication_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("seen_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["scope_monitor_id", "user_id"], ["monitors.id", "monitors.user_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "deduplication_key"),
        sa.CheckConstraint("type IN ('alert.published', 'monitor.activated', 'monitor.paused')", name="valid_type"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="object_payload"),
        sa.CheckConstraint("deduplication_key ~ '[^[:space:]]'", name="nonblank_key"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at", "id"])
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "created_at"], postgresql_where=sa.text("read_at IS NULL"))
    op.create_index("ix_notifications_monitor", "notifications", ["scope_monitor_id"])


def downgrade() -> None:
    """Drop inbox state without changing monitors, alerts or evidence."""
    op.drop_table("notifications")
