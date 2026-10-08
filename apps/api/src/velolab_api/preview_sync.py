"""One bounded recent activity preview, never full-sync/backfill orchestration."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, SQLAlchemyError
from sqlalchemy.orm import Session

from velolab_api.integration_secrets import IntegrationKeyCipher, IntegrationSecretError
from velolab_api.intervals_client import (
    ErrorCode,
    IntervalsClient,
    IntervalsClientError,
    IntervalsClientPolicy,
)
from velolab_api.intervals_upsert import UpsertError, upsert_activities
from velolab_api.models import IntegrationSyncState, UserIntegration
from velolab_api.sync_state import LeaseLostError, SyncErrorCode, SyncPolicy, SyncStateService


class PreviewStatus(BaseModel):
    last_preview_at: datetime | None = None
    preview_oldest: date | None = None
    preview_newest: date | None = None
    possibly_truncated: bool = False
    last_attempt_status: str | None = None
    last_error_code: str | None = None


def preview_status(session: Session, user_id: UUID, integration_id: UUID) -> PreviewStatus:
    row = session.scalar(
        select(IntegrationSyncState)
        .where(
            IntegrationSyncState.user_id == user_id,
            IntegrationSyncState.integration_id == integration_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return PreviewStatus()
    codes = {code.value for code in (*ErrorCode, *SyncErrorCode)}
    statuses = {"running", "failed", "preview_partial", "preview_completed", "succeeded"}
    return PreviewStatus(
        last_preview_at=row.last_preview_at,
        preview_oldest=row.preview_oldest,
        preview_newest=row.preview_newest,
        possibly_truncated=row.possibly_truncated,
        last_attempt_status=row.last_attempt_status
        if row.last_attempt_status in statuses
        else None,
        last_error_code=row.last_error_code if row.last_error_code in codes else None,
    )


class PreviewResult(BaseModel):
    synced_count: int  # Inserted/changed rows, not sightings; identical rerun returns zero.
    possibly_truncated: bool
    last_preview_at: datetime
    last_error_code: str | None = None


class PreviewSyncError(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code
        super().__init__(code)


@dataclass(frozen=True)
class PreviewPolicy:
    # ADR-025 assumptions, not provider completeness guarantees. One window only.
    window_days: int = 30
    deadline_seconds: float = 90
    sync: SyncPolicy = field(default_factory=SyncPolicy)  # 300s > budget + in-flight timeout
    client: IntervalsClientPolicy = field(default_factory=IntervalsClientPolicy)
    statement_timeout_ms: int = 10_000

    def __post_init__(self) -> None:
        if (
            type(self.window_days) is not int
            or not 1 <= self.window_days <= 30
            or type(self.statement_timeout_ms) is not int
            or not 0 < self.statement_timeout_ms <= 10_000
            or not self.deadline_seconds > 0
            or self.deadline_seconds
            + 4 * self.client.timeout_seconds
            + self.statement_timeout_ms / 1000
            >= self.sync.lease_ttl.total_seconds()
        ):
            raise ValueError("invalid_preview_policy")


DEFAULT_PREVIEW_POLICY = PreviewPolicy()


def _bound_database_wait(session: Session, policy: PreviewPolicy) -> None:
    # LOCAL applies only to this short transaction, including lock waits.
    session.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": str(policy.statement_timeout_ms)},
    )


def sync_preview(
    session: Session,
    *,
    user_id: UUID,
    cipher: IntegrationKeyCipher,
    transport: httpx.BaseTransport | None,
    policy: PreviewPolicy = DEFAULT_PREVIEW_POLICY,
) -> PreviewResult:
    deadline = monotonic() + policy.deadline_seconds
    started_at = datetime.now(UTC)
    service = None
    token = None
    failure: ErrorCode | SyncErrorCode = SyncErrorCode.PERSISTENCE
    status = 503
    try:
        _bound_database_wait(session, policy)
        row = session.scalar(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id, UserIntegration.provider == "intervals"
            )
        )
        if row is None:
            raise PreviewSyncError(409, "integration_not_configured")
        service = SyncStateService(
            session, user_id=user_id, integration_id=row.id, policy=policy.sync
        )
        token = service.claim_lease()
        if token is None:
            raise PreviewSyncError(409, "integration_busy")
        # Replacement uses the same state lock. Reload AFTER claim to snapshot
        # any replacement that committed before us, then detach before commit.
        session.refresh(row)
        session.expunge(row)
        session.commit()
        key = cipher.decrypt(row)
        with IntervalsClient(
            key,
            row.external_athlete_id,
            transport=transport,
            policy=policy.client,
            deadline=deadline,
        ) as client:
            profile = client.athlete_profile()  # Exact identity verified by client.
            try:
                if not profile.timezone:
                    raise ValueError
                zone = ZoneInfo(profile.timezone)
            except ZoneInfoNotFoundError, ValueError:
                raise IntervalsClientError(ErrorCode.INVALID_TIMEZONE) from None
            newest = started_at.astimezone(zone).date()
            oldest = newest - timedelta(days=policy.window_days - 1)
            listing = client.activities(oldest, newest)
        if monotonic() >= deadline:
            raise IntervalsClientError(ErrorCode.DEADLINE)
        # All domain writes/outcome/release commit together, never over network.
        _bound_database_wait(session, policy)
        service.fence_lease(token)
        changed = upsert_activities(
            session, user_id=user_id, integration=row, records=listing.records
        )
        if monotonic() >= deadline:
            raise IntervalsClientError(ErrorCode.DEADLINE)
        service.record_preview_outcome(
            token,
            oldest=oldest,
            newest=newest,
            possibly_truncated=listing.possibly_truncated,
        )
        service.release_lease(token)
        state = preview_status(session, user_id, row.id)
        assert state.last_preview_at is not None
        result = PreviewResult(
            synced_count=changed,
            possibly_truncated=state.possibly_truncated,
            last_preview_at=state.last_preview_at,
        )
        session.commit()
        return result
    except PreviewSyncError:
        session.rollback()
        raise
    except LeaseLostError:
        session.rollback()
        raise PreviewSyncError(409, "lease_lost") from None
    except IntegrationSecretError:
        failure = SyncErrorCode.CREDENTIALS
    except IntervalsClientError as error:
        failure, status = error.code, 502
    except UpsertError, DataError:
        failure, status = SyncErrorCode.INVALID_RECORDS, 502
    except SQLAlchemyError:
        pass
    session.rollback()  # Discard EVERY domain write before recording failure.
    if service is not None and token is not None:
        try:
            _bound_database_wait(session, policy)
            service.record_failure(token, failure)
            service.release_lease(token)
            session.commit()
        except LeaseLostError:
            session.rollback()  # Never alter a newer holder's outcome/lease.
            raise PreviewSyncError(409, "lease_lost") from None
        except SQLAlchemyError:
            session.rollback()
            failure, status = SyncErrorCode.PERSISTENCE, 503
    raise PreviewSyncError(status, failure.value)
