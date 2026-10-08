"""Generated responses only; importing the standalone probe must not run its CLI.

Failure inventory / seams (probe report, mocked HTTP adapter, terminal CLI):
- DST/local midnight: wrong complete-day window; assert literal provider-local dates.
- Missing/null/zero/wrong types or hostile strings: lost distinctions or disclosure;
  assert allowlisted counts/types and absence of values through the probe report.
- Overlapping windows/ignored limit/duplicates: misleading observations; assert the
  bounded GET plan and discrepancies, never a completeness claim.
- Invalid timezone, identity, listing shape/cap: unsafe continuation; stop with
  static errors, including the limit-comparison response's ownership check.
- HTTP status/request diagnostics: any of the eight planned requests can fail,
  hiding where/which status (including 429); drive main -> probe -> make_reader
  with poisoned HTTPError URL/reason/headers/body and synthetic credentials.
  Require numeric status + plan-owned label only, response cleanup, immediate
  exit without retries/follow-ups or partial JSON. The old adapter-only test
  cannot establish CLI labels; isolation is only at the external HTTP boundary.
- Explicit athlete selection: hardcoded 0 targets the wrong path; run the CLI
  with hidden synthetic ID/key prompts and inspect actual Request URLs. Every
  planned request must use the unchanged ID string, with no ID in output/files.
- Opaque selection (letters, Unicode, punctuation, leading/all-zero strings):
  numeric assumptions reject valid input or mutate identity; drive all eight CLI
  GETs with literal expected encoded segments and unchanged query parameters.
- Invalid selection (empty, exact 0 alias, dot segments, whitespace, Cc/Cf/Cs,
  slash/backslash/percent): unsafe routing or ambiguous identity; reject before
  the key prompt or opener, including direct make_reader callers. Validation is
  a local conservative safety policy, not a provider schema constraint.
- Returned profile/activity ID differs, including canonically equivalent Unicode:
  wrong athlete's data processed; compare the original string exactly and stop
  with a static error, no partial report or identity disclosure.
- User-Agent: urllib's default remains unless explicitly overridden; inspect all
  eight CLI Request headers for literal Mozilla/5.0, keeping auth/Accept and the
  direct bounded GET controls unchanged. Synthetic transport cannot prove that
  provider User-Agent filtering caused the 403 or that this change resolves it.
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
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "probe_intervals.py"
spec = importlib.util.spec_from_file_location("standalone_intervals_probe", SCRIPT)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

NOW = datetime(2026, 3, 9, 12, tzinfo=UTC)
ATHLETE_ID = "00123456789"  # Synthetic; leading digits must survive as a string.
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
        probe.probe(read, "synthetic-athlete", NOW)
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

    report = probe.probe(read, PRIVATE, now)
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

    report = probe.probe(read, PRIVATE, NOW)
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
        probe.probe(read, PRIVATE, NOW)
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
        probe.probe(read, PRIVATE, NOW)
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

    report = probe.probe(read, "generated-athlete", NOW)
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
        probe.make_reader("generated-key", ATHLETE_ID)("", {})
    assert len(calls) == 1
    assert body.closed


@pytest.mark.parametrize(
    ("athlete_id", "encoded_id"),
    [
        (ATHLETE_ID, "00123456789"),
        ("synthetic-a123", "synthetic-a123"),
        ("vélo-東京", "v%C3%A9lo-%E6%9D%B1%E4%BA%AC"),
        ("ve\u0301lo", "ve%CC%81lo"),
        ("١٢٣", "%D9%A1%D9%A2%D9%A3"),
        ("１２３", "%EF%BC%91%EF%BC%92%EF%BC%93"),
        ("athlete?x=1#:@+", "athlete%3Fx%3D1%23%3A%40%2B"),
        ("-1", "-1"),
        ("+1", "%2B1"),
        ("000", "000"),
        (".athlete..", ".athlete.."),
    ],
)
def test_cli_uses_explicit_athlete_id_for_all_direct_bounded_gets(
    monkeypatch, capsys, athlete_id, encoded_id
):
    monkeypatch.setattr(probe.sys, "argv", ["probe"])
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(probe.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(
        probe.getpass,
        "getpass",
        lambda prompt: athlete_id if "athlete ID" in prompt else "generated-key",
    )
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(probe, "datetime", Clock)
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
            assert url.fragment == ""
            assert request.get_method() == "GET" and request.data is None
            assert request.get_header("Authorization") == "Basic QVBJX0tFWTpnZW5lcmF0ZWQta2V5"
            assert request.get_header("Accept") == "application/json"
            if not url.query:
                payload = {"id": athlete_id, "timezone": "UTC"}
            elif url.path.endswith("/activities"):
                payload = [
                    {
                        "id": "generated-activity",
                        "icu_athlete_id": athlete_id,
                        "start_date_local": "2026-03-02T12:00:00",
                    }
                ]
            else:
                payload = []
            response = Response(json.dumps(payload).encode())
            responses.append(response)
            return response

        monkeypatch.setattr(opener, "open", open_response)
        return opener

    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:1234")
    monkeypatch.setattr(probe, "build_opener", build)
    assert probe.main() == 0
    captured = capsys.readouterr()
    assert not captured.err
    report = json.loads(captured.out.split("\n", 1)[1])
    assert len(requests) == report["requests_completed"] == 8
    for request in requests:
        assert request.get_header("User-agent") == "Mozilla/5.0"
    assert [urlsplit(request.full_url).path for request in requests] == [
        f"/api/v1/athlete/{encoded_id}",
        *[f"/api/v1/athlete/{encoded_id}/activities"] * 4,
        *[f"/api/v1/athlete/{encoded_id}/wellness"] * 3,
    ]
    assert [urlsplit(request.full_url).query for request in requests] == [
        "",
        "oldest=2026-03-02&newest=2026-03-08&limit=1000",
        "oldest=2026-03-02&newest=2026-03-05&limit=1000",
        "oldest=2026-03-05&newest=2026-03-08&limit=1000",
        "oldest=2026-03-02&newest=2026-03-08&limit=1",
        "oldest=2026-03-02&newest=2026-03-08",
        "oldest=2026-03-02&newest=2026-03-05",
        "oldest=2026-03-05&newest=2026-03-08",
    ]
    assert read_sizes == [probe.MAX_BYTES + 1] * 8
    assert all(response.closed for response in responses)
    output = captured.out + captured.err
    for secret in (athlete_id, encoded_id, json.dumps(athlete_id)[1:-1], "generated-key"):
        assert secret not in output
    with pytest.raises(probe.ProbeError, match="^Endpoint refused.$"):
        probe.make_reader("generated-key", athlete_id)("https://other.invalid/", {})
    assert len(requests) == 8


@pytest.mark.parametrize(
    ("athlete_id", "returned_id", "encoded_id", "failed_request", "label"),
    [
        (ATHLETE_ID, PRIVATE, "00123456789", 1, "Athlete profile"),
        ("vélo", "ve\u0301lo", "v%C3%A9lo", 1, "Athlete profile"),
        ("ve\u0301lo", "vélo", "ve%CC%81lo", 2, "Activity athlete"),
        ("Case-ID", "case-id", "Case-ID", 1, "Athlete profile"),
        ("000", "0", "000", 2, "Activity athlete"),
    ],
)
def test_cli_identity_mismatch_stops_immediately_without_private_output(
    monkeypatch, capsys, athlete_id, returned_id, encoded_id, failed_request, label
):
    monkeypatch.setattr(probe.sys, "argv", ["probe"])
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(probe.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(
        probe.getpass,
        "getpass",
        lambda prompt: athlete_id if "athlete ID" in prompt else "generated-key",
    )
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    real_build = probe.build_opener
    calls, responses = [], []

    def build(*handlers):
        opener = real_build(*handlers)

        def open_response(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                payload = {
                    "id": returned_id if failed_request == 1 else athlete_id,
                    "timezone": "UTC",
                }
            else:
                payload = [{"id": "generated-activity", "icu_athlete_id": returned_id}]
            response = io.BytesIO(json.dumps(payload).encode())
            responses.append(response)
            return response

        monkeypatch.setattr(opener, "open", open_response)
        return opener

    monkeypatch.setattr(probe, "build_opener", build)
    assert probe.main() == 1
    captured = capsys.readouterr()
    assert captured.err == f"{label} identity mismatch; probe stopped.\n"
    assert captured.out == (
        "Read-only probe: eight GETs max, seven complete local days, no raw files.\n"
    )
    assert len(calls) == failed_request
    assert calls[0].full_url == f"https://intervals.icu/api/v1/athlete/{encoded_id}"
    assert all(response.closed for response in responses)
    for secret in (athlete_id, returned_id, encoded_id, "PRIVATE_GENERATED_VALUE", "generated-key"):
        assert secret not in captured.out + captured.err


@pytest.mark.parametrize(
    ("failed_request", "label", "code"),
    [
        (1, "athlete/profile", 401),
        (2, "activities/full", 403),
        (3, "activities/left", 404),
        (4, "activities/right", 500),
        (5, "activities/limit-one", 429),
        (6, "wellness/full", 403),
        (7, "wellness/left", 502),
        (8, "wellness/right", 429),
    ],
)
def test_cli_http_failure_reports_only_status_and_static_request_label(
    monkeypatch, capsys, failed_request, label, code
):
    monkeypatch.setattr(probe.sys, "argv", ["probe"])
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(probe.sys.stderr, "isatty", lambda: True)
    key = "generated-private-key"
    monkeypatch.setattr(
        probe.getpass, "getpass", lambda prompt: ATHLETE_ID if "athlete ID" in prompt else key
    )
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    real_build = probe.build_opener
    calls, responses = [], []
    body = io.BytesIO(PRIVATE.encode())

    def build(*handlers):
        opener = real_build(*handlers)

        def open_response(request, timeout):
            calls.append(request)
            if len(calls) == failed_request:
                headers = Message()
                headers["Authorization"] = request.get_header("Authorization")
                headers["X-Private"] = PRIVATE.splitlines()[0]
                raise HTTPError(request.full_url + "&private=" + key, code, PRIVATE, headers, body)
            payload = {"id": ATHLETE_ID, "timezone": "UTC"} if len(calls) == 1 else []
            response = io.BytesIO(json.dumps(payload).encode())
            responses.append(response)
            return response

        monkeypatch.setattr(opener, "open", open_response)
        return opener

    monkeypatch.setattr(probe, "build_opener", build)
    assert probe.main() == 1
    captured = capsys.readouterr()
    suffix = "rate limited; stopped without retry." if code == 429 else "stopped without retry."
    assert captured.err == f"{label}: HTTP {code}; {suffix}\n"
    assert captured.out == (
        "Read-only probe: eight GETs max, seven complete local days, no raw files.\n"
    )
    assert len(calls) == failed_request  # No retry or later planned requests.
    assert body.closed and all(response.closed for response in responses)
    output = captured.out + captured.err
    for secret in (
        key,
        ATHLETE_ID,
        "PRIVATE_GENERATED_VALUE",
        "malicious-secret",
        "Authorization",
        "intervals.icu",
        "oldest=",
        "newest=",
        calls[-1].get_header("Authorization"),
    ):
        assert secret not in output


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
        probe.make_reader("generated-key", ATHLETE_ID)("", {})
    assert len(calls) == 1
    assert (
        str(error.value)
        == {
            "429": "HTTP 429; rate limited; stopped without retry.",
            "401": "HTTP 401; stopped without retry.",
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


@pytest.mark.parametrize(
    "athlete_id",
    [
        "",
        "0",
        ".",
        "..",
        " 1",
        "1 ",
        "1\t2",
        "12\n",
        "12\r",
        "a\u00a0b",  # Nonbreaking space.
        "a\u2028b",  # Line separator (Zl).
        "a\u2029b",  # Paragraph separator (Zp).
        "a\x00b",  # Cc: NUL.
        "a\x7fb",  # Cc: DEL.
        "a\x85b",  # Cc: next line.
        "a\u200bb",  # Cf: zero-width space.
        "a\u202eb",  # Cf: bidi override.
        "a\ufeffb",  # Cf: BOM.
        "a\ud800b",  # Cs: high surrogate.
        "a\udfffb",  # Cs: low surrogate.
        "12/../34",
        "12\\34",
        "12%34",
        "%2F",
        "%252F",
        "%30",
    ],
)
def test_cli_rejects_invalid_athlete_id_before_key_prompt_or_transport(
    monkeypatch, capsys, athlete_id
):
    monkeypatch.setattr(probe.sys, "argv", ["probe"])
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(probe.sys.stderr, "isatty", lambda: True)
    prompts = []

    def prompt(message):
        prompts.append(message)
        return athlete_id if len(prompts) == 1 else "generated-key"

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid athlete ID must not construct a transport")

    monkeypatch.setattr(probe.getpass, "getpass", prompt)
    monkeypatch.setattr(probe, "build_opener", forbidden)
    assert probe.main() == 1
    captured = capsys.readouterr()
    assert captured.err == "Invalid athlete ID; use an explicit ID without unsafe characters.\n"
    assert len(prompts) == 1 and "athlete ID" in prompts[0]
    assert captured.out == (
        "Read-only probe: eight GETs max, seven complete local days, no raw files.\n"
    )
    # Direct callers must get the same validation before opener construction.
    with pytest.raises(probe.ProbeError) as error:
        probe.make_reader("generated-key", athlete_id)
    assert str(error.value) + "\n" == captured.err


def test_reader_refuses_unsafe_athlete_path_before_constructing_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe athlete path must not construct a transport")

    monkeypatch.setattr(probe, "build_opener", forbidden)
    with pytest.raises(probe.ProbeError, match="^Invalid athlete ID;"):
        probe.make_reader("generated-key", "12/../activities?private=value")


@pytest.mark.parametrize(
    ("failure", "prompt_phase"),
    [
        ("arguments", None),
        ("stdin", None),
        ("stderr", None),
        ("warning", "athlete"),
        ("eof", "athlete"),
        ("cancel", "athlete"),
        ("warning", "key"),
        ("eof", "key"),
        ("cancel", "key"),
    ],
)
def test_cli_refuses_unsafe_prompt_or_cancels_without_a_request(
    monkeypatch, capsys, failure, prompt_phase
):
    monkeypatch.setattr(
        probe.sys, "argv", ["probe", PRIVATE] if failure == "arguments" else ["probe"]
    )
    monkeypatch.setattr(probe.sys.stdin, "isatty", lambda: failure != "stdin")
    monkeypatch.setattr(probe.sys.stderr, "isatty", lambda: failure != "stderr")

    def prompt(message):
        if "athlete ID" in message and prompt_phase == "key":
            return ATHLETE_ID
        if failure == "warning":
            warnings.warn(PRIVATE, probe.getpass.GetPassWarning, stacklevel=2)
        if failure == "eof":
            raise EOFError(PRIVATE)
        if failure == "cancel":
            raise KeyboardInterrupt(PRIVATE)
        pytest.fail("Must refuse before prompting")

    def refuse_reader(key, athlete_id):
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
    monkeypatch.setattr(
        probe.getpass,
        "getpass",
        lambda prompt: ATHLETE_ID if "athlete ID" in prompt else "generated-key",
    )
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)

    def read(endpoint, params):
        return {"id": ATHLETE_ID, "timezone": "UTC"} if not endpoint else []

    monkeypatch.setattr(probe, "make_reader", lambda key, athlete_id: read)
    assert probe.main() == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out.split("\n", 1)[1])
    assert report["requests_completed"] == 8
    assert not captured.err
    assert "PRIVATE" not in captured.out and "generated-key" not in captured.out

    def failed_reader(key, athlete_id):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(probe, "make_reader", failed_reader)
    assert probe.main() == 1
    captured = capsys.readouterr()
    assert captured.err == "Probe failed; details withheld to protect private data.\n"
    assert "probe_version" not in captured.out
