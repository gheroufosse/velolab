"""Exercise the initial migration in a disposable Postgres database.

Opt in with VELOLAB_TEST_DATABASE=1 when the local Compose DB is running. Each
run creates and drops its own randomly named database; it never migrates or
clears tables in the configured development database.

Failure inventory: missing/wrong columns or stale model metadata (schema drift),
duplicate emails or provider links, orphan links and missing DB-level cascade,
missing timezone-aware server timestamps, and incomplete downgrade all break
the migration contract. Exercise these through Alembic and real DB reads/writes.
Auth, encryption, retries, concurrency and external services are not part of
this schema slice.
"""

import os
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api.models import User, UserIntegration
from velolab_api.settings import Settings, get_settings

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[Config, Engine]]:
    if os.environ.get("VELOLAB_TEST_DATABASE") != "1":
        pytest.skip("set VELOLAB_TEST_DATABASE=1 to run against local Postgres")

    # Only use the configured database to issue CREATE/DROP DATABASE. Never
    # apply migrations or test inserts to it.
    get_settings.cache_clear()
    settings: Settings = get_settings()
    if settings.postgres_host not in {"127.0.0.1", "localhost"}:
        pytest.fail("database integration tests require a local Postgres host")
    database_name = f"velolab_test_{uuid4().hex}"
    admin_engine: Engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        try:
            with monkeypatch.context() as environment:
                environment.setenv("POSTGRES_DB", database_name)
                get_settings.cache_clear()
                test_engine = create_engine(get_settings().database_url)
                try:
                    yield Config(str(ALEMBIC_INI)), test_engine
                finally:
                    test_engine.dispose()
        finally:
            get_settings.cache_clear()
            with admin_engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()
        get_settings.cache_clear()


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
