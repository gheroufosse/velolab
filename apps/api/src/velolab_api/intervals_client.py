"""Synchronous, read-only provider boundary (ADR-024 slice 1); no settings or DB.

Contract source: docs/intervals-data-contract.md (public API v1 research).
Raw mappings preserve omitted keys and explicit nulls. Typed views are not
metric projections or proof of completeness, units, or source parity.
"""

import json
import math
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from urllib.parse import quote

import httpx
from pydantic import SecretStr


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    TRANSPORT = "transport_failure"
    HTTP = "http_failure"
    RATE_LIMIT = "rate_limited"
    RESPONSE_SIZE = "response_size_exceeded"
    RECORD_CAP = "record_cap_exceeded"
    RESPONSE_SHAPE = "invalid_response"
    ATHLETE_MISMATCH = "athlete_mismatch"


class IntervalsClientError(Exception):
    """Only static codes and numeric HTTP status survive the boundary."""

    def __init__(self, code: ErrorCode, status: int | None = None) -> None:
        if not isinstance(code, ErrorCode) or (
            status is not None and (type(status) is not int or not 100 <= status <= 599)
        ):
            code, status = ErrorCode.INVALID_INPUT, None
        self.code, self.status = code, status
        super().__init__(code.value, status)


class IntervalsRateLimitError(IntervalsClientError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.RATE_LIMIT, 429)


@dataclass(frozen=True, slots=True)
class IntervalsClientPolicy:
    """Local assumptions, not verified provider behavior; override deliberately."""

    # ADR-024 / blockers 1 and 4: probe-only UA and timeout evidence.
    user_agent: str = "Mozilla/5.0"
    timeout_seconds: float = 15.0
    # Blocker 1: cap/reached-limit is uncertainty, not pagination/completeness.
    activity_limit: int = 1000
    record_cap: int = 1000
    max_response_bytes: int = 4 * 1024 * 1024
    # ADR-024 / blocker 1: conservative retry budgets, not provider limits.
    max_429_retries: int = 2
    max_retry_after_seconds: float = 30.0
    fallback_backoff_seconds: float = 1.0
    # Blocker 3: no fields filter; retain nulls. Unsuffixed JSON wellness route.
    # Blocker 4: candidate identity/date fields only, no unit/zone projections.
    activity_athlete_field: str = "icu_athlete_id"
    activity_utc_field: str = "start_date"
    activity_local_field: str = "start_date_local"

    def __post_init__(self) -> None:
        valid = (
            all(
                type(value) is int and value > 0
                for value in (self.activity_limit, self.record_cap, self.max_response_bytes)
            )
            and self.activity_limit <= self.record_cap
            and type(self.max_429_retries) is int
            and 0 <= self.max_429_retries <= 10
            and all(
                type(value) in (int, float) and math.isfinite(value) and value > 0
                for value in (
                    self.timeout_seconds,
                    self.max_retry_after_seconds,
                    self.fallback_backoff_seconds,
                )
            )
        )
        valid = valid and all(
            isinstance(value, str) and value and value.isascii() and value.isprintable()
            for value in (
                self.user_agent,
                self.activity_athlete_field,
                self.activity_utc_field,
                self.activity_local_field,
            )
        )
        if not valid:
            raise IntervalsClientError(ErrorCode.INVALID_INPUT)

    def __repr__(self) -> str:
        return "<IntervalsClientPolicy>"


@dataclass(frozen=True, slots=True, repr=False)
class AthleteProfile:
    id: str
    timezone: str | None
    raw: Mapping[str, object] = field(repr=False)

    def __repr__(self) -> str:
        return "<AthleteProfile>"


@dataclass(frozen=True, slots=True, repr=False)
class ActivityRecord:
    id: str
    athlete_id: str | None
    start_date: datetime | None
    start_date_local: datetime | None
    raw: Mapping[str, object] = field(repr=False)

    def __repr__(self) -> str:
        return "<ActivityRecord>"


@dataclass(frozen=True, slots=True, repr=False)
class WellnessRecord:
    local_date: date
    raw: Mapping[str, object] = field(repr=False)

    def __repr__(self) -> str:
        return "<WellnessRecord>"


@dataclass(frozen=True, slots=True, repr=False)
class Listing[T]:
    records: tuple[T, ...]
    possibly_truncated: bool

    def __repr__(self) -> str:
        return "<Listing>"


def _athlete_segment(athlete_id: str) -> str:
    # ADR-024: explicit opaque ID, no alias 0 (cause unknown), not integer-only.
    # Probe safety policy, not provider ID grammar. Encode exactly one segment.
    if (
        not isinstance(athlete_id, str)
        or not athlete_id
        or athlete_id in ("0", ".", "..")
        or any(
            char.isspace() or unicodedata.category(char) in ("Cc", "Cf", "Cs") or char in "/\\%"
            for char in athlete_id
        )
    ):
        raise IntervalsClientError(ErrorCode.INVALID_INPUT)
    return quote(athlete_id, safe="", encoding="utf-8", errors="strict")


def _identity(raw: Mapping[str, object], key: str = "id") -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise IntervalsClientError(ErrorCode.RESPONSE_SHAPE)
    return value


def _optional_string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        raise IntervalsClientError(ErrorCode.RESPONSE_SHAPE)
    return value


def _optional_datetime(raw: Mapping[str, object], key: str) -> datetime | None:
    value = _optional_string(raw, key)
    if value is None:
        return None  # The raw mapping still distinguishes missing from null.
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    raise IntervalsClientError(ErrorCode.RESPONSE_SHAPE)


class IntervalsClient:
    """Bound credentials/athlete; close explicitly or use as a context manager.

    Only the transport and clock/sleeper are injectable, not host or HTTP client.
    A supplied transport is a trusted testing boundary. TLS stays verified,
    redirects and environment proxies are disabled. Cancellation propagates.
    """

    def __init__(
        self,
        api_key: SecretStr,
        athlete_id: str,
        *,
        policy: IntervalsClientPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        segment = _athlete_segment(athlete_id)
        if not isinstance(api_key, SecretStr):
            raise IntervalsClientError(ErrorCode.INVALID_INPUT)
        key = api_key.get_secret_value()
        if (
            not key
            or len(key) > 4096
            or any(unicodedata.category(char) in ("Cc", "Cs") for char in key)
        ):
            raise IntervalsClientError(ErrorCode.INVALID_INPUT)
        self._athlete_id = athlete_id
        self._path = f"/api/v1/athlete/{segment}"
        self._policy = policy or IntervalsClientPolicy()
        self._sleep, self._clock = sleep, clock
        self._http = httpx.Client(
            base_url="https://intervals.icu",
            auth=httpx.BasicAuth("API_KEY", key),
            headers={"Accept": "application/json", "User-Agent": self._policy.user_agent},
            timeout=httpx.Timeout(self._policy.timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return "<IntervalsClient>"

    def __enter__(self) -> IntervalsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _retry_wait(self, value: str | None, attempt: int) -> float | None:
        wait = None
        if value is not None:
            try:
                if value.isascii() and value.isdigit():
                    wait = float(value)
                else:
                    timestamp = parsedate_to_datetime(value)
                    if timestamp.tzinfo is not None:
                        wait = max(0.0, timestamp.timestamp() - self._clock())
            except ValueError, OverflowError, TypeError:
                pass
        if wait is None:
            wait = self._policy.fallback_backoff_seconds * 2**attempt
        # Never retry earlier than an advertised delay exceeding our budget.
        if not math.isfinite(wait) or wait > self._policy.max_retry_after_seconds:
            return None
        return wait

    def _read(self, endpoint: str, params: dict[str, str]) -> object:
        # Raise after the handler so __context__ cannot retain URL/body/key.
        failure = ErrorCode.TRANSPORT
        try:
            for attempt in range(self._policy.max_429_retries + 1):
                wait = None
                with self._http.stream("GET", self._path + endpoint, params=params) as response:
                    status = response.status_code
                    if status == 429:
                        if attempt < self._policy.max_429_retries:
                            wait = self._retry_wait(response.headers.get("Retry-After"), attempt)
                        if wait is None:
                            raise IntervalsRateLimitError()
                    elif not 200 <= status < 300:
                        raise IntervalsClientError(ErrorCode.HTTP, status)
                    else:
                        body = bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if len(body) + len(chunk) > self._policy.max_response_bytes:
                                raise IntervalsClientError(ErrorCode.RESPONSE_SIZE)
                            body.extend(chunk)
                        failure = ErrorCode.RESPONSE_SHAPE
                        return json.loads(body)
                # Close each response before sleeping; KeyboardInterrupt propagates.
                self._sleep(wait)
        except IntervalsClientError:
            raise
        except httpx.HTTPError, OSError, RuntimeError:
            failure = ErrorCode.TRANSPORT
        except ValueError, UnicodeError, RecursionError:
            failure = ErrorCode.RESPONSE_SHAPE
        raise IntervalsClientError(failure)

    def athlete_profile(self) -> AthleteProfile:
        raw = self._read("", {})
        if not isinstance(raw, dict):
            raise IntervalsClientError(ErrorCode.RESPONSE_SHAPE)
        identity = _identity(raw)
        if identity != self._athlete_id:
            raise IntervalsClientError(ErrorCode.ATHLETE_MISMATCH)
        return AthleteProfile(identity, _optional_string(raw, "timezone"), raw)

    def _listing(self, endpoint: str, oldest: date, newest: date) -> list[dict[str, object]]:
        if type(oldest) is not date or type(newest) is not date or oldest > newest:
            raise IntervalsClientError(ErrorCode.INVALID_INPUT)
        # Exact supplied local days. Overlapping windows belong to orchestration
        # (blocker 1); do not silently shift a caller's requested boundaries here.
        params = {"oldest": oldest.isoformat(), "newest": newest.isoformat()}
        if endpoint == "/activities":
            params["limit"] = str(self._policy.activity_limit)
        # The public wellness contract has no limit parameter; use record_cap
        # as its conservative uncertainty threshold, not a completeness claim.
        raw = self._read(endpoint, params)
        if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
            raise IntervalsClientError(ErrorCode.RESPONSE_SHAPE)
        if len(raw) > self._policy.record_cap:
            raise IntervalsClientError(ErrorCode.RECORD_CAP)
        return raw

    def activities(self, oldest: date, newest: date) -> Listing[ActivityRecord]:
        rows = self._listing("/activities", oldest, newest)
        records = []
        for raw in rows:
            athlete_id = _optional_string(raw, self._policy.activity_athlete_field)
            if athlete_id is not None and athlete_id != self._athlete_id:
                raise IntervalsClientError(ErrorCode.ATHLETE_MISMATCH)
            records.append(
                ActivityRecord(
                    _identity(raw),
                    athlete_id,
                    _optional_datetime(raw, self._policy.activity_utc_field),
                    _optional_datetime(raw, self._policy.activity_local_field),
                    raw,
                )
            )
        return Listing(tuple(records), len(rows) >= self._policy.activity_limit)

    def wellness(self, oldest: date, newest: date) -> Listing[WellnessRecord]:
        rows = self._listing("/wellness", oldest, newest)
        records = []
        for raw in rows:
            value = _identity(raw)
            try:
                local_date = date.fromisoformat(value)
                if local_date.isoformat() != value:
                    raise ValueError
            except ValueError:
                local_date = None
            if local_date is None:
                raise IntervalsClientError(ErrorCode.RESPONSE_SHAPE)
            records.append(WellnessRecord(local_date, raw))
        return Listing(tuple(records), len(rows) >= self._policy.record_cap)
