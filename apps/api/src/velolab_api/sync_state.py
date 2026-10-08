"""Owned sync-state primitives (ADR-024/025), without HTTP or orchestration.

The caller owns transactions: commit a claim before network work, then start a
SHORT transaction, fence_lease before domain upserts, record the outcome and
release. Any exception must roll back that transaction, including activity
writes. No commit here and no network-spanning locks. Checkpoints, outcomes and
release all require the same unexpired token; a crash recovers through expiry.
PostgreSQL wall-clock time (not transaction-start now()) enforces expiry even
when a transaction began earlier or waited for another holder's row lock.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from velolab_api.intervals_client import ErrorCode
from velolab_api.models import IntegrationSyncState, UserIntegration


class SyncStateError(ValueError):
    """Static local validation codes only."""


class LeaseLostError(SyncStateError):
    def __init__(self) -> None:
        super().__init__("lease_lost")


class SyncErrorCode(StrEnum):
    INVALID_TIMEZONE = "invalid_timezone"
    INVALID_RECORDS = "invalid_records"
    PERSISTENCE = "persistence_failure"
    DEADLINE = "deadline_exceeded"


@dataclass(frozen=True, slots=True)
class SyncPolicy:
    # ADR-024 assumptions, not provider facts. Preview orchestration must choose
    # a TTL longer than its explicit network/request budget (ADR-025).
    lease_ttl: timedelta = timedelta(minutes=5)
    automatic_interval: timedelta = timedelta(hours=24)  # ADR-007

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, timedelta) or value <= timedelta(0)
            for value in (self.lease_ttl, self.automatic_interval)
        ):
            raise SyncStateError("invalid_sync_policy")


DEFAULT_SYNC_POLICY = SyncPolicy()


def should_sync(
    last_success_at: datetime | None,
    *,
    now: datetime,
    force: bool = False,
    policy: SyncPolicy = DEFAULT_SYNC_POLICY,
) -> bool:
    """Pure full-sync throttle; preview/attempt/error timestamps never enter it.

    Exactly 24 hours is eligible. A future success timestamp remains throttled.
    Manual force changes only this decision, never persisted lease exclusion.
    """
    if now.utcoffset() is None or (
        last_success_at is not None and last_success_at.utcoffset() is None
    ):
        raise SyncStateError("invalid_sync_time")
    return force or last_success_at is None or now - last_success_at >= policy.automatic_interval


class SyncStateService:
    def __init__(
        self,
        session: Session,
        *,
        user_id: UUID,
        integration_id: UUID,
        policy: SyncPolicy = DEFAULT_SYNC_POLICY,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.integration_id = integration_id
        self.policy = policy

    def _owned(self) -> tuple[ColumnElement[bool], ...]:
        return (
            IntegrationSyncState.integration_id == self.integration_id,
            IntegrationSyncState.user_id == self.user_id,
        )

    def _fenced(self, lease_token: UUID) -> tuple[ColumnElement[bool], ...]:
        if not isinstance(lease_token, UUID):
            raise LeaseLostError()
        return (
            *self._owned(),
            IntegrationSyncState.lease_token == lease_token,
            IntegrationSyncState.lease_expires_at > func.clock_timestamp(),
        )

    def claim_lease(self) -> UUID | None:
        """Return a fresh token or None if busy; caller must commit the claim.

        Lazily initialize state from the owned integration only. ON CONFLICT
        serializes simultaneous first claims as well as existing-row claims.
        """
        with self.session.no_autoflush:
            self.session.execute(
                insert(IntegrationSyncState)
                .from_select(
                    ["integration_id", "user_id"],
                    select(UserIntegration.id, UserIntegration.user_id).where(
                        UserIntegration.id == self.integration_id,
                        UserIntegration.user_id == self.user_id,
                    ),
                )
                .on_conflict_do_nothing(index_elements=["integration_id"])
            )
            owned = self.session.scalar(
                select(IntegrationSyncState.integration_id).where(*self._owned())
            )
            if owned is None:
                raise SyncStateError("integration_owner_mismatch")
            token = uuid4()
            return self.session.scalar(
                update(IntegrationSyncState)
                .where(
                    *self._owned(),
                    or_(
                        IntegrationSyncState.lease_token.is_(None),
                        IntegrationSyncState.lease_expires_at <= func.clock_timestamp(),
                    ),
                )
                .values(
                    lease_token=token,
                    lease_expires_at=func.clock_timestamp() + self.policy.lease_ttl,
                    last_attempt_at=func.clock_timestamp(),
                    last_attempt_status="running",
                    last_error_code=None,
                )
                .returning(IntegrationSyncState.lease_token)
                .execution_options(synchronize_session=False)
            )

    def fence_lease(self, lease_token: UUID) -> None:
        """Lock an owned, unexpired lease before any domain writes in this txn.

        Always record an outcome before committing, so expiry during domain
        writes is rechecked and must roll back the entire transaction.
        """
        with self.session.no_autoflush:
            locked = self.session.scalar(
                select(IntegrationSyncState.integration_id)
                .where(*self._fenced(lease_token))
                .with_for_update()
            )
            # A lock wait can outlive the TTL even when the other transaction
            # leaves the tuple unchanged (so PostgreSQL need not re-evaluate
            # the original WHERE). Recheck wall-clock AFTER acquiring the lock.
            if (
                locked is None
                or self.session.scalar(
                    select(IntegrationSyncState.integration_id).where(*self._fenced(lease_token))
                )
                is None
            ):
                raise LeaseLostError()

    def _write(self, token: UUID, **values: object) -> None:
        self.fence_lease(token)
        with self.session.no_autoflush:
            written = self.session.scalar(
                update(IntegrationSyncState)
                .where(*self._fenced(token))
                .values(**values)
                .returning(IntegrationSyncState.integration_id)
                .execution_options(synchronize_session=False)
            )
        if written is None:
            raise LeaseLostError()

    def release_lease(self, lease_token: UUID) -> None:
        self._write(lease_token, lease_token=None, lease_expires_at=None)

    def record_checkpoint(
        self, lease_token: UUID, checkpoint: date, *, complete: bool = False
    ) -> None:
        # Provider-local checkpoint only; window direction/completeness remains
        # deferred to evidenced full-sync orchestration, not this primitive.
        if type(checkpoint) is not date or type(complete) is not bool:
            raise SyncStateError("invalid_checkpoint")
        self._write(lease_token, backfill_checkpoint=checkpoint, backfill_complete=complete)

    def record_success(self, lease_token: UUID) -> None:
        """FULL sync only. Preview callers must use record_preview_outcome."""
        self._write(
            lease_token,
            last_success_at=func.clock_timestamp(),
            last_attempt_status="succeeded",
            last_error_code=None,
        )

    def record_failure(self, lease_token: UUID, code: ErrorCode | SyncErrorCode) -> None:
        if not isinstance(code, (ErrorCode, SyncErrorCode)):
            raise SyncStateError("invalid_error_code")
        self._write(lease_token, last_attempt_status="failed", last_error_code=code.value)

    def record_preview_outcome(
        self,
        lease_token: UUID,
        *,
        oldest: date,
        newest: date,
        possibly_truncated: bool,
    ) -> None:
        """Recent fetch completed/partial, never full-sync or backfill success.

        Even a nontruncated listing is NOT evidence of provider completeness.
        Failure uses record_failure and preserves the previous preview markers.
        """
        if (
            type(oldest) is not date
            or type(newest) is not date
            or oldest > newest
            or type(possibly_truncated) is not bool
        ):
            raise SyncStateError("invalid_preview_window")
        self._write(
            lease_token,
            last_preview_at=func.clock_timestamp(),
            preview_oldest=oldest,
            preview_newest=newest,
            possibly_truncated=possibly_truncated,
            last_attempt_status="preview_partial" if possibly_truncated else "preview_completed",
            last_error_code=None,
        )
