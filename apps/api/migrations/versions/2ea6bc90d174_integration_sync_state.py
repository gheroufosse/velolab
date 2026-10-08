"""Add owned fenced sync state and separate recent-preview markers.

Revision ID: 2ea6bc90d174
Revises: 8d294a62b103
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2ea6bc90d174"
down_revision: str | Sequence[str] | None = "8d294a62b103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_sync_state",
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_status", sa.String(50), nullable=True),
        sa.Column("last_error_code", sa.String(50), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_checkpoint", sa.Date(), nullable=True),
        sa.Column("backfill_complete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_preview_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preview_oldest", sa.Date(), nullable=True),
        sa.Column("preview_newest", sa.Date(), nullable=True),
        sa.Column("possibly_truncated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("integration_id", name="pk_integration_sync_state"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_integration_sync_state_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id", "user_id"],
            ["user_integrations.id", "user_integrations.user_id"],
            name="fk_integration_sync_state_integration_owner",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_integration_sync_state_lease_pair",
        ),
        sa.CheckConstraint(
            "(preview_oldest IS NULL AND preview_newest IS NULL) OR "
            "(preview_oldest IS NOT NULL AND preview_newest IS NOT NULL "
            "AND preview_oldest <= preview_newest)",
            name="ck_integration_sync_state_preview_window",
        ),
    )


def downgrade() -> None:
    op.drop_table("integration_sync_state")
