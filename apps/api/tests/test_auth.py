"""Auth contract through HTTP, with real hashing and a disposable SQLAlchemy DB."""

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, tzinfo
from threading import Barrier

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from velolab_api import auth
from velolab_api.app import app
from velolab_api.db import Base, get_session
from velolab_api.models import RefreshToken
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
        # Only the portable auth tables; training payloads require PostgreSQL JSONB.
        Base.metadata.create_all(
            engine,
            tables=[
                Base.metadata.tables[name] for name in ("users", "auth_sessions", "refresh_tokens")
            ],
        )
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
        auth_trusted_origin="http://localhost",
    )
    existing_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            client.headers.update({"Origin": "http://localhost", "X-Velolab-CSRF": "1"})
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
        client.headers.update(auth_client.headers)
        path = "/api/auth/login"
    elif deployment == "root_path":
        client = TestClient(app, root_path="/api")
        client.headers.update(auth_client.headers)
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


@pytest.mark.parametrize("auth_client", ["sqlite", "postgres"], indirect=True)
def test_refresh_rotates_and_replay_revokes_only_its_session(auth_client: TestClient) -> None:
    first = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
    )
    assert first.status_code == 200
    original = auth_client.cookies["velolab_refresh"]
    assert "httponly" in first.headers["set-cookie"].lower()
    assert "samesite=strict" in first.headers["set-cookie"].lower()
    assert "secure" not in first.headers["set-cookie"].lower()
    assert "path=/auth" in first.headers["set-cookie"].lower()
    independent = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
    )
    other = auth_client.cookies["velolab_refresh"]
    renewed = auth_client.post("/auth/refresh", headers={"Cookie": f"velolab_refresh={original}"})
    assert renewed.status_code == 200
    assert set(renewed.json()) == {"access_token", "token_type", "expires_in"}
    assert renewed.headers["cache-control"] == "no-store"
    rotated = auth_client.cookies["velolab_refresh"]
    assert rotated != original
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={original}"}
        ).status_code
        == 401
    )
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={rotated}"}
        ).status_code
        == 401
    )
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={other}"}
        ).status_code
        == 200
    )
    assert independent.json()["expires_in"] == 600


@pytest.mark.parametrize("auth_client", ["sqlite", "postgres"], indirect=True)
def test_logout_revokes_only_matching_session(auth_client: TestClient) -> None:
    first_login = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
    )
    first = auth_client.cookies["velolab_refresh"]
    auth_client.post("/auth/login", json={"email": "rider@example.com", "password": PASSWORD})
    second = auth_client.cookies["velolab_refresh"]
    logout = auth_client.post("/auth/logout", headers={"Cookie": f"velolab_refresh={first}"})
    assert logout.status_code == 204
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert (
        auth_client.get(
            "/auth/me", headers={"Authorization": f"Bearer {first_login.json()['access_token']}"}
        ).status_code
        == 200
    )
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={first}"}
        ).status_code
        == 401
    )
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={second}"}
        ).status_code
        == 200
    )


def test_csrf_and_unknown_token_do_not_revoke_a_session(auth_client: TestClient) -> None:
    credentials = {"email": "rider@example.com", "password": PASSWORD}
    for path in ("/auth/login", "/auth/refresh", "/auth/logout"):
        for headers in ({"Origin": "https://other.example"}, {"X-Velolab-CSRF": ""}):
            rejected = auth_client.post(path, json=credentials, headers=headers)
            assert rejected.status_code == 403
    auth_client.headers.pop("Origin")
    assert auth_client.post("/auth/login", json=credentials).status_code == 403
    auth_client.headers["Origin"] = "http://localhost"
    assert auth_client.post("/auth/login", json=credentials).status_code == 200
    actual = auth_client.cookies["velolab_refresh"]
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={'z' * 43}"}
        ).status_code
        == 401
    )
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={actual}"}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "http://example.com",
        "https://example.com/",
        "http://localhost.evil",
        "http://user@localhost",
        "ftp://localhost",
    ],
)
def test_invalid_origin_configuration_disables_auth_not_health(
    auth_client: TestClient, origin: str | None
) -> None:
    settings = app.dependency_overrides[get_settings]()
    settings.auth_trusted_origin = origin
    assert auth_client.get("/health").status_code == 200
    for path in ("/auth/login", "/auth/refresh", "/auth/logout"):
        assert (
            auth_client.post(
                path, json={"email": "rider@example.com", "password": PASSWORD}
            ).status_code
            == 503
        )
    assert (
        auth_client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 503
    )


def test_secure_cookie_and_scoped_proxy_path(auth_client: TestClient) -> None:
    settings = app.dependency_overrides[get_settings]()
    settings.auth_trusted_origin = "https://rider.example"
    settings.auth_cookie_path = "/api/auth"
    auth_client.headers["Origin"] = "https://rider.example"
    result = auth_client.post(
        "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
    )
    assert result.status_code == 200
    assert "secure" in result.headers["set-cookie"].lower()
    assert "path=/api/auth" in result.headers["set-cookie"].lower()
    token = result.cookies["velolab_refresh"]
    refreshed = auth_client.post("/auth/refresh", headers={"Cookie": f"velolab_refresh={token}"})
    assert refreshed.status_code == 200
    assert "secure" in refreshed.headers["set-cookie"].lower()


def test_absolute_expiry_is_not_extended_by_rotation(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_datetime = datetime
    offset = 0

    class Clock(real_datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Clock:
            current = real_datetime.now(UTC) + timedelta(seconds=offset)
            return cls.fromtimestamp(current.timestamp(), tz=tz)

    monkeypatch.setattr(auth, "datetime", Clock)
    assert (
        auth_client.post(
            "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    original = auth_client.cookies["velolab_refresh"]
    offset = 3 * 24 * 60 * 60
    rotated = auth_client.post("/auth/refresh", headers={"Cookie": f"velolab_refresh={original}"})
    assert rotated.status_code == 200
    assert (
        3 * 24 * 60 * 60
        < int(rotated.headers["set-cookie"].split("Max-Age=")[1].split(";")[0])
        <= 4 * 24 * 60 * 60
    )
    current = rotated.cookies["velolab_refresh"]
    offset = 7 * 24 * 60 * 60 + 1
    assert (
        auth_client.post(
            "/auth/refresh", headers={"Cookie": f"velolab_refresh={current}"}
        ).status_code
        == 401
    )


@pytest.fixture
def postgres_http(isolated_database: tuple[Config, Engine]) -> Generator[tuple[TestClient, Engine]]:
    config, engine = isolated_database
    command.upgrade(config, "head")
    with Session(engine) as session:
        provision_user(session, "rider@example.com", PASSWORD)

    def db_override() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    settings = Settings(
        _env_file=None,
        postgres_user="unused",
        postgres_db="unused",
        postgres_password="unused",
        auth_jwt_secret=TEST_SECRET,
        auth_trusted_origin="http://localhost",
    )
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = db_override
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            client.headers.update({"Origin": "http://localhost", "X-Velolab-CSRF": "1"})
            yield client, engine
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_postgres_rotation_persists_only_hash_and_replay_serializes(
    postgres_http: tuple[TestClient, Engine],
) -> None:
    client, engine = postgres_http
    assert (
        client.post(
            "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    original = client.cookies["velolab_refresh"]
    with Session(engine) as reader:
        stored = reader.scalars(select(RefreshToken)).one()
        assert stored.token_hash != original
        assert len(stored.token_hash) == 64

    ready = Barrier(2)

    def rendezvous(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.startswith("SELECT") and "refresh_tokens.token_hash =" in statement:
            ready.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", rendezvous)
    try:

        def rotate() -> Response:
            with TestClient(app) as competitor:
                return competitor.post(
                    "/auth/refresh",
                    headers={
                        "Origin": "http://localhost",
                        "X-Velolab-CSRF": "1",
                        "Cookie": f"velolab_refresh={original}",
                    },
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(rotate) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", rendezvous)
    assert sorted(response.status_code for response in results) == [200, 401]
    successor = next(
        response.cookies["velolab_refresh"] for response in results if response.status_code == 200
    )
    assert (
        client.post("/auth/refresh", headers={"Cookie": f"velolab_refresh={successor}"}).status_code
        == 401
    )


def test_postgres_logout_and_refresh_race_cannot_leave_live_cookie(
    postgres_http: tuple[TestClient, Engine],
) -> None:
    client, engine = postgres_http
    assert (
        client.post(
            "/auth/login", json={"email": "rider@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    original = client.cookies["velolab_refresh"]
    ready = Barrier(2)

    def rendezvous(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.startswith("SELECT") and "refresh_tokens.token_hash =" in statement:
            ready.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", rendezvous)
    try:

        def send(path: str) -> Response:
            with TestClient(app) as competitor:
                return competitor.post(
                    path,
                    headers={
                        "Origin": "http://localhost",
                        "X-Velolab-CSRF": "1",
                        "Cookie": f"velolab_refresh={original}",
                    },
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            refresh_future = executor.submit(send, "/auth/refresh")
            logout_future = executor.submit(send, "/auth/logout")
            refreshed = refresh_future.result(timeout=20)
            logged_out = logout_future.result(timeout=20)
    finally:
        event.remove(engine, "before_cursor_execute", rendezvous)
    assert logged_out.status_code == 204
    assert refreshed.status_code in (200, 401)
    if refreshed.status_code == 200:
        successor = refreshed.cookies["velolab_refresh"]
        assert (
            client.post(
                "/auth/refresh", headers={"Cookie": f"velolab_refresh={successor}"}
            ).status_code
            == 401
        )


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
