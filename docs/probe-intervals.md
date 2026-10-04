# Owner-run intervals.icu probe

`scripts/probe_intervals.py` is a standalone, read-only evidence-gathering tool,
not the app's client or sync implementation. **Only the owner runs it against
an account they control.** Agents/tests must not make personal API requests.
It imports no app code, reads no app settings or `.env`, accesses no database,
installs no dependencies and persists neither credentials nor responses.

## Run

Use the already-installed `apps/api` uv environment. From the repository root,
in an interactive terminal:

```text
cd apps/api
uv run --no-sync python ../../scripts/probe_intervals.py
```

Enter the API key only at the hidden prompt. There are no arguments, environment
variable, file or piped-input credential alternatives. Nonterminal stdin/stderr
and a failed hidden prompt stop the probe; there is no visible-input fallback.
Do not put the key in a command, chat, screenshot or shell history.

A successful run prints a JSON summary and exits zero. A failed/cancelled run
exits one with a static, sanitized message and **no partial JSON report**.
It never prints exception details, provider errors, request URLs or auth headers.
No retry is made, including on rate limiting. Do not automate repeated runs.

## What it requests

At most **eight GETs**, directly to the fixed HTTPS host `intervals.icu`:

1. `/api/v1/athlete/0`: resolve the authenticated athlete's identity and valid
   IANA timezone in memory. No timezone override or local-machine fallback.
2. `/api/v1/athlete/0/activities`: the last seven **complete calendar days** in
   that athlete timezone, excluding today; then two overlapping subwindows
   within those same days. Each requests `limit=1000`.
3. The same full activity window with `limit=1`, for a limit observation.
4. `/api/v1/athlete/0/wellness`: the full window and the same two subwindows.

The subwindows contain days 1–4 and 4–7, overlapping on day 4. Calendar arithmetic
preserves seven days across DST, not an assumed 168-hour duration. Exact dates
and timezone names are never emitted. No activity type, source or field-selection
filter is supplied; no activity details or streams are fetched.

Redirects and environment proxies are disabled. Each request has a 15-second
socket-operation timeout (not a guaranteed total run deadline), a 4 MiB response
cap and a 1000-record client cap. Wellness has no server-side `limit` parameter.
There is a short pause before requests, but no retries, pagination or follow-up
requests after a failure. Unexpected JSON/record shapes, missing identities,
invalid athlete timezone, or a present non-null activity athlete ID that differs
from the resolved identity stop the run. Missing activity ownership fields are
not proof of ownership. Invalid record dates are counted, not printed.

## Data reduction and interpretation

Raw responses and authorization exist only in process memory. The hidden prompt
and lack of raw files reduce exposure; they are **not secure memory erasure** or
protection against a compromised terminal/machine. The script deliberately reads
unfiltered responses to observe omission; those responses can contain private
fields beyond the summary allowlist.

The summary emits only static labels, counts and booleans:

- Record/duplicate-ID counts; unreadable dates, dates outside the requested
  window, and records on its first/last day. Activities use `start_date_local`;
  wellness uses its date-shaped `id`.
- Missing, explicit-null and JSON/Python type counts for fixed, allowlisted
  fields. Null also appears in type counts as `NoneType`; zero is non-null
  numeric data, not missing, but its value is never emitted. String contents,
  nested values and unknown field names are discarded from output.
- Counts of identities present only in the full response or only in the union
  of subwindows, and counts shared by both subwindows. No IDs are printed.
- Whether any regular listing reached the record cap, the number returned for
  `limit=1`, and how many of those identities were absent from the full listing.

These are **observations, not completeness guarantees**. Matching lists, fewer
than 1000 rows, or one row returned for `limit=1` do not establish that the server
honors limits or that all records were returned. Sequential requests can observe
upstream changes. Empty boundary days cannot establish endpoint inclusivity;
a count discrepancy alone does not explain its cause. Unexpected fields/types
remain observations rather than silently asserted provider schema guarantees.

This probe does not prove metric values/units/date alignment or numerical source
parity, historical correction/deletion behavior, explicit clearing semantics,
provider partial-update behavior, or timezone-change behavior. It does not clear,
reconcile or delete anything. Those contract questions remain separate work.

Even reduced counts and missingness patterns can fingerprint private health or
activity history. Review the JSON locally before sharing any subset; do not
capture/share the prompt, key, raw payloads, dates, IDs or timezone. The tool does
not save a report automatically. Do not turn personal responses into fixtures;
automated tests use freshly generated synthetic data only.

## Local verification (no personal requests)

From `apps/api`, using the same existing environment:

```text
uv run --no-sync pytest tests/test_probe_intervals.py
uv run --no-sync ruff check --config pyproject.toml ../../scripts/probe_intervals.py tests/test_probe_intervals.py
uv run --no-sync ruff format --check --config pyproject.toml ../../scripts/probe_intervals.py tests/test_probe_intervals.py
uv run --no-sync ty check ../../scripts/probe_intervals.py tests/test_probe_intervals.py
```

The tests import the standalone script via `importlib` without running its CLI.
They generate responses, control time, simulate terminals and mock the external
HTTP boundary; real connections are forbidden. They are included in the existing
API pytest suite. No disposable or real-use database is needed for these tests.
Passing mocks verifies local safety/summary behavior, not the live provider.
