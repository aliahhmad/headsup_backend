"""Add alert persistence without generating or delivering alerts.

Revision ID: add_alerts
Revises: add_observation_evidence
"""

import sqlalchemy as sa
from alembic import op

revision = "add_alerts"
down_revision = "add_observation_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add sourced alerts with same-monitor provenance and deduplication."""
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("deduplication_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.ForeignKeyConstraint(
            ["monitor_id"],
            ["monitors.id"],
            ondelete="CASCADE",
            name=op.f("fk_alerts_monitor_id_monitors"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_run_id", "monitor_id"],
            ["monitor_runs.id", "monitor_runs.monitor_id"],
            ondelete="CASCADE",
            name=op.f("fk_alerts_monitor_run_id_monitor_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "monitor_run_id"],
            ["observations.id", "observations.monitor_run_id"],
            ondelete="CASCADE",
            name=op.f("fk_alerts_observation_id_observations"),
        ),
        sa.UniqueConstraint(
            "observation_id", name=op.f("uq_alerts_observation_id")
        ),
        sa.UniqueConstraint(
            "monitor_id", "deduplication_key", name=op.f("uq_alerts_monitor_id")
        ),
        sa.CheckConstraint(
            "title ~ '[^[:space:]]'", name=op.f("ck_alerts_nonblank_title")
        ),
        sa.CheckConstraint(
            "body ~ '[^[:space:]]'", name=op.f("ck_alerts_nonblank_body")
        ),
        sa.CheckConstraint(
            "deduplication_key ~ '[^[:space:]]'",
            name=op.f("ck_alerts_nonblank_deduplication_key"),
        ),
    )
    op.create_index(
        "ix_alerts_monitor_created", "alerts", ["monitor_id", "created_at"]
    )
    op.create_index("ix_alerts_monitor_run_id", "alerts", ["monitor_run_id"])


def downgrade() -> None:
    """Remove alert persistence."""
    op.drop_index("ix_alerts_monitor_run_id", table_name="alerts")
    op.drop_index("ix_alerts_monitor_created", table_name="alerts")
    op.drop_table("alerts")
