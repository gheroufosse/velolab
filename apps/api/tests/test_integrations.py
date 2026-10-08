"""Connection routes with real auth/crypto/PostgreSQL; only provider I/O is fake."""

import base64
import json
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from velolab_api.app import app
from velolab_api.db import get_session
from velolab_api.integration_secrets import IntegrationKeyCipher, IntegrationSecretError
from velolab_api.integrations import get_intervals_transport
from velolab_api.models import User, UserIntegration
from velolab_api.provisioning import provision_user
from velolab_api.settings import IntegrationKeySettings, Settings, get_settings
from velolab_api.sync_state import SyncStateService

PATH = "/integrations/intervals"
KEY = "synthetic-intervals-credential-not-real"
REPLACEMENT = "synthetic-replacement-not-real"
BODY = {"api_key": KEY, "athlete_id": "opaque-athlete"}
EMPTY = {
    "configured": False,
    "athlete_id": None,
    "last_error_code": None,
    "last_preview_at": None,
    "preview_oldest": None,
    "preview_newest": None,
    "possibly_truncated": False,
    "last_attempt_status": None,
}
SAVED = {**EMPTY, "configured": True, "athlete_id": BODY["athlete_id"]}
PASSWORD = "synthetic account password"


@dataclass
class Harness:
    client: TestClient
    engine: Engine
    settings: Settings
    other_headers: dict[str, str]
    calls: list[httpx.Request] = field(default_factory=list)
    failure: str | None = None

    def provider(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.url.scheme == "https" and request.url.host == "intervals.icu"
        assert request.url.path == "/api/v1/athlete/" + BODY["athlete_id"]
        if self.failure == "transport":
            raise httpx.ConnectError(KEY, request=request)
        if self.failure == "http":
            return httpx.Response(401, json={"error": KEY})
        return httpx.Response(
            200,
            json={
                "id": "different-athlete" if self.failure == "identity" else BODY["athlete_id"],
                "timezone": "Europe/Brussels",
                "api_key": KEY,
                "private_profile_field": "must not be returned",
            },
        )

    def stored(self) -> tuple[UUID, str] | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(UserIntegration).where(
                    UserIntegration.user_id == self.settings.integration_preview_owner_id
                )
            )
            return (row.id, row.encrypted_api_key) if row else None

    def assert_safe(self, response: httpx.Response, status: int) -> None:
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"
        assert KEY not in response.text and REPLACEMENT not in response.text
        assert "private_profile_field" not in response.text
        stored = self.stored()
        if stored:
            assert stored[1] not in response.text


@pytest.fixture
def connection(
    isolated_database: tuple[Config, Engine], monkeypatch: pytest.MonkeyPatch
) -> Generator[Harness]:
    config, engine = isolated_database
    command.upgrade(config, "head")
    with Session(engine) as session:
        provision_user(session, "owner@example.com", PASSWORD)
        provision_user(session, "other@example.com", PASSWORD)
        owner_id = session.scalar(select(User.id).where(User.email == "owner@example.com"))
        assert owner_id
    settings = Settings(
        _env_file=None,
        postgres_user="unused",
        postgres_db="unused",
        postgres_password="unused",
        auth_jwt_secret="synthetic-signing-material-at-least-thirty-two-bytes",
        auth_trusted_origin="http://localhost:5173",
        integration_preview_enabled=True,
        integration_preview_owner_id=owner_id,
    )
    encoded = base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode().rstrip("=")
    monkeypatch.setenv("INTEGRATION_KEYRING", json.dumps({"test": encoded}))
    monkeypatch.setenv("INTEGRATION_WRITE_KEY_ID", "test")

    def session_override() -> Generator[Session]:
        with Session(engine) as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            client.headers.update({"Origin": settings.auth_trusted_origin, "X-Velolab-CSRF": "1"})
            headers = []
            for email in ("owner@example.com", "other@example.com"):
                login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
                assert login.status_code == 200
                headers.append({"Authorization": "Bearer " + login.json()["access_token"]})
            client.headers.update(headers[0])
            harness = Harness(client, engine, settings, headers[1])
            app.dependency_overrides[get_intervals_transport] = lambda: httpx.MockTransport(
                harness.provider
            )
            yield harness
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_test_save_and_replace_are_distinct_and_encrypted(connection: Harness) -> None:
    h = connection
    assert h.client.get(PATH).json() == EMPTY
    tested = h.client.post(PATH + "/test", json=BODY)
    h.assert_safe(tested, 200)
    assert tested.json() == {"athlete_id": BODY["athlete_id"], "timezone": "Europe/Brussels"}
    assert h.stored() is None
    for credential in (KEY, REPLACEMENT, REPLACEMENT):
        before = h.stored()
        saved = h.client.put(PATH, json={**BODY, "api_key": credential})
        h.assert_safe(saved, 200)
        assert saved.json() == SAVED
        metadata = h.client.get(PATH)
        h.assert_safe(metadata, 200)
        assert metadata.json() == SAVED
        stored = h.stored()
        assert stored and credential not in stored[1] and stored[1].startswith("v1:test:")
        if before:
            assert stored[0] == before[0] and stored[1] != before[1]
        with Session(h.engine) as session:
            row = session.get(UserIntegration, stored[0])
            assert row is not None
            assert (
                IntegrationKeyCipher(IntegrationKeySettings()).decrypt(row).get_secret_value()
                == credential
            )
    assert len(h.calls) == 4  # Every save independently verifies, even identical credentials.


@pytest.mark.parametrize("failure", ["identity", "http", "transport"])
def test_provider_failure_preserves_stored_connection(connection: Harness, failure: str) -> None:
    h = connection
    assert h.client.put(PATH, json=BODY).status_code == 200
    before = h.stored()
    h.failure = failure
    for method, path in (("POST", PATH + "/test"), ("PUT", PATH)):
        response = h.client.request(method, path, json={**BODY, "api_key": REPLACEMENT})
        h.assert_safe(response, 502)
        assert response.json() == {
            "detail": {
                "identity": "athlete_mismatch",
                "http": "http_failure",
                "transport": "transport_failure",
            }[failure]
        }
        assert h.stored() == before
        assert h.client.get(PATH).json() == SAVED


@pytest.mark.parametrize("keyring", [None, "invalid-json"])
def test_crypto_readiness_precedes_network_and_writes(
    connection: Harness, monkeypatch: pytest.MonkeyPatch, keyring: str | None
) -> None:
    h = connection
    if keyring is None:
        monkeypatch.delenv("INTEGRATION_KEYRING")
    else:
        monkeypatch.setenv("INTEGRATION_KEYRING", keyring)
    for method, path in (("POST", PATH + "/test"), ("PUT", PATH)):
        response = h.client.request(method, path, json=BODY)
        h.assert_safe(response, 503)
        assert response.json() == {"detail": "credentials_unavailable"}
    assert not h.calls and h.stored() is None


def test_owner_isolation_and_bearer_requirement(connection: Harness) -> None:
    h = connection
    assert h.client.put(PATH, json=BODY).status_code == 200
    before = h.stored()
    h.calls.clear()
    for method, path in (("GET", PATH), ("POST", PATH + "/test"), ("PUT", PATH)):
        for headers, status in ((h.other_headers, 403), ({"Authorization": ""}, 401)):
            response = h.client.request(
                method, path, headers=headers, json=BODY if method != "GET" else None
            )
            h.assert_safe(response, status)
    assert h.stored() == before and not h.calls


@pytest.mark.parametrize("gate", ["disabled", "missing_owner", "nonloopback"])
def test_preview_fails_closed(connection: Harness, gate: str) -> None:
    h = connection
    if gate == "disabled":
        h.settings.integration_preview_enabled = False
    elif gate == "missing_owner":
        h.settings.integration_preview_owner_id = None
    else:
        h.settings.auth_trusted_origin = "https://example.com"
    for method, path in (("GET", PATH), ("POST", PATH + "/test"), ("PUT", PATH)):
        response = h.client.request(method, path, json=BODY if method != "GET" else None)
        h.assert_safe(response, 503)
        assert response.json() == {"detail": "preview_unavailable"}
    assert not h.calls and h.stored() is None


@pytest.mark.parametrize("prefix", ["", "/api", "/proxy/api"])
def test_validation_redacts_all_inputs_including_malformed_json(
    connection: Harness, prefix: str
) -> None:
    h = connection
    parent = FastAPI()
    parent.mount(prefix or "/", app)
    with TestClient(parent) as client:
        client.headers.update(h.client.headers)
        for body in (
            json.dumps({**BODY, "athlete_id": {"secret": KEY}}),
            json.dumps({**BODY, "unexpected": KEY}),
            '{"api_key":"' + KEY + '",',
        ):
            for method, path in (("POST", PATH + "/test"), ("PUT", PATH)):
                response = client.request(
                    method,
                    prefix + path,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
                h.assert_safe(response, 422)
                assert response.json() == {"detail": "invalid_input"}
    assert not h.calls and h.stored() is None


def test_rebinding_and_active_sync_cannot_replace_credentials(connection: Harness) -> None:
    h = connection
    assert h.client.put(PATH, json=BODY).status_code == 200
    before = h.stored()
    assert before
    # Independent verification succeeds for the new athlete, but immutable
    # storage binding still rejects it. Use the same real client boundary.
    app.dependency_overrides[get_intervals_transport] = lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, json={"id": "new-athlete"})
    )
    response = h.client.put(PATH, json={**BODY, "athlete_id": "new-athlete"})
    h.assert_safe(response, 409)
    assert response.json() == {"detail": "athlete_rebinding"} and h.stored() == before
    app.dependency_overrides[get_intervals_transport] = lambda: httpx.MockTransport(h.provider)
    with Session(h.engine) as session:
        assert h.settings.integration_preview_owner_id
        service = SyncStateService(
            session, user_id=h.settings.integration_preview_owner_id, integration_id=before[0]
        )
        token = service.claim_lease()
        assert token
        session.commit()
    response = h.client.put(PATH, json={**BODY, "api_key": REPLACEMENT})
    h.assert_safe(response, 409)
    assert response.json() == {"detail": "integration_busy"} and h.stored() == before
    with Session(h.engine) as session:
        service = SyncStateService(
            session, user_id=h.settings.integration_preview_owner_id, integration_id=before[0]
        )
        service.release_lease(token)
        session.commit()
    h.assert_safe(h.client.put(PATH, json={**BODY, "api_key": REPLACEMENT}), 200)


def test_concurrent_first_saves_keep_one_decryptable_integration(connection: Harness) -> None:
    h = connection
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda key: h.client.put(PATH, json={**BODY, "api_key": key}), (KEY, REPLACEMENT)
            )
        )
    for response in responses:
        h.assert_safe(response, 200)
    with Session(h.engine) as session:
        rows = session.scalars(select(UserIntegration)).all()
        assert len(rows) == 1
        assert IntegrationKeyCipher(IntegrationKeySettings()).decrypt(
            rows[0]
        ).get_secret_value() in (KEY, REPLACEMENT)


@pytest.mark.parametrize("failure", ["encrypt", "persist"])
def test_failed_replacement_rolls_back_and_redacts_errors(
    connection: Harness, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    h = connection
    assert h.client.put(PATH, json=BODY).status_code == 200
    before = h.stored()

    def fail_encrypt(*args: object, **kwargs: object) -> None:
        raise IntegrationSecretError()

    def fail_persist(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        # Fault after the UPDATE executed: HTTP failure must roll it back.
        if statement.startswith("UPDATE user_integrations"):
            raise SQLAlchemyError(KEY)

    if failure == "encrypt":
        monkeypatch.setattr(IntegrationKeyCipher, "encrypt", fail_encrypt)
    else:
        event.listen(h.engine, "after_cursor_execute", fail_persist)
    try:
        response = h.client.put(PATH, json={**BODY, "api_key": REPLACEMENT})
        h.assert_safe(response, 503)
        assert response.json() == {
            "detail": "credentials_unavailable" if failure == "encrypt" else "persistence_failure"
        }
        assert h.stored() == before
        assert h.client.get(PATH).json() == SAVED
    finally:
        if failure == "persist":
            event.remove(h.engine, "after_cursor_execute", fail_persist)
