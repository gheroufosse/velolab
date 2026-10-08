from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from velolab_api.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserIntegration(Base):
    __tablename__ = "user_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider"),
        UniqueConstraint("id", "user_id", name="uq_user_integrations_id_user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey(column="users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(length=50))
    external_athlete_id: Mapped[str] = mapped_column(String(length=255))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationSyncState(Base):
    """Full-sync and recent-preview freshness are deliberately independent."""

    __tablename__ = "integration_sync_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["integration_id", "user_id"],
            ["user_integrations.id", "user_integrations.user_id"],
            ondelete="CASCADE",
            name="fk_integration_sync_state_integration_owner",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_expires_at IS NULL)",
            name="ck_integration_sync_state_lease_pair",
        ),
        CheckConstraint(
            "(preview_oldest IS NULL AND preview_newest IS NULL) OR "
            "(preview_oldest IS NOT NULL AND preview_newest IS NOT NULL "
            "AND preview_oldest <= preview_newest)",
            name="ck_integration_sync_state_preview_window",
        ),
    )

    integration_id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_status: Mapped[str | None] = mapped_column(String(50))
    last_error_code: Mapped[str | None] = mapped_column(String(50))
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last committed provider-local backfill day; orchestration will own direction/coverage.
    backfill_checkpoint: Mapped[date | None] = mapped_column(Date)
    backfill_complete: Mapped[bool] = mapped_column(Boolean, server_default=false())
    last_preview_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preview_oldest: Mapped[date | None] = mapped_column(Date)
    preview_newest: Mapped[date | None] = mapped_column(Date)
    possibly_truncated: Mapped[bool] = mapped_column(Boolean, server_default=false())


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        UniqueConstraint("id", "user_id", name="uq_auth_sessions_id_user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_session_id", "session_id"),
        ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["auth_sessions.id", "auth_sessions.user_id"],
            ondelete="CASCADE",
            name="fk_refresh_tokens_session_owner",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    spent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Activity(Base):
    """Payload is truth; nullable projections are rebuildable (ADR-024 slice 2).

    Provider identity is Intervals `id`, never upstream `external_id`.
    Mapping/units remain unverified (contract blocker 4); intervals_upsert owns
    the single projection mapping and bookkeeping updates, not this schema.
    """

    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "integration_id", "provider_activity_id", name="uq_activities_identity"
        ),
        ForeignKeyConstraint(
            ["integration_id", "user_id"],
            ["user_integrations.id", "user_integrations.user_id"],
            ondelete="CASCADE",
            name="fk_activities_integration_owner",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    integration_id: Mapped[UUID] = mapped_column()
    provider_activity_id: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB(none_as_null=True))
    start_date_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_date_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    training_load: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WellnessDay(Base):
    """Provider-supplied date keys the day; never derive it from UTC.

    Payload is truth, including missing/null and tempWeight/tempRestingHR flags.
    Nullable projections make no unit or measured-value claim (blocker 4).
    """

    __tablename__ = "wellness_days"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "integration_id", "local_date", name="uq_wellness_days_identity"
        ),
        ForeignKeyConstraint(
            ["integration_id", "user_id"],
            ["user_integrations.id", "user_integrations.user_id"],
            ondelete="CASCADE",
            name="fk_wellness_days_integration_owner",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    integration_id: Mapped[UUID] = mapped_column()
    local_date: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB(none_as_null=True))
    ctl: Mapped[float | None] = mapped_column(Float)
    atl: Mapped[float | None] = mapped_column(Float)
    ramp_rate: Mapped[float | None] = mapped_column(Float)
    resting_hr: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float)
    weight_carried_over: Mapped[bool | None] = mapped_column(Boolean)
    resting_hr_carried_over: Mapped[bool | None] = mapped_column(Boolean)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
