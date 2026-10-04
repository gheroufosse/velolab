"""Generated responses only; importing the standalone probe must not run its CLI.

Failure inventory / seams (probe report, mocked HTTP adapter, terminal CLI):
- DST/local midnight: wrong complete-day window; assert literal provider-local dates.
- Missing/null/zero/wrong types or hostile strings: lost distinctions or disclosure;
  assert allowlisted counts/types and absence of values through the probe report.
- Overlapping windows/ignored limit/duplicates: misleading observations; assert the
  bounded GET plan and discrepancies, never a completeness claim.
- Invalid timezone, identity, listing shape/cap: unsafe continuation; stop with
  static errors, including the limit-comparison response's ownership check.
- HTTP failures, redirects, proxies, oversize/invalid JSON: credential disclosure,
  unbounded reads or retries; isolate the external urllib boundary to verify
  direct fixed-host GETs, timeout/cap, cleanup, no retries and sanitized errors.
- Nonterminal input or getpass fallback/cancellation: exposed credentials;
  exercise CLI with simulated terminals and never create a real reader.
No DB/persistence/concurrency is involved; provider consistency is not provable
with generated responses. No real requests, credentials or recorded fixtures.
"""

import importlib.util
import io
import json
import socket
import warnings
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "probe_intervals.py"
spec = importlib.util.spec_from_file_location("standalone_intervals_probe", SCRIPT)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

NOW = datetime(2026, 3, 9, 12, tzinfo=UTC)
PRIVATE = "PRIVATE_GENERATED_VALUE\nAuthorization: malicious-secret"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def refused(*args, **kwargs):
        pytest.fail("Tests must never open a network connection")

    monkeypatch.setattr(socket, "create_connection", refused)


def test_limit_comparison_checks_activity_ownership():
    calls = []

    def read(endpoint, params):
        calls.append((endpoint, params))
        if not endpoint:
            return {"id": "synthetic-athlete", "timezone": "America/New_York"}
        if params.get("limit") == "1":
            return [{"id": "synthetic-activity", "icu_athlete_id": PRIVATE}]
        return []

    with pytest.raises(probe.ProbeError, match="^Activity athlete identity mismatch"):
        probe.probe(read, NOW)
    assert len(calls) == 5  # Stop before requesting wellness.


@pytest.mark.parametrize(
    ("now", "zone", "oldest", "middle", "newest"),
    [
        # Before local midnight; UTC date must not choose the window.
        (
            datetime(2026, 3, 9, 3, 30, tzinfo=UTC),
            "America/New_York",
            "2026-03-01",
            "2026-03-04",
            "2026-03-07",
        ),
        # Windows containing the spring-forward and fall-back days.
        (
            datetime(2026, 3, 10, 3, 30, tzinfo=UTC),
            "America/New_York",
            "2026-03-02",
            "2026-03-05",
            "2026-03-08",
        ),
        (
            datetime(2026, 11, 3, 4, 30, tzinfo=UTC),
            "America/New_York",
            "2026-10-26",
            "2026-10-29",
            "2026-11-01",
        ),
        # Athlete date is ahead of UTC; leap day belongs to the window.
        (
            datetime(2024, 2, 29, 12, tzinfo=UTC),
            "Pacific/Kiritimati",
            "2024-02-23",
            "2024-02-26",
            "2024-02-29",
        ),
    ],
)
def test_seven_complete_local_days_and_bounded_unfiltered_request_plan(
    now, zone, oldest, middle, newest
):
    calls = []

    def read(endpoint, params):
        calls.append((endpoint, params))
        return {"id": PRIVATE, "timezone": zone} if not endpoint else []

    report = probe.probe(read, now)
    assert calls == [
        ("", {}),
        ("activities", {"oldest": oldest, "newest": newest, "limit": "1000"}),
        ("activities", {"oldest": oldest, "newest": middle, "limit": "1000"}),
        ("activities", {"oldest": middle, "newest": newest, "limit": "1000"}),
        ("activities", {"oldest": oldest, "newest": newest, "limit": "1"}),
        ("wellness", {"oldest": oldest, "newest": newest}),
        ("wellness", {"oldest": oldest, "newest": middle}),
        ("wellness", {"oldest": middle, "newest": newest}),
    ]
    assert report["window_days"] == 7
    assert report["requests_completed"] == 8
    assert report["observations"]["activities"]["full"]["records"] == 0
    assert report["observations"]["wellness"]["full"]["records"] == 0
    output = json.dumps(report)
    for secret in (PRIVATE, zone, oldest, middle, newest):
        assert secret not in output
    assert "Matching lists do not prove completeness" in output


def test_generated_report_preserves_missing_null_types_and_redacts_all_values():
    activities = [
        {
            "id": PRIVATE + "-a",
            "start_date_local": "2026-03-02T00:00:00",
            "distance": True,
            "moving_time": [PRIVATE],
            "analyzed": {PRIVATE: PRIVATE},
        },
        {
            "id": PRIVATE + "-b",
            "start_date_local": "2026-03-08T23:59:59",
            "icu_training_load": None,
            "external_id": 3.5,
        },
        {"id": PRIVATE + "-c", "start_date_local": None, "icu_training_load": 0},
        {
            "id": PRIVATE + "-d",
            "start_date_local": PRIVATE,
            "icu_training_load": PRIVATE,
            PRIVATE: PRIVATE,
        },
    ]
    wellness = [
        {"id": "2026-03-02", "ctl": None},
        {"id": "2026-03-08", "ctl": 0},
        {"id": "2026-03-09", "ctl": PRIVATE},
    ]

    def read(endpoint, params):
        if not endpoint:
            return {"id": PRIVATE, "timezone": "America/New_York", "name": PRIVATE}
        if endpoint == "wellness":
            return wellness
        if params["limit"] == "1":
            # An ignored limit is an observation, not grounds to claim completeness.
            return [activities[0], {"id": PRIVATE + "-f"}]
        if params["newest"] == "2026-03-05":
            return [activities[0], activities[2]]
        if params["oldest"] == "2026-03-05":
            return [activities[2], {"id": PRIVATE + "-e"}]
        return activities + [activities[0]]

    report = probe.probe(read, NOW)
    observation = report["observations"]["activities"]
    full = observation["full"]
    assert full["records"] == 5
    assert full["duplicate_ids"] == 1
    assert full["unreadable_dates"] == 2
    assert full["on_oldest_day"] == 2
    assert full["on_newest_day"] == 1
    assert full["fields"]["icu_training_load"] == {
        "missing": 2,
        "null": 1,
        "types": {"NoneType": 1, "int": 1, "str": 1},
    }
    assert full["fields"]["distance"]["types"] == {"bool": 2}
    assert full["fields"]["moving_time"]["types"] == {"list": 2}
    assert full["fields"]["analyzed"]["types"] == {"dict": 2}
    assert full["fields"]["external_id"]["types"] == {"float": 1}
    assert observation["full_only_ids"] == 2
    assert observation["subwindows_only_ids"] == 1
    assert observation["overlap_ids"] == 1
    assert observation["limit_one_count"] == 2
    assert observation["limit_one_ids_not_in_full"] == 1
    assert not observation["record_cap_reached"]
    assert report["observations"]["wellness"]["full"]["outside_requested_days"] == 1
    output = json.dumps(report)
    for secret in (
        "PRIVATE_GENERATED_VALUE",
        "malicious-secret",
        "Authorization:",
        "2026-03-02",
        "2026-03-08",
        "2026-03-09",
        "America/New_York",
        "3.5",
    ):
        assert secret not in output
    assert set(full["fields"]) == {
        "id",
        "icu_athlete_id",
        "source",
        "external_id",
        "start_date",
        "start_date_local",
        "timezone",
        "icu_training_load",
        "icu_sync_date",
        "analyzed",
        "distance",
        "moving_time",
    }


@pytest.mark.parametrize(
    "athlete",
    [
        [],
        {},
        {"id": None},
        {"id": 12},
        {"id": ""},
        {"id": PRIVATE, "timezone": None},
        {"id": PRIVATE, "timezone": []},
        {"id": PRIVATE, "timezone": PRIVATE},
        {"id": PRIVATE, "timezone": "../private"},
    ],
)
def test_invalid_identity_or_timezone_stops_before_listings(athlete):
    calls = []

    def read(endpoint, params):
        calls.append(endpoint)
        return athlete

    with pytest.raises(probe.ProbeError) as error:
        probe.probe(read, NOW)
    assert calls == [""]
    assert "PRIVATE" not in str(error.value)
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "listing",
    [
        None,
        {},
        [None],
        [PRIVATE],
        [{}],
        [{"id": None}],
        [{"id": 0}],
        [{"id": ""}],
        [{"id": "generated"}] * 1001,
    ],
)
def test_invalid_listing_stops_immediately_with_sanitized_error(listing):
    calls = []

    def read(endpoint, params):
        calls.append(endpoint)
        if not endpoint:
            return {"id": PRIVATE, "timezone": "UTC"}
        return listing

    with pytest.raises(probe.ProbeError) as error:
        probe.probe(read, NOW)
    assert calls == ["", "activities"]
    assert "PRIVATE" not in str(error.value)


def test_record_cap_is_an_observation_not_a_completeness_guarantee():
    def read(endpoint, params):
        if not endpoint:
            return {"id": "generated-athlete", "timezone": "UTC"}
        if endpoint == "wellness":
            return []
        count = 1 if params["limit"] == "1" else 1000
        return [{"id": f"generated-{i}"} for i in range(count)]

    report = probe.probe(read, NOW)
    assert report["observations"]["activities"]["record_cap_reached"]
    assert "Matching lists do not prove completeness" in json.dumps(report)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_refusal_closes_response_without_followup(monkeypatch, code):
    real_build = probe.build_opener
    calls = []
    body = io.BytesIO(PRIVATE.encode())

    def build(*handlers):
        opener = real_build(*handlers)

        def open_redirect(request, timeout):
            calls.append(request)
            headers = Message()
            headers["Location"] = "https://other.invalid/" + PRIVATE.splitlines()[0]
            return opener.error("http", request, body, code, PRIVATE, headers)

        monkeypatch.setattr(opener, "open", open_redirect)
        return opener

    monkeypatch.setattr(probe, "build_opener", build)
    with pytest.raises(probe.ProbeError, match="^Redirect refused; no follow-up request sent.$"):
        probe.make_reader("generated-key")("", {})
    assert len(calls) == 1
    assert body.closed


def test_mocked_transport_uses_direct_fixed_host_bounded_gets(monkeypatch):
    real_build = probe.build_opener
    requests, responses, read_sizes = [], [], []

    class Response(io.BytesIO):
        def read(self, size=-1):
            read_sizes.append(size)
            return super().read(size)

    def build(*handlers):
        proxy = next(handler for handler in handlers if isinstance(handler, ProxyHandler))
        assert proxy.proxies == {}
        opener = real_build(*handlers)
        redirects = [h for h in opener.handlers if isinstance(h, HTTPRedirectHandler)]
        assert len(redirects) == 1 and isinstance(redirects[0], probe.NoRedirect)

        def open_response(request, timeout):
            requests.append(request)
            assert timeout == 15
            url = urlsplit(request.full_url)
            assert url.scheme == "https" and url.netloc == "intervals.icu"
            assert url.path in {
                "/api/v1/athlete/0",
                "/api/v1/athlete/0/activities",
                "/api/v1/athlete/0/wellness",
            }
            assert set(parse_qs(url.query)) <= {"oldest", "newest", "limit"}
            assert request.get_method() == "GET" and request.data is None
            assert request.get_header("Authorization") == "Basic QVBJX0tFWTpnZW5lcmF0ZWQta2V5"
            assert request.get_header("Accept") == "application/json"
            payload = {"id": PRIVATE, "timezone": "UTC"} if not url.query else []
            response = Response(json.dumps(payload).encode())
            responses.append(response)
            return response

        monkeypatch.setattr(opener, "open", open_response)
        return opener

    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:1234")
    monkeypatch.setattr(probe, "build_opener", build)
    report = probe.probe(probe.make_reader("generated-key"), NOW)
    assert len(requests) == report["requests_completed"] == 8
    assert read_sizes == [probe.MAX_BYTES + 1] * 8
    assert all(response.closed for response in responses)
    assert "generated-key" not in json.dumps(report)
    with pytest.raises(probe.ProbeError, match="^Endpoint refused.$"):
        probe.make_reader("generated-key")("https://other.invalid/", {})
    assert len(requests) == 8


@pytest.mark.parametrize("failure", ["429", "401", "timeout", "json", "oversize"])
def test_transport_failures_are_sanitized_closed_and_not_retried(monkeypatch, failure):
    real_build = probe.build_opener
    calls = []
    body = io.BytesIO(PRIVATE.encode())
    if failure == "oversize":
        body = io.BytesIO(b"x" * (probe.MAX_BYTES + 1))

    def build(*handlers):
        opener = real_build(*handlers)

        def open_failure(request, timeout):
            calls.append(request)
            if failure in ("429", "401"):
                raise HTTPError(request.full_url, int(failure), PRIVATE, Message(), body)
            if failure == "timeout":
                raise TimeoutError(PRIVATE)
            return body

        monkeypatch.setattr(opener, "open", open_failure)
        return opener

    monkeypatch.setattr(probe, "build_opener", build)
    with pytest.raises(probe.ProbeError) as error:
        probe.make_reader("generated-key")("", {})
    assert len(calls) == 1
    assert (
        str(error.value)
        == {
            "429": "Rate limited; stopped without retry.",
            "401": "Provider HTTP failure; stopped without retry.",
            "timeout": "Transport or JSON failure; probe stopped.",
            "json": "Transport or JSON failure; probe stopped.",
            "oversize": "Response exceeded size cap; probe stopped.",
        }[failure]
    )
    assert "PRIVATE" not in str(error.value)
    if failure != "timeout":
        assert body.closed
    else:
        body.close()  # Not returned to the reader.


@pytest.mark.parametrize("failure", ["arguments", "stdin", "stderr", "warning", "eof", "cancel"])
def test_cli_refuses_unsafe_prompt_or_cancels_without_a_request(monkeypatch, capsys, failure):
    monkeypatch.setattr(
        probe.sys, "argv", ["probe", PRIVATE] if failure == "arguments" else ["probe"]
    )
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: failure != "stdin")
    monkeypatch.setattr(probe.sys.stderr, "isatty", lambda: failure != "stderr")

    def prompt(*args):
        if failure == "warning":
            warnings.warn(PRIVATE, probe.getpass.GetPassWarning, stacklevel=2)
        if failure == "eof":
            raise EOFError(PRIVATE)
        if failure == "cancel":
            raise KeyboardInterrupt(PRIVATE)
        pytest.fail("Must refuse before prompting")

    def refuse_reader(key):
        pytest.fail("Must not construct a reader")

    monkeypatch.setattr(probe.getpass, "getpass", prompt)
    monkeypatch.setattr(probe, "make_reader", refuse_reader)
    assert probe.main() == 1
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    assert "probe_version" not in captured.out
    assert captured.err


def test_cli_prints_only_report_or_static_error(monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["probe"])
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(probe.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(probe.getpass, "getpass", lambda prompt: "generated-key")
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)

    def read(endpoint, params):
        return {"id": PRIVATE, "timezone": "UTC"} if not endpoint else []

    monkeypatch.setattr(probe, "make_reader", lambda key: read)
    assert probe.main() == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out.split("\n", 1)[1])
    assert report["requests_completed"] == 8
    assert not captured.err
    assert "PRIVATE" not in captured.out and "generated-key" not in captured.out

    def failed_reader(key):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(probe, "make_reader", failed_reader)
    assert probe.main() == 1
    captured = capsys.readouterr()
    assert captured.err == "Probe failed; details withheld to protect private data.\n"
    assert "probe_version" not in captured.out
