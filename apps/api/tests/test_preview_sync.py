"""HTTP workflow, real auth/crypto/lease/upsert and disposable PostgreSQL.

Only provider I/O is synthetic. Regression risks: overlap/replacement, stale
holders, all-or-nothing batch writes, bad profile/timezone/payload, bounded
responses/deadline, freshness preservation, isolation, and missing-vs-zero.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from velolab_api.app import app
from velolab_api.integrations import get_intervals_transport
from velolab_api.models import Activity, IntegrationSyncState, User, UserIntegration, WellnessDay
from velolab_api.preview_sync import DEFAULT_PREVIEW_POLICY
from velolab_api.sync_state import SyncStateService

from .test_integrations import BODY, KEY, PATH, Harness
from .test_integrations import connection as connection

SYNC = PATH + "/sync-now"
ROWS: list[dict[str, object]] = [
    {
        "id": "ride-one",
        "name": "Synthetic ride",
        "type": "Ride",
        "start_date_local": "2026-10-08T08:00:00",
        "moving_time": 3600,
        "icu_distance": 42000,
        "distance": 99999,  # Not the provider-recommended display distance.
        "icu_training_load": 45,
        "description": "private description",
    },
    {
        "id": "ride-zero",
        "start_date_local": "2026-10-08T09:00:00",
        "moving_time": 0,
        "icu_distance": 0,
        "icu_training_load": 0,
    },
    {"id": "stub"},
]


def setup_provider(
    h: Harness,
    rows: list[dict[str, object]],
    *,
    timezone: str | None = "Europe/Brussels",
    failure: str | None = None,
    during_listing=None,
) -> list[httpx.Request]:
    if h.stored() is None:
        assert h.client.put(PATH, json=BODY).status_code == 200
    requests = []

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        # The lease is persisted and no HTTP-spanning transaction holds it.
        stored = h.stored()
        assert stored
        with Session(h.engine) as session:
            state = session.get(IntegrationSyncState, stored[0])
            assert state and state.lease_token and state.last_attempt_status == "running"
        if request.url.path.endswith("/activities"):
            if during_listing:
                during_listing()
            if failure == "http":
                return httpx.Response(500, json={"error": KEY})
            if failure == "transport":
                raise httpx.ConnectError(KEY, request=request)
            if failure == "rate":
                return httpx.Response(429, headers={"Retry-After": "9999"})
            return httpx.Response(200, json=rows)
        assert request.url.path == "/api/v1/athlete/" + BODY["athlete_id"]
        return httpx.Response(
            200,
            json={
                "id": "wrong-athlete" if failure == "identity" else BODY["athlete_id"],
                "timezone": timezone,
                "api_key": KEY,
            },
        )

    app.dependency_overrides[get_intervals_transport] = lambda: httpx.MockTransport(provider)
    return requests


def read(h: Harness) -> dict:
    response = h.client.get("/activities")
    h.assert_safe(response, 200)
    assert "payload" not in response.text and "private description" not in response.text
    return response.json()


def test_preview_round_trip_rerun_and_missingness(connection: Harness) -> None:
    h = connection
    assert read(h) == {
        "items": [],
        "has_more": False,
        "coverage": "recent_preview",
        "last_preview_at": None,
        "possibly_truncated": False,
        "last_error_code": None,
    }
    calls = setup_provider(h, ROWS)
    before = datetime.now(UTC).astimezone(ZoneInfo("Europe/Brussels")).date()
    first = h.client.post(SYNC)
    h.assert_safe(first, 200)
    assert first.json()["synced_count"] == 3
    assert first.json()["last_error_code"] is None
    cached = read(h)
    zero, ride, stub = cached["items"]
    assert zero == {
        "id": zero["id"],
        "name": None,
        "type": None,
        "start_local": "2026-10-08T09:00:00",
        "duration_s": 0,
        "distance_m": 0,
        "training_load": 0,
    }
    assert ride["name"] == "Synthetic ride" and ride["duration_s"] == 3600
    assert ride["distance_m"] == 42000 and ride["training_load"] == 45
    assert all(value is None for key, value in stub.items() if key != "id")
    listing = calls[1]
    newest = date.fromisoformat(listing.url.params["newest"])
    assert newest in (before, datetime.now(UTC).astimezone(ZoneInfo("Europe/Brussels")).date())
    assert listing.url.params["oldest"] == (newest - timedelta(days=29)).isoformat()
    assert len(calls) == 2 and "fields" not in listing.url.params
    stored = h.stored()
    assert stored
    with Session(h.engine) as session:
        bookkeeping = [(row.id, row.updated_at) for row in session.scalars(select(Activity)).all()]
    second = h.client.post(SYNC)
    h.assert_safe(second, 200)
    assert second.json()["synced_count"] == 0
    assert read(h)["items"] == cached["items"]
    metadata = h.client.get(PATH).json()
    assert metadata["preview_newest"] == newest.isoformat()
    assert metadata["last_preview_at"] == second.json()["last_preview_at"]
    with Session(h.engine) as session:
        assert [
            (row.id, row.updated_at) for row in session.scalars(select(Activity)).all()
        ] == bookkeeping
        state = session.get(IntegrationSyncState, stored[0])
        assert state and state.last_success_at is None and state.backfill_checkpoint is None
        assert not state.backfill_complete and state.lease_token is None
        assert not session.scalars(select(WellnessDay)).all()


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("http", "http_failure"),
        ("transport", "transport_failure"),
        ("rate", "rate_limited"),
        ("identity", "athlete_mismatch"),
    ],
)
def test_provider_failure_keeps_cached_data_and_freshness(
    connection: Harness, failure: str, code: str
) -> None:
    h = connection
    setup_provider(h, ROWS)
    h.assert_safe(h.client.post(SYNC), 200)
    before = read(h)
    setup_provider(h, [{"id": "new-row"}], failure=failure)
    response = h.client.post(SYNC)
    h.assert_safe(response, 502)
    assert response.json() == {"detail": code}
    assert read(h) == {**before, "last_error_code": code}
    assert h.client.get(PATH).json()["last_attempt_status"] == "failed"


@pytest.mark.parametrize("timezone", [None, "", "not/a/timezone", "../Europe/Brussels"])
def test_invalid_timezone_fails_without_guessing(connection: Harness, timezone: str | None) -> None:
    calls = setup_provider(connection, ROWS, timezone=timezone)
    response = connection.client.post(SYNC)
    connection.assert_safe(response, 502)
    assert response.json() == {"detail": "invalid_timezone"}
    assert len(calls) == 1
    assert read(connection)["items"] == []
    assert read(connection)["last_error_code"] == "invalid_timezone"


def test_busy_and_owner_gates_make_no_provider_calls(connection: Harness) -> None:
    h = connection
    h.assert_safe(h.client.post(SYNC), 409)
    calls = setup_provider(h, ROWS)
    stored = h.stored()
    assert stored and h.settings.integration_preview_owner_id
    with Session(h.engine) as session:
        service = SyncStateService(
            session, user_id=h.settings.integration_preview_owner_id, integration_id=stored[0]
        )
        assert service.claim_lease()
        session.commit()
    response = h.client.post(SYNC)
    h.assert_safe(response, 409)
    assert response.json() == {"detail": "integration_busy"}
    for method, path in (("POST", SYNC), ("GET", "/activities")):
        for headers, status in ((h.other_headers, 403), ({"Authorization": ""}, 401)):
            h.assert_safe(h.client.request(method, path, headers=headers), status)
    assert not calls


def test_truncation_and_owned_ordering_limit(connection: Harness) -> None:
    h = connection
    # 1000 reaches default limit; ordering must ignore upstream list order.
    rows = [
        {
            "id": f"row-{i}",
            "name": str(i),
            **({"start_date_local": "2026-10-08T08:00:00"} if i < 999 else {}),
        }
        for i in range(1000)
    ]
    calls = setup_provider(h, rows)
    response = h.client.post(SYNC)
    h.assert_safe(response, 200)
    assert response.json()["possibly_truncated"] is True
    stored = h.stored()
    assert stored
    with Session(h.engine) as session:
        expected = sorted(session.scalars(select(Activity.id)).all(), key=str)
        # Exclude the null local start, which must be last even if UUID sorts first.
        null_id = session.scalar(
            select(Activity.id).where(Activity.provider_activity_id == "row-999")
        )
        assert null_id is not None
        expected.remove(null_id)
        other = session.scalar(select(User).where(User.email == "other@example.com"))
        assert other
        integration = UserIntegration(
            id=uuid4(),
            user_id=other.id,
            provider="intervals",
            external_athlete_id="other",
            encrypted_api_key="synthetic-unused",
        )
        session.add(integration)
        session.flush()
        session.add(
            Activity(
                user_id=other.id,
                integration_id=integration.id,
                provider_activity_id="private",
                payload={"name": "other user's activity"},
                start_date_local=datetime(2099, 1, 1),
            )
        )
        session.commit()
    cached = read(h)
    assert cached["has_more"] and cached["possibly_truncated"]
    assert [item["id"] for item in cached["items"]] == [str(id) for id in expected[:50]]
    assert len(calls) == 2  # GET is cached only.


@pytest.mark.parametrize(
    "bad_rows",
    [
        [{"id": "valid"}, {"id": "valid"}],
        [{"id": "valid"}, {"id": "bad", "icu_api_key": KEY}],
        [{"id": "valid"}, {"id": "bad", "nested": {"access_token": KEY}}],
        [{"id": "valid"}, {"id": "bad", "client_secret": KEY}],
    ],
)
def test_invalid_batch_never_persists_partial_rows(
    connection: Harness, bad_rows: list[dict[str, object]]
) -> None:
    setup_provider(connection, bad_rows)
    response = connection.client.post(SYNC)
    connection.assert_safe(response, 502)
    assert response.json() == {"detail": "invalid_records"}
    assert read(connection)["items"] == []
    with Session(connection.engine) as session:
        assert not session.scalars(select(Activity)).all()


def test_persistence_failure_rolls_back_domain_and_preview(connection: Harness) -> None:
    h = connection
    setup_provider(h, ROWS)
    h.assert_safe(h.client.post(SYNC), 200)
    before = read(h)
    setup_provider(h, [{"id": "new"}])

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO activities"):
            raise SQLAlchemyError(KEY)

    event.listen(h.engine, "after_cursor_execute", fail)
    try:
        response = h.client.post(SYNC)
        h.assert_safe(response, 503)
        assert response.json() == {"detail": "persistence_failure"}
    finally:
        event.remove(h.engine, "after_cursor_execute", fail)
    assert read(h) == {**before, "last_error_code": "persistence_failure"}


def test_lost_lease_cannot_write_or_release_new_holder(connection: Harness) -> None:
    h = connection
    stored_token = []

    def takeover():
        stored = h.stored()
        assert stored and h.settings.integration_preview_owner_id
        with Session(h.engine) as session:
            session.execute(
                update(IntegrationSyncState)
                .where(IntegrationSyncState.integration_id == stored[0])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            session.commit()
            service = SyncStateService(
                session, user_id=h.settings.integration_preview_owner_id, integration_id=stored[0]
            )
            stored_token.append(service.claim_lease())
            session.commit()

    setup_provider(h, ROWS, during_listing=takeover)
    response = h.client.post(SYNC)
    h.assert_safe(response, 409)
    assert response.json() == {"detail": "lease_lost"}
    assert read(h)["items"] == []
    stored = h.stored()
    assert stored
    with Session(h.engine) as session:
        state = session.get(IntegrationSyncState, stored[0])
        assert state and state.lease_token == stored_token[0] and state.last_preview_at is None


def test_deadline_discards_provider_response(
    connection: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from velolab_api import preview_sync

    setup_provider(connection, ROWS)
    # Deterministic monotonic budget exhaustion before persistence, no sleeps.
    start = preview_sync.monotonic()
    times = iter((start, start + 91.0))
    monkeypatch.setattr(preview_sync, "monotonic", lambda: next(times))
    response = connection.client.post(SYNC)
    connection.assert_safe(response, 502)
    assert response.json() == {"detail": "deadline_exceeded"}
    assert read(connection)["items"] == []


def test_provider_deadline_expires_during_response(
    connection: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from velolab_api import intervals_client

    start = intervals_client.monotonic()

    def expire():
        monkeypatch.setattr(intervals_client, "monotonic", lambda: start + 91)

    setup_provider(connection, ROWS, during_listing=expire)
    response = connection.client.post(SYNC)
    connection.assert_safe(response, 502)
    assert response.json() == {"detail": "deadline_exceeded"}
    assert read(connection)["items"] == []


def test_overlapping_request_and_replacement_excluded_during_network(connection: Harness) -> None:
    h = connection
    before = []

    def overlap():
        before.append(h.stored())
        busy = h.client.post(SYNC)
        h.assert_safe(busy, 409)
        assert busy.json() == {"detail": "integration_busy"}
        replacement = h.client.put(PATH, json=BODY)
        h.assert_safe(replacement, 409)
        assert replacement.json() == {"detail": "integration_busy"}
        assert h.stored() == before[0]

    setup_provider(h, ROWS, during_listing=overlap)
    h.assert_safe(h.client.post(SYNC), 200)
    assert len(read(h)["items"]) == 3


def test_unusable_ciphertext_records_static_failure_without_provider(connection: Harness) -> None:
    h = connection
    calls = setup_provider(h, ROWS)
    stored = h.stored()
    assert stored
    with Session(h.engine) as session:
        row = session.get(UserIntegration, stored[0])
        assert row
        row.encrypted_api_key = "synthetic-corrupt-ciphertext"
        session.commit()
    response = h.client.post(SYNC)
    h.assert_safe(response, 503)
    assert response.json() == {"detail": "credentials_unavailable"}
    assert not calls and read(h)["last_error_code"] == "credentials_unavailable"


def test_invalid_list_fields_show_gaps_without_coercion(connection: Harness) -> None:
    setup_provider(
        connection,
        [{"id": "bad-optional", "name": {}, "type": 1, "moving_time": True, "icu_distance": "100"}],
    )
    connection.assert_safe(connection.client.post(SYNC), 200)
    item = read(connection)["items"][0]
    assert all(value is None for key, value in item.items() if key != "id")


def test_cached_rows_and_freshness_share_one_snapshot(connection: Harness) -> None:
    h = connection
    setup_provider(h, ROWS)
    h.assert_safe(h.client.post(SYNC), 200)
    before = read(h)
    setup_provider(h, [{"id": "concurrent-new", "start_date_local": "2026-10-09T10:00:00"}])
    synced = []

    def commit_sync_between_reads(conn, cursor, statement, parameters, context, executemany):
        if "FROM activities" in statement and not synced:
            synced.append(True)
            h.assert_safe(h.client.post(SYNC), 200)

    event.listen(h.engine, "after_cursor_execute", commit_sync_between_reads)
    try:
        assert read(h) == before
    finally:
        event.remove(h.engine, "after_cursor_execute", commit_sync_between_reads)
    assert synced
    assert len(read(h)["items"]) == 4
    assert read(h)["last_preview_at"] != before["last_preview_at"]


def test_small_limit_can_be_configured_without_backfill(
    connection: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from velolab_api import integrations

    calls = setup_provider(connection, [{"id": "one"}])
    original = integrations.sync_preview
    policy = replace(
        DEFAULT_PREVIEW_POLICY, client=replace(DEFAULT_PREVIEW_POLICY.client, activity_limit=1)
    )
    monkeypatch.setattr(
        integrations,
        "sync_preview",
        lambda *args, **kwargs: original(*args, **kwargs, policy=policy),
    )
    response = connection.client.post(SYNC)
    connection.assert_safe(response, 200)
    assert response.json()["possibly_truncated"] and calls[1].url.params["limit"] == "1"
