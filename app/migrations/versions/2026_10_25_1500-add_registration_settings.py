"""Persist the administrator-controlled registration policy.

Revision ID: add_registration_settings
Revises: add_last_used_at
"""

# ruff: noqa: INP001

import sqlalchemy as sa
from alembic import op

revision: str = "add_registration_settings"
down_revision: str | None = "add_last_used_at"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the policy table without changing the initial signup requirement."""
    op.create_table(
        "registration_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("referral_code_required", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "id = 1", name=op.f("ck_registration_settings_singleton")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_registration_settings")),
    )


def downgrade() -> None:
    """Remove the override and restore environment-based signup policy."""
    op.drop_table("registration_settings")
