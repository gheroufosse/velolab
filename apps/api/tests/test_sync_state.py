"""Sync-state service through migrated disposable PostgreSQL, no real provider.

Failure inventory (trigger -> invariant/recovery; all DB cases integrated):
- First/existing simultaneous claims -> one committed winner, busy is read-only.
- Crash/expired token -> takeover with fresh token; old holder cannot write or
  release, even before takeover. DB wall-clock, not transaction time, governs.
- Stale/mismatched owner -> no markers or domain writes; fence before upsert and
  roll back the short transaction when an outcome loses its lease.
- Failed provider/persistence attempt -> prior success/backfill/preview markers
  survive; only static allowlisted error codes, never exception/provider text.
- Complete or truncated preview -> separate recent-window markers; never advance
  full-sync freshness/backfill; failure preserves the last preview outcome.
- Schema drift/duplicate/orphan/cross-owner state -> migration enforces one owned
  row, defaults, paired leases, valid windows and cascades; reversible migration.
- Throttle: null, just-under/exact/over 24h, future timestamp and manual force ->
  decide from full success only. Pure isolated test is needed for clock boundary
  arithmetic; naive times/invalid policy must fail, not silently guess a zone.
No HTTP, retries, auth endpoints, provider completeness or backfill scheduling
in this slice. Cancellation is caller rollback/lease expiry, not a new worker.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, delete, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api.intervals_client import ActivityRecord, ErrorCode
from velolab_api.intervals_upsert import upsert_activities
from velolab_api.models import Activity, IntegrationSyncState, User, UserIntegration
from velolab_api.sync_state import (
    LeaseLostError,
    SyncErrorCode,
    SyncPolicy,
    SyncStateError,
    SyncStateService,
    should_sync,
)

DAY = date(2026, 10, 8)


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


def service(session: Session) -> SyncStateService:
    integration = link(session)
    return SyncStateService(session, user_id=integration.user_id, integration_id=integration.id)


def state(session: Session) -> IntegrationSyncState:
    return session.scalars(
        select(IntegrationSyncState).where(IntegrationSyncState.integration_id == link(session).id)
    ).one()


def claim(database: Engine) -> UUID:
    with Session(database) as session:
        token = service(session).claim_lease()
        assert token is not None
        session.commit()
        return token


def expire(session: Session) -> None:
    # No sleeps or client-supplied production clock: simulate crash/expiry in DB.
    session.execute(
        update(IntegrationSyncState).values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
    )
    session.commit()


@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_claims_have_one_winner_and_busy_does_not_change_attempt(
    database: Engine, existing: bool
) -> None:
    if existing:
        token = claim(database)
        with Session(database) as session:
            service(session).release_lease(token)
            session.commit()
    barrier = Barrier(2)

    def worker() -> UUID | None:
        with Session(database) as session:
            lease = service(session)
            barrier.wait(timeout=10)
            token = lease.claim_lease()
            session.commit()
            return token

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        tokens = [future.result(timeout=20) for future in futures]
    winners = [token for token in tokens if token is not None]
    assert len(winners) == 1
    with Session(database) as session:
        stored = state(session)
        assert stored.lease_token == winners[0]
        assert stored.last_attempt_status == "running"
        assert stored.last_attempt_at is not None
        assert stored.lease_expires_at is not None
        assert stored.lease_expires_at > stored.last_attempt_at
        previous = (stored.last_attempt_at, stored.lease_expires_at)
        # Force bypasses the throttle but does not even enter the lease API.
        assert should_sync(datetime.now(UTC), now=datetime.now(UTC), force=True)
        assert service(session).claim_lease() is None
        session.commit()
    with Session(database) as session:
        stored = state(session)
        assert (stored.last_attempt_at, stored.lease_expires_at) == previous
        assert session.scalars(select(IntegrationSyncState)).all() == [stored]


def test_expiry_takeover_fences_all_stale_writes_and_domain_transaction(database: Engine) -> None:
    stale = claim(database)
    with Session(database) as session:
        expire(session)
        # Expired holders fail even when nobody has taken over yet.
        with pytest.raises(LeaseLostError):
            service(session).record_success(stale)
        session.rollback()
    winner = claim(database)
    assert stale != winner
    with Session(database) as session:
        lease = service(session)
        for action in (
            lambda: lease.fence_lease(stale),
            lambda: lease.record_checkpoint(stale, DAY, complete=True),
            lambda: lease.record_success(stale),
            lambda: lease.record_failure(stale, ErrorCode.TRANSPORT),
            lambda: lease.record_preview_outcome(
                stale, oldest=DAY, newest=DAY, possibly_truncated=False
            ),
            lambda: lease.release_lease(stale),
        ):
            with pytest.raises(LeaseLostError, match="^lease_lost$"):
                action()
        session.commit()
    with Session(database) as session, pytest.raises(LeaseLostError), session.begin():
        lease = service(session)
        lease.fence_lease(stale)
        integration = link(session)
        upsert_activities(
            session,
            user_id=integration.user_id,
            integration=integration,
            records=[ActivityRecord("a", None, None, None, {"id": "a"})],
        )
    # Expiry after fencing/upsert but before outcome must also roll back records.
    with Session(database) as session, pytest.raises(LeaseLostError), session.begin():
        lease = service(session)
        lease.fence_lease(winner)
        integration = link(session)
        upsert_activities(
            session,
            user_id=integration.user_id,
            integration=integration,
            records=[ActivityRecord("a", None, None, None, {"id": "a"})],
        )
        # Expiry is after transaction-start time but before this outcome. Using
        # PostgreSQL now() would incorrectly allow the write in this transaction.
        session.execute(
            update(IntegrationSyncState).values(lease_expires_at=func.clock_timestamp())
        )
        lease.record_preview_outcome(winner, oldest=DAY, newest=DAY, possibly_truncated=False)
    with Session(database) as session:
        assert session.scalars(select(Activity)).all() == []
        stored = state(session)
        assert stored.lease_token == winner
        assert stored.last_success_at is None
        assert stored.last_preview_at is None
        assert stored.backfill_checkpoint is None
        assert stored.backfill_complete is False
        service(session).release_lease(winner)
        session.commit()
    assert claim(database) not in (stale, winner)


@pytest.mark.parametrize("possibly_truncated", [False, True])
def test_preview_and_failure_preserve_full_sync_markers(
    database: Engine, possibly_truncated: bool
) -> None:
    full = claim(database)
    with Session(database) as session:
        lease = service(session)
        lease.record_checkpoint(full, DAY, complete=True)
        lease.record_success(full)
        lease.release_lease(full)
        session.commit()
    with Session(database) as session:
        stored = state(session)
        success = stored.last_success_at
        assert success is not None and success.utcoffset() is not None
    preview = claim(database)
    with Session(database) as session, session.begin():
        lease = service(session)
        lease.fence_lease(preview)
        integration = link(session)
        upsert_activities(
            session,
            user_id=integration.user_id,
            integration=integration,
            records=[ActivityRecord("a", None, None, None, {"id": "a", "name": "synthetic"})],
        )
        lease.record_preview_outcome(
            preview,
            oldest=DAY - timedelta(days=29),
            newest=DAY,
            possibly_truncated=possibly_truncated,
        )
        lease.release_lease(preview)
    with Session(database) as session:
        stored = state(session)
        preview_at = stored.last_preview_at
        assert preview_at is not None and preview_at.utcoffset() is not None
        assert (stored.preview_oldest, stored.preview_newest) == (DAY - timedelta(days=29), DAY)
        assert stored.possibly_truncated is possibly_truncated
        assert stored.last_attempt_status == (
            "preview_partial" if possibly_truncated else "preview_completed"
        )
        assert (stored.last_success_at, stored.backfill_checkpoint, stored.backfill_complete) == (
            success,
            DAY,
            True,
        )
        assert session.scalars(select(Activity)).one().payload["name"] == "synthetic"
    failure = claim(database)
    with Session(database) as session:
        lease = service(session)
        with pytest.raises(SyncStateError, match="^invalid_error_code$"):
            lease.record_failure(failure, cast(ErrorCode, "provider text with credentials"))
        lease.record_failure(failure, ErrorCode.RATE_LIMIT)
        lease.release_lease(failure)
        session.commit()
    with Session(database) as session:
        stored = state(session)
        assert stored.last_attempt_status == "failed"
        assert stored.last_error_code == "rate_limited"
        assert (stored.lease_token, stored.lease_expires_at) == (None, None)
        assert stored.last_success_at == success
        assert (stored.backfill_checkpoint, stored.backfill_complete) == (DAY, True)
        assert stored.last_preview_at == preview_at
        assert (stored.preview_oldest, stored.preview_newest) == (DAY - timedelta(days=29), DAY)
        assert stored.possibly_truncated is possibly_truncated
        assert session.scalars(select(Activity)).one().payload["name"] == "synthetic"


def test_preview_alone_never_marks_full_sync_success(database: Engine) -> None:
    token = claim(database)
    with Session(database) as session:
        lease = service(session)
        lease.record_preview_outcome(token, oldest=DAY, newest=DAY, possibly_truncated=True)
        lease.release_lease(token)
        session.commit()
    with Session(database) as session:
        stored = state(session)
        assert stored.last_success_at is None
        assert stored.backfill_checkpoint is None
        assert stored.backfill_complete is False
        assert should_sync(stored.last_success_at, now=datetime.now(UTC))


def test_owner_scoping_and_invalid_inputs_leave_state_unchanged(database: Engine) -> None:
    token = claim(database)
    with Session(database) as session:
        rider, friend = link(session), link(session, "friend")
        wrong = SyncStateService(session, user_id=friend.user_id, integration_id=rider.id)
        missing = SyncStateService(session, user_id=rider.user_id, integration_id=uuid4())
        for lease in (wrong, missing):
            with pytest.raises(SyncStateError, match="^integration_owner_mismatch$"):
                lease.claim_lease()
            with pytest.raises(LeaseLostError):
                lease.record_failure(token, SyncErrorCode.PERSISTENCE)
        lease = service(session)
        with pytest.raises(SyncStateError, match="^invalid_preview_window$"):
            lease.record_preview_outcome(
                token, oldest=DAY + timedelta(days=1), newest=DAY, possibly_truncated=False
            )
        session.commit()
    with Session(database) as session:
        assert len(session.scalars(select(IntegrationSyncState)).all()) == 1
        assert state(session).lease_token == token
        assert state(session).last_attempt_status == "running"


def test_sync_state_migration_defaults_constraints_cascade_and_reversal(
    isolated_database: tuple[Config, Engine],
) -> None:
    config, engine = isolated_database
    command.upgrade(config, "head")
    command.check(config)
    with Session(engine) as session:
        owner = User(email="rider@example.com", password_hash="synthetic")
        friend = User(email="friend@example.com", password_hash="synthetic")
        session.add_all([owner, friend])
        session.flush()
        integration = UserIntegration(
            user_id=owner.id,
            provider="intervals.icu",
            external_athlete_id="synthetic",
            encrypted_api_key="synthetic",
        )
        session.add(integration)
        session.flush()
        owner_id, friend_id, integration_id = owner.id, friend.id, integration.id
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(IntegrationSyncState(user_id=friend_id, integration_id=integration_id))
            session.flush()
        stored = IntegrationSyncState(user_id=owner_id, integration_id=integration_id)
        session.add(stored)
        session.commit()
        assert stored.backfill_complete is False
        assert stored.possibly_truncated is False
        assert stored.last_success_at is None
        assert stored.last_preview_at is None
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO integration_sync_state (user_id, integration_id) VALUES (:u, :i)"
                ),
                {"u": owner_id, "i": integration_id},
            )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(update(IntegrationSyncState).values(lease_token=uuid4()))
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(update(IntegrationSyncState).values(preview_oldest=DAY))
        session.execute(delete(User).where(User.id == owner_id))
        session.commit()
    with Session(engine) as session:
        assert session.scalars(select(IntegrationSyncState)).all() == []
    command.downgrade(config, "8d294a62b103")
    assert not inspect(engine).has_table("integration_sync_state")
    assert inspect(engine).has_table("activities")
    with Session(engine) as session:
        assert session.get(User, friend_id) is not None
    command.upgrade(config, "head")
    command.check(config)


def test_pure_throttle_boundary_force_and_invalid_clocks() -> None:
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    assert should_sync(None, now=now)
    assert not should_sync(now - timedelta(hours=24) + timedelta(microseconds=1), now=now)
    assert should_sync(now - timedelta(hours=24), now=now)
    assert should_sync(now - timedelta(hours=24, microseconds=1), now=now)
    assert not should_sync(now + timedelta(days=1), now=now)
    assert should_sync(now, now=now, force=True)
    assert should_sync(
        now - timedelta(hours=1),
        now=now,
        policy=SyncPolicy(automatic_interval=timedelta(hours=1)),
    )
    with pytest.raises(SyncStateError, match="^invalid_sync_time$"):
        should_sync(None, now=now.replace(tzinfo=None))
    with pytest.raises(SyncStateError, match="^invalid_sync_time$"):
        should_sync(now.replace(tzinfo=None), now=now)
    with pytest.raises(SyncStateError, match="^invalid_sync_policy$"):
        SyncPolicy(lease_ttl=timedelta(0))
