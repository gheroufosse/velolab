"""Password login and short-lived access authentication; no refresh or signup."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from velolab_api.db import get_session
from velolab_api.models import User
from velolab_api.provisioning import normalize_email
from velolab_api.settings import Settings, get_settings

ACCESS_LIFETIME_SECONDS = 600
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


def unauthorized(detail: str = "Invalid access token") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


@router.post("/login", response_model=AccessTokenResponse)
def login(
    credentials: LoginRequest,
    response: Response,
    signing_key: SigningKey,
    session: SessionDependency,
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
    response.headers["Cache-Control"] = "no-store"
    return AccessTokenResponse(access_token=token)


def get_current_user(
    signing_key: SigningKey,
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
