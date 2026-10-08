"""Add payload-backed activities and wellness days with integration ownership.

Revision ID: 8d294a62b103
Revises: c437ce183e2b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8d294a62b103"
down_revision: str | Sequence[str] | None = "c437ce183e2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_user_integrations_id_user_id", "user_integrations", ["id", "user_id"]
    )
    op.create_table(
        "activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("provider_activity_id", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("start_date_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_date_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("training_load", sa.Float(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_activities"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_activities_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["integration_id", "user_id"],
            ["user_integrations.id", "user_integrations.user_id"],
            name="fk_activities_integration_owner",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id", "integration_id", "provider_activity_id", name="uq_activities_identity"
        ),
    )
    op.create_table(
        "wellness_days",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("payload", postgresql.JSONB(none_as_null=True), nullable=False),
        sa.Column("ctl", sa.Float(), nullable=True),
        sa.Column("atl", sa.Float(), nullable=True),
        sa.Column("ramp_rate", sa.Float(), nullable=True),
        sa.Column("resting_hr", sa.Float(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("weight_carried_over", sa.Boolean(), nullable=True),
        sa.Column("resting_hr_carried_over", sa.Boolean(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wellness_days"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_wellness_days_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["integration_id", "user_id"],
            ["user_integrations.id", "user_integrations.user_id"],
            name="fk_wellness_days_integration_owner",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id", "integration_id", "local_date", name="uq_wellness_days_identity"
        ),
    )


def downgrade() -> None:
    op.drop_table("wellness_days")
    op.drop_table("activities")
    op.drop_constraint("uq_user_integrations_id_user_id", "user_integrations", type_="unique")
