"""Add observations and their supporting evidence.

Revision ID: add_observation_evidence
Revises: add_monitor_foundation
"""

import sqlalchemy as sa
from alembic import op

revision = "add_observation_evidence"
down_revision = "add_monitor_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add immutable findings and per-finding source records."""
    op.create_table(
        "observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observations")),
        sa.ForeignKeyConstraint(
            ["monitor_run_id"],
            ["monitor_runs.id"],
            ondelete="CASCADE",
            name=op.f("fk_observations_monitor_run_id_monitor_runs"),
        ),
        sa.UniqueConstraint(
            "monitor_run_id",
            "item_key",
            name=op.f("uq_observations_monitor_run_id"),
        ),
        sa.UniqueConstraint(
            "id", "monitor_run_id", name=op.f("uq_observations_id")
        ),
        sa.CheckConstraint(
            "summary ~ '[^[:space:]]'",
            name=op.f("ck_observations_nonblank_summary"),
        ),
    )
    op.create_index(
        "ix_observations_monitor_run_id", "observations", ["monitor_run_id"]
    )
    op.create_table(
        "observation_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column(
            "source_published_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_excerpt", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observation_sources")),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            ondelete="CASCADE",
            name=op.f("fk_observation_sources_observation_id_observations"),
        ),
        sa.UniqueConstraint(
            "observation_id",
            "source_key",
            name=op.f("uq_observation_sources_observation_id"),
        ),
    )


def downgrade() -> None:
    """Remove evidence before its parent observations."""
    op.drop_table("observation_sources")
    op.drop_index("ix_observations_monitor_run_id", table_name="observations")
    op.drop_table("observations")
