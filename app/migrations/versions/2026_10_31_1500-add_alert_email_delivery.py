"""Add opt-in email preferences and a durable delivery outbox.

Revision ID: add_alert_email_delivery
Revises: add_notifications
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "add_alert_email_delivery"
down_revision = "add_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep existing accounts opted out and do not replay historical alerts."""
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("email_alerts_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notification_id", sa.Uuid(), sa.ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("monitor_snapshot", JSONB(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("recipient_email", sa.Text()),
        sa.Column("message_id", sa.Text()),
        sa.Column("error_code", sa.Text()),
        sa.Column("reason_code", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("notification_id", "channel"),
        sa.CheckConstraint("channel = 'email'", name="valid_channel"),
        sa.CheckConstraint("status IN ('pending', 'processing', 'sending', 'sent', 'skipped', 'failed', 'uncertain')", name="valid_status"),
        sa.CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
        sa.CheckConstraint("((status IN ('processing', 'sending')) AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR ((status NOT IN ('processing', 'sending')) AND lease_token IS NULL AND lease_expires_at IS NULL)", name="lease_matches_state"),
    )
    op.create_index("ix_notification_deliveries_due", "notification_deliveries", ["status", "next_attempt_at"])


def downgrade() -> None:
    """Remove delivery/preferences without deleting inbox events or alerts."""
    op.drop_table("notification_deliveries")
    op.drop_table("notification_preferences")
