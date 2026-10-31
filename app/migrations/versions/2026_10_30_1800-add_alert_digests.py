"""add alert digests

Revision ID: 2dea7ae9017f
Revises: add_alerts
Create Date: 2026-10-30

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_alert_digests"
down_revision = "add_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve single-alert history; refuse ambiguous legacy consolidation."""
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM alerts GROUP BY monitor_run_id HAVING count(*) > 1) THEN
            RAISE EXCEPTION 'Consolidate legacy alerts per run before migrating to digests';
        END IF;
    END $$""")
    op.create_unique_constraint(
        op.f("uq_alerts_id"), "alerts", ["id", "monitor_run_id", "monitor_id"]
    )
    op.create_unique_constraint(
        op.f("uq_alerts_monitor_run_id"), "alerts", ["monitor_run_id"]
    )

    op.create_table(
        "alert_jobs",
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column(
            "lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_alert_jobs_lease_matches_state"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'skipped', 'failed', 'cancelled')",
            name=op.f("ck_alert_jobs_valid_status"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_alert_jobs_nonnegative_attempts"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_run_id", "monitor_id"],
            ["monitor_runs.id", "monitor_runs.monitor_id"],
            name=op.f("fk_alert_jobs_monitor_run_id_monitor_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_jobs")),
        sa.UniqueConstraint(
            "monitor_run_id", name=op.f("uq_alert_jobs_monitor_run_id")
        ),
    )
    op.create_index(
        "ix_alert_jobs_due",
        "alert_jobs",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "ix_alert_jobs_monitor_id", "alert_jobs", ["monitor_id"], unique=False
    )
    op.create_table(
        "alert_candidates",
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("observation_ref", sa.Text(), nullable=False),
        sa.Column("should_alert", sa.Boolean(), nullable=False),
        sa.Column("what_changed", sa.Text(), nullable=False),
        sa.Column("why_it_matters", sa.Text(), nullable=False),
        sa.Column("development_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(should_alert AND development_key ~ '[^[:space:]]') OR (NOT should_alert AND development_key IS NULL)",
            name=op.f("ck_alert_candidates_decision_matches_key"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'rejected', 'duplicate', 'excluded', 'published')",
            name=op.f("ck_alert_candidates_valid_status"),
        ),
        sa.CheckConstraint(
            "NOT should_alert OR development_key IS NOT NULL",
            name=op.f("ck_alert_candidates_positive_key_required"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_run_id", "monitor_id"],
            ["monitor_runs.id", "monitor_runs.monitor_id"],
            name=op.f("fk_alert_candidates_monitor_run_id_monitor_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "monitor_run_id"],
            ["observations.id", "observations.monitor_run_id"],
            name=op.f("fk_alert_candidates_observation_id_observations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_candidates")),
        sa.UniqueConstraint(
            "monitor_run_id",
            "observation_ref",
            name=op.f("uq_alert_candidates_monitor_run_id"),
        ),
    )
    op.create_table(
        "alert_observations",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("development_key", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "development_key ~ '[^[:space:]]'",
            name=op.f("ck_alert_observations_nonblank_development_key"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id", "monitor_run_id", "monitor_id"],
            ["alerts.id", "alerts.monitor_run_id", "alerts.monitor_id"],
            name=op.f("fk_alert_observations_alert_id_alerts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "monitor_run_id"],
            ["observations.id", "observations.monitor_run_id"],
            name=op.f("fk_alert_observations_observation_id_observations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_observations")),
        sa.UniqueConstraint(
            "monitor_id",
            "development_key",
            name=op.f("uq_alert_observations_monitor_id"),
        ),
        sa.UniqueConstraint(
            "observation_id", name=op.f("uq_alert_observations_observation_id")
        ),
    )
    op.create_index(
        "ix_alert_observations_alert_id",
        "alert_observations",
        ["alert_id"],
        unique=False,
    )
    op.execute("""INSERT INTO alert_observations
        (id, alert_id, monitor_id, monitor_run_id, observation_id, development_key, created_at)
        SELECT gen_random_uuid(), id, monitor_id, monitor_run_id, observation_id,
               deduplication_key, created_at FROM alerts""")
    op.drop_constraint(op.f("uq_alerts_monitor_id"), "alerts", type_="unique")
    op.drop_constraint(
        op.f("uq_alerts_observation_id"), "alerts", type_="unique"
    )
    op.drop_constraint(
        op.f("fk_alerts_observation_id_observations"),
        "alerts",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_alerts_nonblank_deduplication_key"), "alerts", type_="check"
    )
    op.drop_column("alerts", "observation_id")
    op.drop_column("alerts", "deduplication_key")


def downgrade() -> None:
    """Restore original storage only when every digest has one observation."""
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT a.id FROM alerts a LEFT JOIN alert_observations o
                   ON o.alert_id = a.id GROUP BY a.id HAVING count(o.id) <> 1) THEN
            RAISE EXCEPTION 'Multi-observation digests cannot be downgraded without data loss';
        END IF;
    END $$""")
    op.add_column(
        "alerts", sa.Column("observation_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "alerts", sa.Column("deduplication_key", sa.Text(), nullable=True)
    )
    op.execute("""UPDATE alerts a SET observation_id = o.observation_id,
        deduplication_key = o.development_key FROM alert_observations o
        WHERE o.alert_id = a.id""")
    op.alter_column("alerts", "observation_id", nullable=False)
    op.alter_column("alerts", "deduplication_key", nullable=False)
    op.drop_table("alert_observations")
    op.drop_table("alert_candidates")
    op.drop_table("alert_jobs")
    op.drop_constraint(
        op.f("uq_alerts_monitor_run_id"), "alerts", type_="unique"
    )
    op.drop_constraint(op.f("uq_alerts_id"), "alerts", type_="unique")
    op.create_unique_constraint(
        op.f("uq_alerts_observation_id"), "alerts", ["observation_id"]
    )
    op.create_unique_constraint(
        op.f("uq_alerts_monitor_id"),
        "alerts",
        ["monitor_id", "deduplication_key"],
    )
    op.create_foreign_key(
        op.f("fk_alerts_observation_id_observations"),
        "alerts",
        "observations",
        ["observation_id", "monitor_run_id"],
        ["id", "monitor_run_id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_alerts_nonblank_deduplication_key"),
        "alerts",
        "deduplication_key ~ '[^[:space:]]'",
    )
