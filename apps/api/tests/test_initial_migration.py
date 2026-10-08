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

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import Engine, delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api import models
from velolab_api.models import AuthSession, RefreshToken, User, UserIntegration


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

        owner_session = AuthSession(
            user_id=rider.id, expires_at=datetime.now(UTC) + timedelta(days=7)
        )
        session.add(owner_session)
        session.flush()
        token = RefreshToken(user_id=rider.id, session_id=owner_session.id, token_hash="a" * 64)
        session.add(token)
        session.commit()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                RefreshToken(user_id=friend.id, session_id=owner_session.id, token_hash="b" * 64)
            )
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                RefreshToken(user_id=rider.id, session_id=owner_session.id, token_hash="a" * 64)
            )
            session.flush()

        token_id = token.id
        session.delete(rider)
        session.commit()
        session.expunge_all()  # Observe the DB result, not the identity map's cached link.
        assert session.get(RefreshToken, token_id) is None
        assert session.get(UserIntegration, link_id) is None
        assert len(session.scalars(select(UserIntegration)).all()) == 1

    # Slice 2 is independently reversible without disturbing existing data.
    command.downgrade(config, "c437ce183e2b")
    assert not inspect(engine).has_table("activities")
    assert not inspect(engine).has_table("wellness_days")
    assert "uq_user_integrations_id_user_id" not in {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("user_integrations")
    }
    with Session(engine) as session:
        assert len(session.scalars(select(UserIntegration)).all()) == 1
    command.upgrade(config, "head")
    command.check(config)

    command.downgrade(config, "base")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('public.users')")).scalar_one() is None
        assert (
            connection.execute(text("SELECT to_regclass('public.user_integrations')")).scalar_one()
            is None
        )
        assert (
            connection.execute(text("SELECT to_regclass('public.auth_sessions')")).scalar_one()
            is None
        )
        assert (
            connection.execute(text("SELECT to_regclass('public.refresh_tokens')")).scalar_one()
            is None
        )


@pytest.mark.parametrize("table_name", ["activities", "wellness_days"])
def test_training_records_preserve_payload_and_enforce_owned_identity(
    isolated_database: tuple[Config, Engine], table_name: str
) -> None:
    # Risks: duplicate identities, cross-owner references, lossy payloads or
    # invented projections, and orphan records after DB cascades. Exercise each
    # through the migrated PostgreSQL schema and fresh-session reads.
    config, engine = isolated_database
    command.upgrade(config, "head")
    assert inspect(engine).has_table(table_name)
    model = models.Activity if table_name == "activities" else models.WellnessDay
    identity = (
        {"provider_activity_id": "opaque-provider-id"}
        if table_name == "activities"
        else {"local_date": date(2026, 10, 8)}
    )
    payload = {"external_id": "not-the-record-key", "zero": 0, "cleared": None, "nested": [True]}
    with Session(engine) as session:
        rider = User(email="rider@example.com", password_hash="hash")
        friend = User(email="friend@example.com", password_hash="hash")
        session.add_all([rider, friend])
        session.flush()
        links = [
            UserIntegration(
                user_id=owner,
                provider=provider,
                external_athlete_id="synthetic-athlete",
                encrypted_api_key="synthetic-ciphertext",
            )
            for owner, provider in (
                (rider.id, "intervals.icu"),
                (friend.id, "intervals.icu"),
                (rider.id, "synthetic-provider"),
            )
        ]
        session.add_all(links)
        session.flush()
        records = [
            model(user_id=link.user_id, integration_id=link.id, payload=payload, **identity)
            for link in links
        ]
        # Non-null projections round-trip without conflating zero/false with
        # missing data, or converting the supplied local time/date to UTC.
        projected = records[1]
        if isinstance(projected, models.Activity):
            projected.payload = {
                "start_date": "2026-10-08T23:30:00Z",
                "start_date_local": "2026-10-09T01:30:00",
                "icu_training_load": 0,
            }
            projected.start_date_utc = datetime(2026, 10, 8, 23, 30, tzinfo=UTC)
            projected.start_date_local = datetime(2026, 10, 9, 1, 30)
            projected.training_load = 0
        else:
            projected.payload = {
                "id": "2026-10-08",
                "ctl": 0,
                "atl": 35.5,
                "rampRate": -1,
                "restingHR": 50,
                "weight": 70.5,
                "tempWeight": True,
                "tempRestingHR": False,
            }
            projected.ctl, projected.atl, projected.ramp_rate = 0, 35.5, -1
            projected.resting_hr, projected.weight = 50, 70.5
            projected.weight_carried_over, projected.resting_hr_carried_over = True, False
        session.add_all(records)
        session.commit()
        rider_id, friend_id, link_id = rider.id, friend.id, links[0].id
        record_id = records[0].id

        with pytest.raises(IntegrityError) as duplicate, session.begin_nested():
            session.add(
                model(user_id=rider_id, integration_id=link_id, payload=payload, **identity)
            )
            session.flush()
        assert isinstance(duplicate.value.orig, UniqueViolation)
        assert duplicate.value.orig.diag.constraint_name == f"uq_{table_name}_identity"

        with pytest.raises(IntegrityError) as wrong_owner, session.begin_nested():
            session.add(
                model(user_id=friend_id, integration_id=link_id, payload=payload, **identity)
            )
            session.flush()
        assert isinstance(wrong_owner.value.orig, ForeignKeyViolation)
        assert wrong_owner.value.orig.diag.constraint_name == f"fk_{table_name}_integration_owner"

    with Session(engine) as reader:
        record = reader.get(model, record_id)
        assert record is not None
        assert record.payload == payload  # null and zero survive; omitted keys stay absent.
        assert reader.scalar(select(func.pg_typeof(model.payload))) == "jsonb"
        for timestamp in (record.first_seen_at, record.last_seen_at, record.updated_at):
            assert isinstance(timestamp, datetime)
            assert timestamp.utcoffset() is not None
        if isinstance(record, models.Activity):
            assert record.provider_activity_id == "opaque-provider-id"
            assert record.start_date_utc is None
            assert record.start_date_local is None
            assert record.training_load is None
        else:
            assert record.local_date == date(2026, 10, 8)
            assert (record.ctl, record.atl, record.ramp_rate, record.resting_hr, record.weight) == (
                None,
                None,
                None,
                None,
                None,
            )
            assert record.weight_carried_over is None
            assert record.resting_hr_carried_over is None
        reader.execute(delete(User).where(User.id == rider_id))
        reader.commit()

    with Session(engine) as reader:
        remaining = reader.scalars(select(model)).all()
        assert len(remaining) == 1
        assert remaining[0].user_id == friend_id
        survivor = remaining[0]
        if isinstance(survivor, models.Activity):
            assert survivor.start_date_utc == datetime(2026, 10, 8, 23, 30, tzinfo=UTC)
            assert survivor.start_date_local == datetime(2026, 10, 9, 1, 30)
            assert survivor.training_load == 0
            assert survivor.payload["icu_training_load"] == 0
        else:
            assert survivor.local_date == date(2026, 10, 8)
            assert (survivor.ctl, survivor.atl, survivor.ramp_rate) == (0, 35.5, -1)
            assert (survivor.resting_hr, survivor.weight) == (50, 70.5)
            assert survivor.weight_carried_over is True
            assert survivor.resting_hr_carried_over is False
            assert survivor.payload["tempRestingHR"] is False
