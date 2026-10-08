"""Synthetic records through the upsert service and migrated PostgreSQL reads.

Failure inventory: reruns rewrite rows; partial/null updates erase known data;
zero/false become missing; projections diverge from merged payload; duplicate
identities or wrong athlete/owner pollute a batch; failed writes leave partial
work; concurrent partial updates lose fields. All are exercised against the
existing disposable PostgreSQL boundary, not mocks/SQLite. No provider parity,
listing completeness, deletion, HTTP, leases or sync freshness claims here.
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from velolab_api.intervals_client import ActivityRecord, WellnessRecord
from velolab_api.intervals_upsert import (
    DEFAULT_UPSERT_POLICY,
    NullPolicy,
    PayloadFields,
    UpsertError,
    UpsertPolicy,
    upsert_activities,
    upsert_wellness_days,
)
from velolab_api.models import Activity, User, UserIntegration, WellnessDay

DAY = date(2026, 10, 8)
SEEN = datetime(2026, 10, 8, 12, tzinfo=UTC)


@pytest.fixture
def database(isolated_database: tuple[Config, Engine]) -> Engine:
    config, engine = isolated_database
    command.upgrade(config, "head")
    with Session(engine) as session:
        for name in ("rider", "friend"):
            user = User(email=f"{name}@example.com", password_hash="synthetic-hash")
            session.add(user)
            session.flush()
            session.add(
                UserIntegration(
                    user_id=user.id,
                    provider="intervals.icu",
                    external_athlete_id="synthetic-athlete",
                    encrypted_api_key="synthetic-ciphertext",
                )
            )
        session.commit()
    return engine


def link(session: Session, name: str = "rider") -> UserIntegration:
    return session.scalars(
        select(UserIntegration)
        .join(User, User.id == UserIntegration.user_id)
        .where(User.email == f"{name}@example.com")
    ).one()


def activity(raw: Mapping[str, object], athlete_id: str | None = None) -> ActivityRecord:
    # Typed date views are deliberately empty: projections must use raw truth.
    return ActivityRecord(str(raw["id"]), athlete_id, None, None, raw)


def wellness(raw: Mapping[str, object]) -> WellnessRecord:
    return WellnessRecord(date.fromisoformat(str(raw["id"])), raw)


def write(
    session: Session,
    kind: str,
    payloads: Sequence[Mapping[str, object]],
    *,
    owner: str = "rider",
    policy: UpsertPolicy = DEFAULT_UPSERT_POLICY,
    observed_at: datetime = SEEN,
) -> int:
    integration = link(session, owner)
    if kind == "activities":
        return upsert_activities(
            session,
            user_id=integration.user_id,
            integration=integration,
            records=[activity(raw) for raw in payloads],
            policy=policy,
            observed_at=observed_at,
        )
    return upsert_wellness_days(
        session,
        user_id=integration.user_id,
        integration=integration,
        records=[wellness(raw) for raw in payloads],
        policy=policy,
        observed_at=observed_at,
    )


def identity(kind: str) -> str:
    return "opaque-activity" if kind == "activities" else DAY.isoformat()


def metric(kind: str) -> str:
    return "icu_training_load" if kind == "activities" else "ctl"


def row(session: Session, kind: str, owner: str = "rider") -> Activity | WellnessDay:
    model = Activity if kind == "activities" else WellnessDay
    return session.scalars(select(model).where(model.user_id == link(session, owner).user_id)).one()


def version(session: Session, kind: str) -> str:
    # PostgreSQL tuple version proves no physical UPDATE, not just equal timestamps.
    return session.execute(text(f"SELECT xmin::text FROM {kind}")).scalar_one()


@pytest.mark.parametrize("kind", ["activities", "wellness_days"])
def test_rerun_and_non_destructive_merge(database: Engine, kind: str) -> None:
    key = metric(kind)
    payload: dict[str, object] = {
        "id": identity(kind),
        key: 50,
        "unknown": {"nested": None},
        "explicit": None,
    }
    if kind == "activities":
        payload.update(start_date="2026-10-08T23:30:00Z", start_date_local="2026-10-09T01:30:00")
    else:
        payload.update(
            atl=35, rampRate=-1, restingHR=50, weight=70, tempWeight=True, tempRestingHR=False
        )
    with Session(database) as session:
        assert write(session, kind, [payload]) == 1
        session.commit()
    with Session(database) as session:
        stored = row(session, kind)
        original_id = stored.id
        assert stored.payload == payload
        assert stored.first_seen_at == stored.updated_at == stored.last_seen_at == SEEN
        if isinstance(stored, Activity):
            assert stored.start_date_utc == datetime(2026, 10, 8, 23, 30, tzinfo=UTC)
            assert stored.start_date_local == datetime(2026, 10, 9, 1, 30)
            assert stored.training_load == 50
        else:
            assert stored.local_date == DAY
            assert (stored.ctl, stored.atl, stored.ramp_rate, stored.resting_hr, stored.weight) == (
                50,
                35,
                -1,
                50,
                70,
            )
            assert stored.weight_carried_over is True
            assert stored.resting_hr_carried_over is False
        original_version = version(session, kind)
        assert write(session, kind, [payload], observed_at=SEEN + timedelta(days=1)) == 0
        assert write(session, kind, [{"id": identity(kind), key: None}]) == 0
        assert write(session, kind, []) == 0  # absence never deletes
        session.commit()
    with Session(database) as session:
        stored = row(session, kind)
        assert stored.id == original_id
        assert version(session, kind) == original_version
        assert stored.updated_at == stored.last_seen_at == SEEN
        assert stored.payload == payload
        # A partial correction preserves omitted fields and opaque nested nulls.
        patch = {"id": identity(kind), "new_field": 7, "new_null": None, key: None}
        assert write(session, kind, [patch], observed_at=SEEN + timedelta(days=2)) == 1
        session.commit()
    with Session(database) as session:
        stored = row(session, kind)
        assert stored.payload == {**payload, "new_field": 7, "new_null": None}
        assert stored.first_seen_at == SEEN
        assert stored.updated_at == stored.last_seen_at == SEEN + timedelta(days=2)
        assert (stored.training_load if isinstance(stored, Activity) else stored.ctl) == 50
        # Explicit clearing is opt-in; unknown fields also follow this policy.
        clear: dict[str, object] = {"id": identity(kind), key: None, "unknown": None}
        if kind == "activities":
            clear.update(start_date=None, start_date_local=None)
        else:
            clear.update(tempWeight=None, tempRestingHR=None)
        assert (
            write(
                session,
                kind,
                [clear],
                policy=UpsertPolicy(explicit_nulls=NullPolicy.CLEAR),
                observed_at=SEEN + timedelta(days=3),
            )
            == 1
        )
        session.commit()
    with Session(database) as session:
        stored = row(session, kind)
        assert stored.payload[key] is None
        assert stored.payload["unknown"] is None
        assert (stored.training_load if isinstance(stored, Activity) else stored.ctl) is None
        if isinstance(stored, Activity):
            assert stored.start_date_utc is None
            assert stored.start_date_local is None
        else:
            assert stored.weight_carried_over is None
            assert stored.resting_hr_carried_over is None
        zero = {"id": identity(kind), key: 0}
        if kind == "wellness_days":
            zero.update(atl=0, rampRate=0, restingHR=0, weight=0, tempWeight=False)
        assert write(session, kind, [zero], observed_at=SEEN + timedelta(days=4)) == 1
        session.commit()
    with Session(database) as session:
        stored = row(session, kind)
        assert stored.payload[key] == 0
        if isinstance(stored, Activity):
            assert stored.training_load == 0
        else:
            assert (stored.ctl, stored.atl, stored.ramp_rate, stored.resting_hr, stored.weight) == (
                0,
                0,
                0,
                0,
                0,
            )
            assert stored.weight_carried_over is False


@pytest.mark.parametrize("kind", ["activities", "wellness_days"])
def test_duplicate_and_mismatched_athlete_reject_entire_batch(database: Engine, kind: str) -> None:
    first = {"id": identity(kind)}
    other = "z-other" if kind == "activities" else "2026-10-09"
    with Session(database) as session:
        for batch, code in (
            ([first, {**first, metric(kind): 1}], "duplicate_record_identity"),
            ([first, {"id": other, "icu_athlete_id": "wrong-athlete"}], "athlete_mismatch"),
            ([first, {"id": other, "athlete_id": "wrong-athlete"}], "athlete_mismatch"),
        ):
            with pytest.raises(UpsertError, match=f"^{code}$"):
                write(session, kind, batch)
            session.commit()
            model = Activity if kind == "activities" else WellnessDay
            assert session.scalars(select(model)).all() == []
        # A matching raw binding is accepted, null or omitted binding is permitted.
        assert write(session, kind, [{**first, "icu_athlete_id": "synthetic-athlete"}]) == 1
        session.commit()


def test_typed_identity_and_athlete_cannot_bypass_raw_validation(database: Engine) -> None:
    with Session(database) as session:
        integration = link(session)
        for record, code in (
            (activity({"id": "a"}, "wrong-athlete"), "athlete_mismatch"),
            (ActivityRecord("a", None, None, None, {"id": "b"}), "invalid_record_identity"),
        ):
            with pytest.raises(UpsertError, match=f"^{code}$"):
                upsert_activities(
                    session, user_id=integration.user_id, integration=integration, records=[record]
                )
        with pytest.raises(UpsertError, match="^invalid_record_identity$"):
            upsert_wellness_days(
                session,
                user_id=integration.user_id,
                integration=integration,
                records=[WellnessRecord(DAY, {"id": "2026-10-09"})],
            )
        session.commit()
        assert session.scalars(select(Activity)).all() == []
        assert session.scalars(select(WellnessDay)).all() == []


@pytest.mark.parametrize("kind", ["activities", "wellness_days"])
def test_owner_scoping_and_stale_binding(database: Engine, kind: str) -> None:
    with Session(database) as session:
        assert write(session, kind, [{"id": identity(kind), metric(kind): 1}]) == 1
        assert write(session, kind, [{"id": identity(kind)}], owner="friend") == 1
        # A different owner's missing field must not influence this row's merge.
        assert write(session, kind, [{"id": identity(kind), metric(kind): None}]) == 0
        assert write(session, kind, [{"id": identity(kind), metric(kind): 2}], owner="friend") == 1
        session.commit()
    with Session(database) as session:
        assert row(session, kind).payload[metric(kind)] == 1
        assert row(session, kind, "friend").payload[metric(kind)] == 2
        integration, friend = link(session), link(session, "friend")
        upsert = upsert_activities if kind == "activities" else upsert_wellness_days
        with pytest.raises(UpsertError, match="^integration_owner_mismatch$"):
            upsert(session, user_id=friend.user_id, integration=integration, records=[])
        integration.external_athlete_id = "changed-binding"
        with pytest.raises(UpsertError, match="^invalid_integration_binding$"):
            upsert(session, user_id=integration.user_id, integration=integration, records=[])
        session.rollback()


@pytest.mark.parametrize("kind", ["activities", "wellness_days"])
def test_failed_batch_and_outer_rollback_leave_no_partial_records(
    database: Engine, kind: str
) -> None:
    first = "a-first" if kind == "activities" else "2026-10-08"
    second = "z-second" if kind == "activities" else "2026-10-09"
    with Session(database) as session:
        with pytest.raises(DataError):
            write(session, kind, [{"id": first}, {"id": second, metric(kind): "not-numeric"}])
        session.commit()  # even a caller catching the error cannot commit partial batch writes
    with Session(database) as session:
        model = Activity if kind == "activities" else WellnessDay
        assert session.scalars(select(model)).all() == []
        assert write(session, kind, [{"id": first}]) == 1
        session.rollback()
    with Session(database) as session:
        assert session.scalars(select(model)).all() == []


@pytest.mark.parametrize("kind", ["activities", "wellness_days"])
def test_mapping_correction_reprojects_retained_payload(database: Engine, kind: str) -> None:
    payload = {"id": identity(kind), metric(kind): 1, "revised_metric": 9}
    fields = (
        PayloadFields(activity_load="revised_metric")
        if kind == "activities"
        else PayloadFields(wellness_ctl="revised_metric")
    )
    with Session(database) as session:
        assert write(session, kind, [payload]) == 1
        session.commit()
    with Session(database) as session:
        assert (
            write(
                session,
                kind,
                [{"id": identity(kind)}],
                policy=UpsertPolicy(fields=fields),
                observed_at=SEEN + timedelta(days=1),
            )
            == 1
        )
        session.commit()
    with Session(database) as session:
        stored = row(session, kind)
        assert stored.payload == payload
        assert (stored.training_load if isinstance(stored, Activity) else stored.ctl) == 9
        assert stored.updated_at == SEEN + timedelta(days=1)
        assert write(session, kind, [payload], policy=UpsertPolicy(fields=fields)) == 0


@pytest.mark.parametrize("kind", ["activities", "wellness_days"])
@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_partial_updates_merge_without_losing_fields(
    database: Engine, kind: str, existing: bool
) -> None:
    if existing:
        with Session(database) as session:
            write(session, kind, [{"id": identity(kind), "original": True}])
            session.commit()
    barrier = Barrier(2)

    def worker(patch: Mapping[str, object]) -> int:
        with Session(database) as session:
            barrier.wait(timeout=10)
            result = write(session, kind, [{"id": identity(kind), **patch}])
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, patch) for patch in ({metric(kind): 0}, {"correction": 7})]
        assert [future.result(timeout=20) for future in futures] == [1, 1]
    with Session(database) as session:
        stored = row(session, kind)
        assert stored.payload[metric(kind)] == 0
        assert stored.payload["correction"] == 7
        if existing:
            assert stored.payload["original"] is True
        assert (stored.training_load if isinstance(stored, Activity) else stored.ctl) == 0
