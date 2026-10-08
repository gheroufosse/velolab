"""Owner-run, read-only provider probe. No application settings or persistence."""

import base64
import getpass
import json
import sys
import time
import unicodedata
import warnings
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

BASE = "https://intervals.icu/api/v1/athlete"
MAX_BYTES = 4 * 1024 * 1024
LIMIT = 1000
FIELDS = {
    "activities": (
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
    ),
    "wellness": (
        "id",
        "ctl",
        "atl",
        "rampRate",
        "weight",
        "restingHR",
        "updated",
        "tempWeight",
        "tempRestingHR",
    ),
}


class ProbeError(Exception):
    """Only static labels and validated numeric HTTP statuses belong here."""


class ProviderHTTPError(ProbeError):
    """Safe HTTP failure, distinct from other probe errors for request labeling."""

    def __init__(self, status: int):
        if type(status) is not int or not 100 <= status <= 599:
            raise ProbeError("Provider HTTP failure; stopped without retry.")
        suffix = (
            "rate limited; stopped without retry." if status == 429 else "stopped without retry."
        )
        super().__init__(f"HTTP {status}; {suffix}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ProbeError("Redirect refused; no follow-up request sent.")


def validate_athlete_id(athlete_id: str) -> None:
    # Conservative local safety policy, not a provider schema restriction.
    # Keep opaque identity unchanged; only exact "0" selects the authenticated alias.
    if (
        not athlete_id
        or athlete_id in ("0", ".", "..")
        or any(
            char.isspace() or unicodedata.category(char) in ("Cc", "Cf", "Cs") or char in "/\\%"
            for char in athlete_id
        )
    ):
        raise ProbeError("Invalid athlete ID; use an explicit ID without unsafe characters.")


def make_reader(key: str, athlete_id: str) -> Callable[[str, dict[str, str]], object]:
    validate_athlete_id(athlete_id)
    encoded_id = quote(athlete_id, safe="", encoding="utf-8", errors="strict")
    # Direct TLS connection: no environment proxies or cross-host redirects.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    authorization = "Basic " + base64.b64encode(("API_KEY:" + key).encode()).decode("ascii")

    def read(endpoint: str, params: dict[str, str]) -> object:
        if endpoint not in ("", "activities", "wellness"):
            raise ProbeError("Endpoint refused.")
        url = BASE + "/" + encoded_id + ("/" + endpoint if endpoint else "")
        if params:
            url += "?" + urlencode(params)
        request = Request(
            url,
            headers={
                "Authorization": authorization,
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            with opener.open(request, timeout=15) as response:
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ProbeError("Response exceeded size cap; probe stopped.")
            return json.loads(body)
        except HTTPError as exc:
            exc.close()
            # Only the numeric status survives; no URL, headers, body or reason.
            raise ProviderHTTPError(exc.code) from None
        except ProbeError:
            raise
        except Exception:
            raise ProbeError("Transport or JSON failure; probe stopped.") from None

    return read


def rows(value: object) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ProbeError("Unexpected listing shape; probe stopped.")
    if len(value) > LIMIT:
        raise ProbeError("Listing exceeded record cap; probe stopped.")
    return value


def record_date(row: dict, endpoint: str) -> date | None:
    value = row.get("id" if endpoint == "wellness" else "start_date_local")
    if not isinstance(value, str):
        return None
    try:
        if endpoint == "wellness":
            return date.fromisoformat(value)
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def identities(records: list[dict]) -> set[str]:
    result = set()
    for row in records:
        value = row.get("id")
        if not isinstance(value, str) or not value:
            raise ProbeError("Missing or invalid record identity; probe stopped.")
        result.add(value)
    return result


def summarize(records: list[dict], endpoint: str, oldest: date, newest: date) -> dict:
    dates = [record_date(row, endpoint) for row in records]
    fields = {}
    for field in FIELDS[endpoint]:
        present = [row[field] for row in records if field in row]
        # Emit fixed type labels only, never values or provider-defined field names.
        types = Counter(type(value).__name__ for value in present)
        fields[field] = {
            "missing": len(records) - len(present),
            "null": sum(value is None for value in present),
            "types": dict(sorted(types.items())),
        }
    return {
        "records": len(records),
        "duplicate_ids": len(records) - len(identities(records)),
        "unreadable_dates": dates.count(None),
        "outside_requested_days": sum(d is not None and not oldest <= d <= newest for d in dates),
        "on_oldest_day": dates.count(oldest),
        "on_newest_day": dates.count(newest),
        "fields": fields,
    }


def probe(
    read: Callable[[str, dict[str, str]], object],
    requested_athlete_id: str,
    now: datetime | None = None,
) -> dict:
    def request(endpoint: str, params: dict[str, str], label: str) -> object:
        # Labels come exclusively from the fixed request plan below, never data.
        try:
            return read(endpoint, params)
        except ProviderHTTPError as exc:
            raise ProbeError(f"{label}: {exc}") from None

    athlete = request("", {}, "athlete/profile")
    if not isinstance(athlete, dict):
        raise ProbeError("Unexpected athlete shape; probe stopped.")
    athlete_id = athlete.get("id")
    if not isinstance(athlete_id, str) or not athlete_id:
        raise ProbeError("Missing athlete identity; probe stopped.")
    if athlete_id != requested_athlete_id:
        raise ProbeError("Athlete profile identity mismatch; probe stopped.")
    try:
        zone = ZoneInfo(athlete["timezone"])
    except Exception:
        raise ProbeError("Missing or invalid athlete IANA timezone; probe stopped.") from None
    today = (now or datetime.now(UTC)).astimezone(zone).date()
    oldest, newest = today - timedelta(days=7), today - timedelta(days=1)
    report = {
        "probe_version": 1,
        "window_days": 7,
        "athlete_identity_present": True,
        "iana_timezone_valid": True,
        "requests_completed": 1,
        "observations": {},
        "limitations": [
            "Counts and field availability are private; review before sharing.",
            "Matching lists do not prove completeness; sequential requests can see changes.",
            "Empty boundaries cannot establish inclusive or exclusive semantics.",
            "No deletion, clearing, units, numerical parity or timezone-change proof.",
            "No field values, identities, exact dates or timezone are emitted.",
        ],
    }

    def listing(endpoint: str, params: dict[str, str], part: str) -> list[dict]:
        batch = rows(request(endpoint, params, f"{endpoint}/{part}"))
        identities(batch)
        if endpoint == "activities" and any(
            row.get("icu_athlete_id") is not None and row["icu_athlete_id"] != athlete_id
            for row in batch
        ):
            raise ProbeError("Activity athlete identity mismatch; probe stopped.")
        return batch

    for endpoint in ("activities", "wellness"):
        batches = []
        # Two subwindows overlap on middle day; all requested dates stay within seven days.
        windows = [
            (oldest, newest),
            (oldest, oldest + timedelta(days=3)),
            (oldest + timedelta(days=3), newest),
        ]
        for part, (lo, hi) in zip(("full", "left", "right"), windows, strict=True):
            params = {"oldest": lo.isoformat(), "newest": hi.isoformat()}
            if endpoint == "activities":
                params["limit"] = str(LIMIT)
            batches.append(listing(endpoint, params, part))
            report["requests_completed"] += 1
        full, left, right = batches
        union = identities(left) | identities(right)
        observation = {
            "full": summarize(full, endpoint, oldest, newest),
            "left": summarize(left, endpoint, windows[1][0], windows[1][1]),
            "right": summarize(right, endpoint, windows[2][0], windows[2][1]),
            "full_only_ids": len(identities(full) - union),
            "subwindows_only_ids": len(union - identities(full)),
            "overlap_ids": len(identities(left) & identities(right)),
            "record_cap_reached": any(len(batch) == LIMIT for batch in batches),
        }
        if endpoint == "activities":
            small = listing(
                endpoint,
                {
                    "oldest": oldest.isoformat(),
                    "newest": newest.isoformat(),
                    "limit": "1",
                },
                "limit-one",
            )
            report["requests_completed"] += 1
            observation["limit_one_count"] = len(small)
            observation["limit_one_ids_not_in_full"] = len(identities(small) - identities(full))
        report["observations"][endpoint] = observation
    return report


def main() -> int:
    if len(sys.argv) != 1:
        print("No arguments accepted; API key belongs only in hidden prompt.", file=sys.stderr)
        return 1
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("Interactive terminal required; no stdin/file credential fallback.", file=sys.stderr)
        return 1
    print("Read-only probe: eight GETs max, seven complete local days, no raw files.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            athlete_id = getpass.getpass("Intervals.icu athlete ID (hidden): ")
            validate_athlete_id(athlete_id)
            key = getpass.getpass("Intervals.icu API key (hidden): ")
        if not key or len(key) > 4096 or any(ord(char) < 32 for char in key):
            raise ProbeError("Invalid credential input; probe stopped.")
        reader = make_reader(key, athlete_id)
        del key  # Not secure memory erasure: authorization remains in process memory.

        def paced_read(endpoint: str, params: dict[str, str]) -> object:
            time.sleep(0.25)
            return reader(endpoint, params)

        report = probe(paced_read, athlete_id)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except KeyboardInterrupt, EOFError:
        print("Probe cancelled; no report produced.", file=sys.stderr)
    except ProbeError as exc:
        print(str(exc), file=sys.stderr)
    except Exception:
        print("Probe failed; details withheld to protect private data.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
