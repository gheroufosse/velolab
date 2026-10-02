"""Password login, short-lived access JWTs, and rotating cookie sessions."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from re import fullmatch
from secrets import token_urlsafe
from typing import Annotated, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from velolab_api.db import get_session
from velolab_api.models import AuthSession, RefreshToken, User
from velolab_api.provisioning import normalize_email
from velolab_api.settings import Settings, get_settings

ACCESS_LIFETIME_SECONDS = 600
REFRESH_LIFETIME_SECONDS = 7 * 24 * 60 * 60
COOKIE_NAME = "velolab_refresh"
ALGORITHM = "HS256"
ISSUER = "velolab-api"
AUDIENCE = "velolab-api"
_PASSWORD_HASHER = PasswordHasher(type=Type.ID)
# A missing account still performs an Argon2id verification. This is not a
# credential or signing secret, and can never authenticate without a DB user.
_DUMMY_HASH = _PASSWORD_HASHER.hash("dummy verification, not an account password")
_BEARER = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])
SessionDependency = Annotated[Session, Depends(get_session)]


class LoginRequest(BaseModel):
    email: str = Field(max_length=1024)
    password: str = Field(max_length=128, repr=False)


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = ACCESS_LIFETIME_SECONDS


class IdentityResponse(BaseModel):
    id: UUID
    email: str


def get_signing_key(settings: Annotated[Settings, Depends(get_settings)]) -> str:
    secret = settings.auth_jwt_secret
    if secret is None or len(secret.get_secret_value().encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return secret.get_secret_value()


SigningKey = Annotated[str, Depends(get_signing_key)]


def trusted_cookie_settings(settings: Annotated[Settings, Depends(get_settings)]) -> Settings:
    if settings.cookie_secure is None or not fullmatch(
        r"/(?:[a-zA-Z0-9_-]+/)*auth", settings.auth_cookie_path
    ):
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return settings


CookieSettings = Annotated[Settings, Depends(trusted_cookie_settings)]


def verify_csrf(
    settings: CookieSettings,
    origin: Annotated[str | None, Header()] = None,
    x_velolab_csrf: Annotated[str | None, Header()] = None,
) -> None:
    if origin != settings.auth_trusted_origin or x_velolab_csrf != "1":
        raise HTTPException(status_code=403, detail="Forbidden")


CsrfCheck = Annotated[None, Depends(verify_csrf)]


def set_refresh_cookie(response: Response, token: str, max_age: int, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max_age,
        path=settings.auth_cookie_path,
        secure=bool(settings.cookie_secure),
        httponly=True,
        samesite="strict",
    )


def session_expiry(parent: AuthSession) -> datetime:
    # SQLite test storage drops timezone info; PostgreSQL returns aware UTC.
    expiry = parent.expires_at
    return expiry if expiry.tzinfo is not None else expiry.replace(tzinfo=UTC)


def hash_refresh(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def issue_access(user: User, signing_key: str) -> AccessTokenResponse:
    issued_at = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {
            "sub": str(user.id),
            "iat": issued_at,
            "exp": issued_at + ACCESS_LIFETIME_SECONDS,
            "token_use": "access",
            "iss": ISSUER,
            "aud": AUDIENCE,
        },
        signing_key,
        algorithm=ALGORITHM,
    )
    return AccessTokenResponse(access_token=token)


def lock_token_session(session: Session, token: str) -> tuple[RefreshToken, AuthSession] | None:
    # Resolve by digest first, then lock the stable parent row. A second waiter
    # reloads the token after the lock to observe a concurrent rotation/revoke.
    # token_urlsafe(32) is 43 URL-safe characters. Reject malformed/oversized
    # cookies before hashing or doing any database work.
    if len(token) != 43 or not fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    digest = hash_refresh(token)
    found = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest))
    if found is None:
        return None
    parent = session.scalar(
        select(AuthSession).where(AuthSession.id == found.session_id).with_for_update()
    )
    if parent is None:
        return None
    current = session.scalar(
        select(RefreshToken)
        .where(RefreshToken.id == found.id)
        .execution_options(populate_existing=True)
    )
    if current is None or current.user_id != parent.user_id:
        return None
    return current, parent


def unauthorized(detail: str = "Invalid access token") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


@router.post("/login", response_model=AccessTokenResponse)
def login(
    credentials: LoginRequest,
    response: Response,
    signing_key: SigningKey,
    session: SessionDependency,
    settings: CookieSettings,
    csrf: CsrfCheck,
) -> AccessTokenResponse:
    try:
        email = normalize_email(credentials.email)
    except EmailNotValidError:
        email = None
    user = session.scalar(select(User).where(User.email == email)) if email is not None else None
    password_hash = user.password_hash if user is not None else _DUMMY_HASH
    try:
        password_matches = _PASSWORD_HASHER.verify(password_hash, credentials.password)
    except VerificationError, InvalidHashError:
        password_matches = False
    if user is None or not password_matches or len(credentials.password) < 10:
        raise unauthorized("Invalid email or password")

    access = issue_access(user, signing_key)
    now = datetime.now(UTC)
    refresh = token_urlsafe(32)
    auth_session = AuthSession(
        user_id=user.id, expires_at=now + timedelta(seconds=REFRESH_LIFETIME_SECONDS)
    )
    session.add(auth_session)
    session.flush()
    session.add(
        RefreshToken(user_id=user.id, session_id=auth_session.id, token_hash=hash_refresh(refresh))
    )
    session.commit()
    set_refresh_cookie(response, refresh, REFRESH_LIFETIME_SECONDS, settings)
    response.headers["Cache-Control"] = "no-store"
    return access


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(
    response: Response,
    signing_key: SigningKey,
    session: SessionDependency,
    settings: CookieSettings,
    csrf: CsrfCheck,
    refresh_cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> AccessTokenResponse:
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    pair = lock_token_session(session, refresh_cookie)
    now = datetime.now(UTC)
    if pair is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    token, parent = pair
    if parent.revoked_at is not None or session_expiry(parent) <= now:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if token.spent_at is not None:
        parent.revoked_at = now
        session.commit()  # Must commit before raising, not rollback the replay revocation.
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = session.get(User, parent.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    access = issue_access(user, signing_key)
    remaining = max(1, int((session_expiry(parent) - now).total_seconds()))
    new_refresh = token_urlsafe(32)
    token.spent_at = now
    session.add(
        RefreshToken(
            user_id=parent.user_id, session_id=parent.id, token_hash=hash_refresh(new_refresh)
        )
    )
    session.commit()
    set_refresh_cookie(response, new_refresh, remaining, settings)
    response.headers["Cache-Control"] = "no-store"
    return access


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    session: SessionDependency,
    settings: CookieSettings,
    csrf: CsrfCheck,
    refresh_cookie: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> None:
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    pair = lock_token_session(session, refresh_cookie)
    if pair is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    _, parent = pair
    if session_expiry(parent) <= datetime.now(UTC):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if parent.revoked_at is None:
        parent.revoked_at = datetime.now(UTC)
        session.commit()
    response.delete_cookie(
        COOKIE_NAME,
        path=settings.auth_cookie_path,
        secure=bool(settings.cookie_secure),
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"


def get_current_user(
    signing_key: SigningKey,
    settings: CookieSettings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    session: SessionDependency,
) -> User:
    if credentials is None:
        raise unauthorized()
    try:
        claims = jwt.decode(
            credentials.credentials,
            signing_key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["exp", "iat", "sub", "token_use", "iss", "aud"]},
        )
        # PyJWT checks expiration/future issuance, but accepts some non-integer
        # dates. Our access-token contract is integer seconds and exactly 10 min.
        if (
            type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or claims["exp"] - claims["iat"] != ACCESS_LIFETIME_SECONDS
            or claims["token_use"] != "access"
        ):
            raise ValueError("Invalid access claims")
        user_id = UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError) as error:
        raise unauthorized() from error
    user = session.get(User, user_id)
    if user is None:
        raise unauthorized()
    return user


@router.get("/me", response_model=IdentityResponse)
def me(response: Response, user: Annotated[User, Depends(get_current_user)]) -> IdentityResponse:
    response.headers["Cache-Control"] = "no-store"
    return IdentityResponse(id=user.id, email=user.email)
