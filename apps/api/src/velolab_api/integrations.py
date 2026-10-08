"""Owner-only local connection and recent-sync preview (ADR-025)."""

from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from velolab_api.auth import SessionDependency, get_current_user
from velolab_api.integration_secrets import IntegrationKeyCipher, IntegrationSecretError
from velolab_api.intervals_client import (
    AthleteProfile,
    ErrorCode,
    IntervalsClient,
    IntervalsClientError,
)
from velolab_api.models import IntegrationSyncState, User, UserIntegration
from velolab_api.preview_sync import (
    PreviewResult,
    PreviewStatus,
    PreviewSyncError,
    preview_status,
    sync_preview,
)
from velolab_api.settings import IntegrationKeySettings, Settings, get_settings

router = APIRouter(prefix="/integrations/intervals", tags=["integrations"])


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=4096, repr=False)
    athlete_id: str = Field(min_length=1, max_length=255)

    @field_validator("api_key")
    @classmethod
    def bounded_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) > 4096:
            raise ValueError("Invalid credential")
        return value


class ConnectionMetadata(PreviewStatus):
    configured: bool
    athlete_id: str | None


class ConnectionTestResult(BaseModel):
    athlete_id: str
    timezone: str | None


def get_preview_owner(
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    # Trust only configured origin, never Host/forwarded headers or DNS resolution.
    host = urlsplit(settings.auth_trusted_origin or "").hostname
    try:
        loopback = host == "localhost" or (host is not None and ip_address(host).is_loopback)
    except ValueError:
        loopback = False
    if (
        not settings.integration_preview_enabled
        or settings.integration_preview_owner_id is None
        or settings.cookie_secure is None
        or not loopback
    ):
        raise HTTPException(503, "preview_unavailable")
    if user.id != settings.integration_preview_owner_id:
        raise HTTPException(403, "forbidden")
    return user


PreviewOwner = Annotated[User, Depends(get_preview_owner)]


def get_integration_cipher() -> IntegrationKeyCipher:
    try:
        return IntegrationKeyCipher(IntegrationKeySettings())
    except IntegrationSecretError, ValidationError:
        pass
    raise HTTPException(503, "credentials_unavailable")


Cipher = Annotated[IntegrationKeyCipher, Depends(get_integration_cipher)]


def get_intervals_transport() -> httpx.BaseTransport | None:
    """Default real transport; dependency override permits offline contract tests."""
    return None


Transport = Annotated[httpx.BaseTransport | None, Depends(get_intervals_transport)]


def verify_profile(
    credentials: ConnectionRequest, transport: httpx.BaseTransport | None
) -> AthleteProfile:
    failure = ErrorCode.INVALID_INPUT
    try:
        with IntervalsClient(
            credentials.api_key, credentials.athlete_id, transport=transport
        ) as client:
            return client.athlete_profile()  # Client verifies exact identity, not alias/display ID.
    except IntervalsClientError as error:
        failure = error.code
    raise HTTPException(422 if failure == ErrorCode.INVALID_INPUT else 502, failure.value)


def metadata(session: Session, user_id: UUID) -> ConnectionMetadata:
    row = session.scalar(
        select(UserIntegration).where(
            UserIntegration.user_id == user_id, UserIntegration.provider == "intervals"
        )
    )
    state = preview_status(session, user_id, row.id) if row is not None else PreviewStatus()
    return ConnectionMetadata(
        configured=row is not None,
        athlete_id=row.external_athlete_id if row is not None else None,
        **state.model_dump(),
    )


@router.get("", response_model=ConnectionMetadata)
def get_connection(user: PreviewOwner, session: SessionDependency) -> ConnectionMetadata:
    try:
        return metadata(session, user.id)
    except SQLAlchemyError:
        session.rollback()
    raise HTTPException(503, "persistence_failure")


@router.post("/sync-now", response_model=PreviewResult)
def sync_now(
    user: PreviewOwner, session: SessionDependency, cipher: Cipher, transport: Transport
) -> PreviewResult:
    user_id = user.id
    session.rollback()  # End the auth read transaction before claiming.
    failure = None
    try:
        return sync_preview(session, user_id=user_id, cipher=cipher, transport=transport)
    except PreviewSyncError as error:
        failure = (error.status, error.code)
    raise HTTPException(*failure)


@router.post("/test", response_model=ConnectionTestResult)
def test_connection(
    credentials: ConnectionRequest,
    user: PreviewOwner,
    session: SessionDependency,
    cipher: Cipher,
    transport: Transport,
) -> ConnectionTestResult:
    # Auth resolved the user in a read transaction. End it before network work.
    session.rollback()
    profile = verify_profile(credentials, transport)
    return ConnectionTestResult(athlete_id=profile.id, timezone=profile.timezone)


@router.put("", response_model=ConnectionMetadata)
def save_connection(
    credentials: ConnectionRequest,
    user: PreviewOwner,
    session: SessionDependency,
    cipher: Cipher,
    transport: Transport,
) -> ConnectionMetadata:
    user_id = user.id
    session.rollback()
    profile = verify_profile(credentials, transport)  # Never trust a browser's prior test.
    failure = "persistence_failure"
    try:
        # The stable owner row serializes simultaneous first enrollment as well
        # as replacements, without holding any lock during provider verification.
        session.scalar(select(User.id).where(User.id == user_id).with_for_update())
        row = session.scalar(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id, UserIntegration.provider == "intervals"
            )
        )
        if row is not None and row.external_athlete_id != profile.id:
            raise HTTPException(409, "athlete_rebinding")
        if row is None:
            row = UserIntegration(
                id=uuid4(), user_id=user_id, provider="intervals", external_athlete_id=profile.id
            )
            row.encrypted_api_key = cipher.encrypt(row, credentials.api_key).get_secret_value()
            session.add(row)
            session.flush()
        else:
            # Initialize missing state, then lock the same row used by lease
            # claims. This also serializes against a concurrent first claim.
            session.execute(
                insert(IntegrationSyncState)
                .values(integration_id=row.id, user_id=user_id)
                .on_conflict_do_nothing(index_elements=["integration_id"])
            )
            session.scalar(
                select(IntegrationSyncState.integration_id)
                .where(IntegrationSyncState.integration_id == row.id)
                .with_for_update()
            )
            busy = session.scalar(
                select(IntegrationSyncState.integration_id).where(
                    IntegrationSyncState.integration_id == row.id,
                    IntegrationSyncState.lease_token.is_not(None),
                    IntegrationSyncState.lease_expires_at > func.clock_timestamp(),
                )
            )
            if busy is not None:
                raise HTTPException(409, "integration_busy")
            row.encrypted_api_key = cipher.encrypt(row, credentials.api_key).get_secret_value()
        result = metadata(session, user_id)
        session.commit()
        return result
    except HTTPException:
        session.rollback()
        raise
    except IntegrationSecretError:
        session.rollback()
        failure = "credentials_unavailable"
    except SQLAlchemyError:
        session.rollback()
    raise HTTPException(503, failure)
