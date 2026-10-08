"""Offline API-v1 boundary scenarios; all identities/payloads are synthetic."""

import base64
import traceback
from datetime import UTC, date, datetime

import httpx
import pytest
from pydantic import SecretStr

from velolab_api.intervals_client import (
    ErrorCode,
    IntervalsClient,
    IntervalsClientError,
    IntervalsClientPolicy,
    IntervalsRateLimitError,
)

KEY = "synthetic-key-not-a-credential"
ATHLETE = "synthetic-athlete"
OLDEST, NEWEST = date(2025, 1, 1), date(2025, 1, 3)


@pytest.fixture(autouse=True)
def deny_real_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*_: object, **__: object) -> None:
        pytest.fail("Real HTTP connections are forbidden in client tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)


def client(handler, **kwargs) -> IntervalsClient:
    return IntervalsClient(
        SecretStr(KEY), ATHLETE, transport=httpx.MockTransport(handler), **kwargs
    )


def assert_safe(error: IntervalsClientError) -> None:
    output = str(error) + repr(error) + "".join(traceback.format_exception(error))
    for private in (KEY, ATHLETE, "intervals.icu", "2025-01-01", "synthetic-activity"):
        assert private not in output
    assert error.__context__ is None
    assert error.__cause__ is None


def test_profile_and_local_date_listings_preserve_payloads(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://unreachable.invalid:1")
    paths = []
    activity = {
        "id": "synthetic-activity",
        "icu_athlete_id": ATHLETE,
        "start_date": "2025-01-02T22:30:00Z",
        "start_date_local": "2025-01-03T00:30:00",
        "icu_training_load": 0,
        "unknown": {"future_field": None},
    }
    days = [
        {"id": "2025-01-01", "weight": None, "restingHR": 0},
        {"id": "2025-01-02", "tempWeight": True},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "intervals.icu"
        assert (
            request.headers["Authorization"]
            == "Basic " + base64.b64encode(f"API_KEY:{KEY}".encode()).decode()
        )
        assert request.headers["Accept"] == "application/json"
        assert request.headers["User-Agent"] == "Mozilla/5.0"
        assert set(request.extensions["timeout"].values()) == {15.0}
        paths.append(request.url.path)
        if request.url.path.endswith("/activities"):
            assert dict(request.url.params) == {
                "oldest": "2025-01-01",
                "newest": "2025-01-03",
                "limit": "1000",
            }
            return httpx.Response(200, json=[activity, {"id": "synthetic-strava-stub"}])
        if request.url.path.endswith("/wellness"):
            assert dict(request.url.params) == {"oldest": "2025-01-01", "newest": "2025-01-03"}
            return httpx.Response(200, json=days)
        assert not request.url.query
        return httpx.Response(200, json={"id": ATHLETE, "timezone": "Europe/Brussels"})

    with client(handler) as api:
        profile = api.athlete_profile()
        activities = api.activities(OLDEST, NEWEST)
        wellness = api.wellness(OLDEST, NEWEST)
        assert profile.id == ATHLETE
        assert profile.timezone == "Europe/Brussels"
        assert activities.records[0].raw == activity
        assert activities.records[0].start_date == datetime(2025, 1, 2, 22, 30, tzinfo=UTC)
        assert activities.records[0].start_date_local == datetime(2025, 1, 3, 0, 30)
        assert activities.records[1].start_date is None  # Retain Strava stub.
        assert wellness.records[0].local_date == OLDEST
        assert wellness.records[0].raw["weight"] is None
        assert "weight" not in wellness.records[1].raw
        assert wellness.records[0].raw["restingHR"] == 0
        assert wellness.records[1].raw["tempWeight"] is True
        assert not activities.possibly_truncated
        assert not wellness.possibly_truncated
        for value in (api, profile, activities, wellness, *activities.records, *wellness.records):
            assert ATHLETE not in repr(value)
            assert "2025" not in repr(value)
            assert KEY not in repr(value)
    assert paths == [
        f"/api/v1/athlete/{ATHLETE}{suffix}" for suffix in ("", "/activities", "/wellness")
    ]


def test_exact_limit_is_possibly_truncated_for_both_listings() -> None:
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {
                    "id": "synthetic-activity"
                    if request.url.path.endswith("activities")
                    else "2025-01-01"
                }
            ],
        )

    with client(handler, policy=IntervalsClientPolicy(activity_limit=1, record_cap=1)) as api:
        assert api.activities(OLDEST, NEWEST).possibly_truncated
        assert api.wellness(OLDEST, NEWEST).possibly_truncated


@pytest.mark.parametrize(
    "retry_after,expected",
    [("2", 2.0), (None, 1.0), ("garbage", 1.0), ("Wed, 01 Jan 2025 00:00:03 GMT", 3.0)],
)
def test_429_retry_after_then_success(retry_after, expected) -> None:
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            headers = {"Retry-After": retry_after} if retry_after is not None else {}
            return httpx.Response(429, headers=headers, content=KEY)
        return httpx.Response(200, json={"id": ATHLETE})

    with client(handler, sleep=sleeps.append, clock=lambda: 1735689600.0) as api:
        assert api.athlete_profile().id == ATHLETE
    assert len(calls) == 2
    assert sleeps == [expected]


def test_429_exhaustion_is_bounded_and_redacted() -> None:
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, content=f"{KEY} {ATHLETE}")

    with (
        client(handler, sleep=sleeps.append) as api,
        pytest.raises(IntervalsRateLimitError) as failure,
    ):
        api.athlete_profile()
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]
    assert failure.value.status == 429
    assert_safe(failure.value)


@pytest.mark.parametrize("retry_after", ["31", "9" * 400])
def test_long_retry_after_fails_instead_of_sleeping_or_retrying_early(retry_after) -> None:
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": retry_after})

    with client(handler, sleep=sleeps.append) as api, pytest.raises(IntervalsRateLimitError):
        api.athlete_profile()
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 500])
def test_http_failures_never_redirect_retry_or_expose_provider_details(status) -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": f"https://unsafe.invalid/{KEY}"},
            content=f"{KEY} {ATHLETE}",
        )

    with client(handler) as api, pytest.raises(IntervalsClientError) as failure:
        api.activities(OLDEST, NEWEST)
    assert len(calls) == 1
    assert failure.value.code == ErrorCode.HTTP
    assert failure.value.status == status
    assert_safe(failure.value)


def test_transport_failure_discards_unsafe_exception_context() -> None:
    def handler(request):
        raise httpx.ReadTimeout(f"{KEY} {ATHLETE} {request.url}", request=request)

    with client(handler) as api, pytest.raises(IntervalsClientError) as failure:
        api.activities(OLDEST, NEWEST)
    assert failure.value.code == ErrorCode.TRANSPORT
    assert_safe(failure.value)


@pytest.mark.parametrize(
    "athlete_id", ["", "0", ".", "..", "a/b", "a\\b", "a%2Fb", "a b", "a\n", "a\u200b", "a\ud800"]
)
def test_unsafe_athlete_id_rejected_before_transport(athlete_id) -> None:
    def denied(request):
        pytest.fail("Unsafe athlete ID reached transport")

    with pytest.raises(IntervalsClientError) as failure:
        IntervalsClient(SecretStr(KEY), athlete_id, transport=httpx.MockTransport(denied))
    assert failure.value.code == ErrorCode.INVALID_INPUT
    assert_safe(failure.value)


@pytest.mark.parametrize("key", ["", "synthetic\nkey", "synthetic\ud800key"])
def test_invalid_credentials_fail_without_unsafe_encoder_errors(key) -> None:
    with pytest.raises(IntervalsClientError) as failure:
        IntervalsClient(
            SecretStr(key), ATHLETE, transport=httpx.MockTransport(lambda _: httpx.Response(200))
        )
    assert failure.value.code == ErrorCode.INVALID_INPUT
    assert_safe(failure.value)


def test_opaque_athlete_id_is_one_encoded_segment() -> None:
    athlete = "opaque:@?#é"

    def handler(request):
        assert request.url.raw_path == b"/api/v1/athlete/opaque%3A%40%3F%23%C3%A9"
        assert not request.url.query
        return httpx.Response(200, json={"id": athlete})

    with IntervalsClient(SecretStr(KEY), athlete, transport=httpx.MockTransport(handler)) as api:
        assert api.athlete_profile().id == athlete


@pytest.mark.parametrize(
    "response,code,method",
    [
        (httpx.Response(200, content=b"not-json"), ErrorCode.RESPONSE_SHAPE, "athlete_profile"),
        (httpx.Response(200, json=[]), ErrorCode.RESPONSE_SHAPE, "athlete_profile"),
        (
            httpx.Response(200, json={"id": "other-athlete"}),
            ErrorCode.ATHLETE_MISMATCH,
            "athlete_profile",
        ),
        (
            httpx.Response(200, json=[{"id": "a", "icu_athlete_id": "other-athlete"}]),
            ErrorCode.ATHLETE_MISMATCH,
            "activities",
        ),
        (
            httpx.Response(200, json=[{"id": "a", "start_date": KEY}]),
            ErrorCode.RESPONSE_SHAPE,
            "activities",
        ),
        (httpx.Response(200, json=[{}]), ErrorCode.RESPONSE_SHAPE, "activities"),
        (httpx.Response(200, json=[1]), ErrorCode.RESPONSE_SHAPE, "activities"),
        (httpx.Response(200, json=[{"id": "not-a-day"}]), ErrorCode.RESPONSE_SHAPE, "wellness"),
    ],
)
def test_invalid_payloads_fail_safely(response, code, method) -> None:
    with client(lambda request: response) as api, pytest.raises(IntervalsClientError) as failure:
        getattr(api, method)(*(() if method == "athlete_profile" else (OLDEST, NEWEST)))
    assert failure.value.code == code
    assert_safe(failure.value)


def test_response_byte_and_record_caps_fail_closed() -> None:
    class OversizedStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b"x" * 65536
            pytest.fail("Response cap did not stop reading the stream")

        def close(self):
            self.closed = True

    stream = OversizedStream()
    with client(
        lambda request: httpx.Response(200, stream=stream),
        policy=IntervalsClientPolicy(max_response_bytes=10),
    ) as api:
        with pytest.raises(IntervalsClientError) as failure:
            api.athlete_profile()
        assert failure.value.code == ErrorCode.RESPONSE_SIZE
    assert stream.closed
    with client(
        lambda request: httpx.Response(200, json=[{"id": "a"}, {"id": "b"}]),
        policy=IntervalsClientPolicy(activity_limit=1, record_cap=1),
    ) as api:
        with pytest.raises(IntervalsClientError) as failure:
            api.activities(OLDEST, NEWEST)
        assert failure.value.code == ErrorCode.RECORD_CAP


def test_cancellation_stops_retries() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    def cancel(wait):
        raise KeyboardInterrupt

    with client(handler, sleep=cancel) as api, pytest.raises(KeyboardInterrupt):
        api.athlete_profile()
    assert len(calls) == 1
