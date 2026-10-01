"""Auth contract through HTTP, with real hashing and a disposable SQLAlchemy DB."""

from collections.abc import Generator
from datetime import UTC, datetime

import jwt
import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from velolab_api.app import app
from velolab_api.db import Base, get_session
from velolab_api.provisioning import provision_user
from velolab_api.settings import Settings, get_settings

# Test-only signing material; never a usable application default.
TEST_SECRET = "test-only-jwt-signing-material-64-bytes-or-more-never-a-real-secret"
PASSWORD = "  vélo password  "


@pytest.fixture
def auth_client(request: pytest.FixtureRequest) -> Generator[TestClient]:
    backend = getattr(request, "param", "sqlite")
    if backend == "postgres":
        config, engine = request.getfixturevalue("isolated_database")
        command.upgrade(config, "head")
    else:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
    with Session(engine) as session:
        provision_user(session, "Rider@EXAMPLE.COM", PASSWORD)

    def session_override() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    settings = Settings(
        _env_file=None,
        postgres_user="unused",
        postgres_db="unused",
        postgres_password="unused",
        auth_jwt_secret=TEST_SECRET,
    )
    existing_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(existing_overrides)
        if backend == "sqlite":
            engine.dispose()


@pytest.mark.parametrize("auth_client", ["sqlite", "postgres"], indirect=True)
def test_login_then_read_minimal_identity(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/auth/login", json={"email": "  RIDER@example.com  ", "password": PASSWORD}
    )
    assert response.status_code == 200
    token = response.json()
    assert set(token) == {"access_token", "token_type", "expires_in"}
    assert token["token_type"] == "bearer"
    assert token["expires_in"] == 600
    assert response.headers["cache-control"] == "no-store"
    claims = jwt.decode(
        token["access_token"],
        TEST_SECRET,
        algorithms=["HS256"],
        issuer="velolab-api",
        audience="velolab-api",
    )
    assert claims["exp"] - claims["iat"] == 600
    assert claims["token_use"] == "access"
    assert set(claims) == {"sub", "iat", "exp", "token_use", "iss", "aud"}

    identity = auth_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert identity.status_code == 200
    assert set(identity.json()) == {"id", "email"}
    assert identity.json()["email"] == "rider@example.com"
    assert identity.json()["id"] == claims["sub"]
    assert identity.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("auth_client", ["sqlite", "postgres"], indirect=True)
def test_invalid_credentials_do_not_reveal_account_existence(auth_client: TestClient) -> None:
    wrong_password = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": "incorrect password"}
    )
    missing_user = auth_client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    invalid_email = auth_client.post(
        "/auth/login", json={"email": "not-an-email", "password": PASSWORD}
    )
    trimmed_password = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD.strip()}
    )
    for response in (wrong_password, missing_user, invalid_email, trimmed_password):
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid email or password"}
        assert response.headers["www-authenticate"] == "Bearer"


def test_expired_tampered_and_malformed_tokens_are_rejected(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
    )
    token = response.json()["access_token"]
    claims = jwt.decode(token, TEST_SECRET, algorithms=["HS256"], audience="velolab-api")
    claims.update(iat=1, exp=601)
    expired = jwt.encode(claims, TEST_SECRET, algorithm="HS256")
    # Mutate payload, not trailing signature padding bits (which can decode identically).
    header, payload, signature = token.split(".")
    changed_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    tampered = f"{header}.{changed_payload}.{signature}"
    for rejected in (expired, tampered, "not.a.jwt"):
        response = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {rejected}"})
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid access token"}
        assert response.headers["www-authenticate"] == "Bearer"
    assert auth_client.get("/auth/me").status_code == 401
    assert (
        auth_client.get("/auth/me", headers={"Authorization": f"Basic {token}"}).status_code == 401
    )


def test_signed_tokens_still_require_access_claims_and_allowed_algorithm(
    auth_client: TestClient,
) -> None:
    response = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
    )
    claims = jwt.decode(
        response.json()["access_token"], TEST_SECRET, algorithms=["HS256"], audience="velolab-api"
    )
    invalid_claims = []
    for required in ("exp", "iat", "sub", "token_use"):
        incomplete = claims.copy()
        del incomplete[required]
        invalid_claims.append(incomplete)
    invalid_claims.extend(
        [
            {**claims, "token_use": "refresh"},
            {**claims, "iss": "another-service"},
            {**claims, "aud": "another-service"},
            {**claims, "sub": "not-a-uuid"},
            # A signed token cannot invent an account.
            {**claims, "sub": "00000000-0000-0000-0000-000000000001"},
            {**claims, "iat": str(claims["iat"])},
            {**claims, "iat": float("inf")},
            {**claims, "exp": float(claims["exp"])},
            {**claims, "iat": int(datetime.now(UTC).timestamp()) + 3600},
            {**claims, "exp": claims["iat"] + 3600},
        ]
    )
    for invalid in invalid_claims:
        token = jwt.encode(invalid, TEST_SECRET, algorithm="HS256")
        rejected = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert rejected.status_code == 401
    alternate_algorithm = jwt.encode(claims, TEST_SECRET, algorithm="HS384")
    rejected = auth_client.get(
        "/auth/me", headers={"Authorization": f"Bearer {alternate_algorithm}"}
    )
    assert rejected.status_code == 401


@pytest.mark.parametrize("secret", [None, "too-short"])
def test_missing_or_weak_key_disables_only_auth(
    auth_client: TestClient, secret: str | None
) -> None:
    settings = app.dependency_overrides[get_settings]()
    settings.auth_jwt_secret = SecretStr(secret) if secret is not None else None
    assert auth_client.get("/health").json() == {"status": "ok"}
    login = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
    )
    protected = auth_client.get("/auth/me", headers={"Authorization": "Bearer not-a-token"})
    for response in (login, protected):
        assert response.status_code == 503
        assert response.json() == {"detail": "Authentication is not configured"}


@pytest.mark.parametrize("deployment", ["direct", "mounted", "root_path"])
def test_invalid_login_body_does_not_echo_credentials(
    auth_client: TestClient, deployment: str
) -> None:
    password = "sensitive" * 20
    if deployment == "mounted":
        parent = FastAPI()
        parent.mount("/api", app)
        client = TestClient(parent)
        path = "/api/auth/login"
    elif deployment == "root_path":
        client = TestClient(app, root_path="/api")
        path = "/api/auth/login"
    else:
        client = auth_client
        path = "/auth/login"

    with client:
        rejected = client.post(path, json={"email": "rider@example.com", "password": password})
    assert rejected.status_code == 422
    assert rejected.json() == {
        "detail": [
            {
                "type": "string_too_long",
                "loc": ["body", "password"],
                "msg": "String should have at most 128 characters",
            }
        ]
    }
    assert password not in rejected.text


def test_other_validation_errors_keep_fastapi_default_response(auth_client: TestClient) -> None:
    def validation_probe(limit: int) -> dict[str, int]:
        return {"limit": limit}

    app.add_api_route("/validation-probe", validation_probe, methods=["GET"])
    route = app.router.routes[-1]
    try:
        rejected = auth_client.get("/validation-probe", params={"limit": "not-a-number"})
    finally:
        app.router.routes.remove(route)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == [
        {
            "type": "int_parsing",
            "loc": ["query", "limit"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
            "input": "not-a-number",
        }
    ]
