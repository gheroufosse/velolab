"""Owner-run, read-only provider probe. No application settings or persistence."""

import base64
import getpass
import json
import sys
import time
import warnings
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

BASE = "https://intervals.icu/api/v1/athlete/0"
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
    """Only static, safe messages belong here."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ProbeError("Redirect refused; no follow-up request sent.")


def make_reader(key: str) -> Callable[[str, dict[str, str]], object]:
    # Direct TLS connection: no environment proxies or cross-host redirects.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    authorization = "Basic " + base64.b64encode(("API_KEY:" + key).encode()).decode("ascii")

    def read(endpoint: str, params: dict[str, str]) -> object:
        if endpoint not in ("", "activities", "wellness"):
            raise ProbeError("Endpoint refused.")
        url = BASE + ("/" + endpoint if endpoint else "")
        if params:
            url += "?" + urlencode(params)
        request = Request(
            url, headers={"Authorization": authorization, "Accept": "application/json"}
        )
        try:
            with opener.open(request, timeout=15) as response:
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ProbeError("Response exceeded size cap; probe stopped.")
            return json.loads(body)
        except HTTPError as exc:
            exc.close()
            # Do not display URL, headers, body, reason or exception details.
            if exc.code == 429:
                raise ProbeError("Rate limited; stopped without retry.") from None
            raise ProbeError("Provider HTTP failure; stopped without retry.") from None
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


def probe(read: Callable[[str, dict[str, str]], object], now: datetime | None = None) -> dict:
    athlete = read("", {})
    if not isinstance(athlete, dict):
        raise ProbeError("Unexpected athlete shape; probe stopped.")
    athlete_id = athlete.get("id")
    if not isinstance(athlete_id, str) or not athlete_id:
        raise ProbeError("Missing athlete identity; probe stopped.")
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

    def listing(endpoint: str, params: dict[str, str]) -> list[dict]:
        batch = rows(read(endpoint, params))
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
        for lo, hi in windows:
            params = {"oldest": lo.isoformat(), "newest": hi.isoformat()}
            if endpoint == "activities":
                params["limit"] = str(LIMIT)
            batches.append(listing(endpoint, params))
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
            key = getpass.getpass("Intervals.icu API key (hidden): ")
        if not key or len(key) > 4096 or any(ord(char) < 32 for char in key):
            raise ProbeError("Invalid credential input; probe stopped.")
        reader = make_reader(key)
        del key  # Not secure memory erasure: authorization remains in process memory.

        def paced_read(endpoint: str, params: dict[str, str]) -> object:
            time.sleep(0.25)
            return reader(endpoint, params)

        report = probe(paced_read)
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
