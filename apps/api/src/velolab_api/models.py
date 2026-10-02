from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    __table_args__: tuple[UniqueConstraint] = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey(column="users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(length=50))
    external_athlete_id: Mapped[str] = mapped_column(String(length=255))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
