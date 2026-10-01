"""Exercise the initial migration in a disposable Postgres database.

Opt in with VELOLAB_TEST_DATABASE=1 and explicit TEST_POSTGRES_* configuration
for infra/test-db. Each run creates and drops its own randomly named database;
it never uses application credentials or the real-use database.

Failure inventory: missing/wrong columns or stale model metadata (schema drift),
duplicate emails or provider links, orphan links and missing DB-level cascade,
missing timezone-aware server timestamps, and incomplete downgrade all break
the migration contract. Exercise these through Alembic and real DB reads/writes.
Auth, encryption, retries, concurrency and external services are not part of
this schema slice.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api.models import User, UserIntegration


def test_initial_migration_matches_models_and_enforces_ownership(
    isolated_database: tuple[Config, Engine],
) -> None:
    config, engine = isolated_database
    command.upgrade(config, "head")
    # Compares live schema against registered model metadata; detects unmapped
    # tables/columns as well as accidental divergence in the revision.
    command.check(config)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'user_integrations' "
                    "AND column_name = 'encrypted_api_key'"
                )
            ).scalar_one()
            == "text"
        )

    with Session(engine) as session:
        rider = User(email="rider@example.com", password_hash="hash")
        friend = User(email="friend@example.com", password_hash="hash")
        session.add_all([rider, friend])
        session.commit()
        assert isinstance(rider.created_at, datetime)
        assert rider.created_at.utcoffset() is not None

        link = UserIntegration(
            user_id=rider.id,
            provider="intervals.icu",
            external_athlete_id="athlete-1",
            encrypted_api_key="ciphertext",
        )
        session.add(link)
        session.commit()
        link_id = link.id
        stored_link = session.get(UserIntegration, link_id)
        assert stored_link is not None
        assert stored_link.encrypted_api_key == "ciphertext"
        assert isinstance(stored_link.created_at, datetime)
        assert stored_link.created_at.utcoffset() is not None

        # The uniqueness scope is per athlete/provider, not globally per provider.
        session.add(
            UserIntegration(
                user_id=friend.id,
                provider="intervals.icu",
                external_athlete_id="athlete-2",
                encrypted_api_key="other ciphertext",
            )
        )
        session.commit()
        assert len(session.scalars(select(UserIntegration)).all()) == 2

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(User(email="rider@example.com", password_hash="other"))
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                UserIntegration(
                    user_id=rider.id,
                    provider="intervals.icu",
                    external_athlete_id="different",
                    encrypted_api_key="other",
                )
            )
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                UserIntegration(
                    user_id=uuid4(),
                    provider="intervals.icu",
                    external_athlete_id="orphan",
                    encrypted_api_key="other",
                )
            )
            session.flush()

        session.delete(rider)
        session.commit()
        session.expunge_all()  # Observe the DB result, not the identity map's cached link.
        assert session.get(UserIntegration, link_id) is None
        assert len(session.scalars(select(UserIntegration)).all()) == 1

    command.downgrade(config, "base")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('public.users')")).scalar_one() is None
        assert (
            connection.execute(text("SELECT to_regclass('public.user_integrations')")).scalar_one()
            is None
        )
