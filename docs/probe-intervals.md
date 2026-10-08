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

Enter your explicit athlete ID at the first hidden prompt, then the API key at
the second hidden prompt. Neither value is echoed or included in the report.
There are no arguments, environment variable, file or piped-input alternatives.
Nonterminal stdin/stderr and a failed hidden prompt stop the probe; there is no
visible-input fallback. Do not put either value in a command, chat, screenshot
or shell history.

The ID is a nonempty **opaque Unicode string**, not a number. Letters, non-ASCII
digits, signs and punctuation such as `?`, `#`, `:`, `@` and `+` are accepted.
Leading zeros are preserved, and `000` is accepted without numeric interpretation.
Only exact `0` is refused because it selects the authenticated-athlete alias rather
than an explicit ID. Exact `.` and `..`, any whitespace (`isspace()`), Unicode
control/format/surrogate characters (categories `Cc`, `Cf`, `Cs`), slash,
backslash and literal percent are also rejected before the key prompt or transport.
Percent rejection includes already-encoded input; paste the original ID instead.

The original string is preserved exactly: no stripping, Unicode normalization,
case folding, integer conversion or URL decoding. It is UTF-8 percent-encoded
once as a single path segment using `quote(id, safe='', encoding='utf-8',
errors='strict')`; query parameters are encoded separately with `urlencode`.
Thus accepted `?` and `#` belong to the ID, never to the URL query or fragment.
These exclusions are a **local conservative probe safety policy**, not a provider
schema constraint or a guarantee that every accepted string identifies an athlete.

A successful run prints a JSON summary and exits zero. A failed/cancelled run
exits one with a sanitized message and **no partial JSON report**. HTTP failures
include only the numeric status and a static label from the request plan, for
example:

```text
activities/full: HTTP 403; stopped without retry.
activities/limit-one: HTTP 429; rate limited; stopped without retry.
```

The labels are `athlete/profile`, `activities/full`, `activities/left`,
`activities/right`, `activities/limit-one`, `wellness/full`, `wellness/left` and
`wellness/right`. They identify the planned request, not a provider-supplied
string. Other failures retain static messages. Exception details, provider text,
bodies, request URLs, dates, IDs, headers and credentials are never printed.
No retry is made, including on rate limiting. Do not automate repeated runs.

The status and label help diagnose where a run stopped; they do not establish
why the provider rejected it or fix that underlying failure. A local mocked
HTTP-error test verifies these diagnostics, not the live provider.

## Explicit athlete selection and source evidence

The owner reported that changing **only** the athlete ID from `0` to their
explicit ID made the same curl request succeed, with the key and curl options
unchanged. This isolates the path change in that owner-run comparison; it does
not verify the entire probe or establish a Cloudflare/User-Agent cause. The
provider's browser-like User-Agent advice remains separate evidence.

With the correct explicit ID and key, the owner subsequently reported HTTP 200
from curl for `activities.csv`, then HTTP 200 for the athlete/profile request
with curl's default User-Agent, while the probe's profile request returned
HTTP 403. The suggested curl comparison using Python's User-Agent has **not been
run or reported**; curl versus urllib also differs in ways other than that header.
These observations do not prove User-Agent filtering or Cloudflare as the cause.

Following the provider's browser-like User-Agent guidance and the owner's request,
the probe now explicitly sends the minimal, stable `User-Agent: Mozilla/5.0`
instead of urllib's default. No elaborate browser/version identity, cookies,
challenge bypass, proxies, alternate hosts or retries are added. This change is
not guaranteed to resolve the 403. The owner later reported the full probe
succeeded with this change (owner-reported; raw output and field/metric values
not agent-verified; root cause of the earlier 403 unproven). Synthetic tests verify the explicit outgoing header and unchanged
safety/authentication controls, not provider acceptance.

The owner-supplied public `openapi-spec.json` and the public
[OpenAPI document](https://intervals.icu/api/v1/docs) were reread before this
change. Athlete path IDs and the returned `WithSportSettings.id` are strings,
without a numeric pattern or length constraint. The provider website documents
`0` as the authenticated-athlete alias; why that alias failed in the owner's
comparison remains unknown. The probe requires an explicit ID and verifies the
returned profile ID matches the original input exactly before any activity or
wellness listing. Canonically equivalent Unicode spellings, case changes and
numeric-looking equivalents are not treated as the same identity. The same exact
binding applies to present, non-null activity athlete IDs. Missing, non-string or
mismatched profile IDs stop the run with a static error, no IDs and no partial report.

Wellness GET is documented as `/api/v1/athlete/{id}/wellness{ext}`, with a required
string `ext` and `.csv` described as an alternative format. This does not
establish the exact JSON extension convention. The probe retains its existing
unsuffixed `/wellness` request with `Accept: application/json`; no `.json` suffix
is invented. Live wellness field/metric behavior is not agent-verified; the owner reported only overall probe success.

## What it requests

At most **eight GETs**, directly to the fixed HTTPS host `intervals.icu`:

1. `/api/v1/athlete/{id}`: verify the requested athlete's identity and resolve
   their valid IANA timezone in memory. No timezone override or local-machine
   fallback.
2. `/api/v1/athlete/{id}/activities`: the last seven **complete calendar days** in
   that athlete timezone, excluding today; then two overlapping subwindows
   within those same days. Each requests `limit=1000`.
3. The same full activity window with `limit=1`, for a limit observation.
4. `/api/v1/athlete/{id}/wellness`: the full window and the same two subwindows.

All eight requests use the same validated athlete ID encoded as one path segment;
the fixed HTTPS host and query parameters are unchanged. Each request explicitly
sends `User-Agent: Mozilla/5.0`, with the existing Basic authorization and
`Accept: application/json` headers unchanged.

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

The entered athlete ID, raw responses and authorization exist only in process
memory. The hidden prompts and lack of raw files reduce exposure; they are
**not secure memory erasure** or protection against a compromised terminal/machine. The script deliberately reads
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
HTTP boundary; real connections are forbidden. Regression coverage exercises all
eight CLI URLs with synthetic alphanumeric, Unicode, punctuation and zero-prefixed
IDs, checks exact encoded paths, unchanged queries, the explicit `Mozilla/5.0`
User-Agent and unchanged authorization/Accept headers, refuses unsafe input before
the key prompt/opener, and rejects Unicode-normalized identity mismatches without
leaking values or partial reports. They are included in the existing
API pytest suite. No disposable or real-use database is needed for these tests.
Passing mocks verifies local safety/summary behavior, not the live provider.
