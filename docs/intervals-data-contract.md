# Intervals.icu training data contract — DRAFT

**Status:** Stage 2 research draft, 2026-10-02 UTC. This is not a verified sync
specification or permission to store a real key. ADR-007/008/010/011/022/023
and the [Stage 2 roadmap](roadmap.md) govern scope. Public documentation
establishes candidate fields, **not** availability or behavior on this account.
No personal API requests, real payloads or generated fixtures were used here.

## 1. Provider facts (public documentation, not product guarantees)

- `GET /api/v1/athlete/{id}/activities` lists by local `oldest` (required)
  and `newest` (optional) in descending date order; it supports `limit` and
  `fields` (which also excludes null values). `GET /api/v1/activity/{id}`
  returns detail; both listing and detail can return empty Strava stubs.
  Bulk lookup by ids ignores missing activities and may return stubs. None of
  these statements promises a complete change cursor, pagination or deletion
  feed. [OpenAPI: Activities](https://intervals.icu/api/v1/docs)
- Candidate activity mapping: `id` is the Intervals activity identity;
  `icu_athlete_id`, `source`, `external_id` (identity on the originating
  service), `start_date` (UTC instant), `start_date_local`, `timezone`,
  `icu_training_load`, `icu_sync_date` and `analyzed` are documented fields.
  Activity fields are generally metric; times are seconds and speeds m/s.
  These timestamps are **not** documented as a complete changed-since cursor.
  [OpenAPI: Activity schema](https://intervals.icu/api/v1/docs),
  [provider field notes](https://forum.intervals.icu/t/25781/16)
- `GET /api/v1/athlete/{id}/wellness` lists records by local ISO date;
  `newest` is explicitly inclusive, while `oldest` has no explicit
  inclusivity statement. Candidate fields: `id` (local ISO day), `ctl`,
  `atl`, `rampRate`, `weight`, `restingHR`, `updated`, `tempWeight`,
  `tempRestingHR`. Both list endpoints' `fields` options suppress nulls.
  The temp flags indicate carried-over, not captured weight/HR; absence of
  a flag is not proof of a new measurement. Provider examples say wellness
  units are metric, but do not settle every field's semantics.
  [OpenAPI: Wellness](https://intervals.icu/api/v1/docs),
  [integration cookbook](https://forum.intervals.icu/t/intervals-icu-integration-cookbook/80090),
  [carry-over explanation](https://forum.intervals.icu/t/112975/2)
- Athlete data exposes `timezone`. Personal keys use Basic auth with username
  `API_KEY`; athlete path `0` resolves to the authenticated athlete. Public
  guidance reserves personal keys for one's own data and recommends OAuth for
  multi-person apps. API-key callers have 5,000 requests/day and 2,500 per
  rolling 15 minutes; there is also a 10/s/IP limit. Rate-limit responses
  are 429; `Retry-After` is documented for the window limits, but the IP
  limit has no rate-limit headers (do not assume a retry header always exists).
  [OpenAPI: Athlete and security](https://intervals.icu/api/v1/docs),
  [API access and limits](https://forum.intervals.icu/t/intervals-icu-api/609),
  [integration cookbook](https://forum.intervals.icu/t/intervals-icu-integration-cookbook/80090)

## 2. Product choices (implementation constraints, not provider facts)

- **Ownership/identity:** Every domain row has `user_id`. Key an activity by
  `(user_id, owned integration, Intervals activity id)`, *not* upstream
  `external_id`; key wellness by `(user_id, owned integration, local date)`.
  Enforce that integration owner equals row owner in the persistence/API path.
  Resolve and bind the provider athlete id to the integration before sync;
  replacement credentials must not silently switch its namespace. A changed
  athlete requires an explicit rebind/reconciliation decision, not an upsert.
- **Provenance/missingness:** Retain relevant activity and wellness provider
  payloads and field-presence/provenance, not credentials, request headers or
  indiscriminate athlete/account responses. Keep missing, explicit null,
  zero and carried-over/estimated values distinguishable. An omitted field
  must not erase a known value. Conversely, there is **no** permanent
  never-clear rule: genuine upstream clearing needs an evidenced path; until
  verified, do not claim partial-field reconciliation complete.
- **Time/metrics:** Store activity UTC instant and provider-local date/time
  separately; key wellness by its supplied local date. Use the athlete's IANA
  timezone for Monday-start local calendar weeks. Compare complete weeks to
  complete weeks, and week-to-date only to equivalent elapsed periods in the
  athlete's zone (not assumed 168-hour weeks across DST). Preserve source
  CTL/ATL by their wellness day; derive TSB = CTL − ATL **only when both are
  present and date-aligned**. Do not claim source numerical/date parity yet.
  A timezone change may regroup activities or alter dates/keys; policy is
  blocked below. Modelled load is not measured physiological readiness.
- **Backfill/freshness:** First sync covers 24 calendar months, with fixed
  boundaries for that run and bounded overlapping local-date windows. Retry
  windows idempotently; atomic window writes and per-integration checkpoint
  advance together. A separate successful-sync marker advances only after
  *all required* coverage is verified. Record attempts, errors and incomplete
  coverage independently; cached data stays readable but visibly stale or
  incomplete after failure. Measure the automatic 24-hour throttle from the
  last *complete successful sync*, not an attempt or window checkpoint.
  Serialize/coalesce per-integration syncs; manual force bypasses the throttle,
  never the lock. No scheduler/worker.
- **Corrections:** Overlap alone does not prove completeness. A truncated
  listing, tied timestamps, moved activities or Strava stubs cannot justify
  deletion; do not infer absence from an incomplete list, destructively
  reconcile, or silently omit uncertain records from totals. Surface
  uncertainty until absence can be evidenced. Historical rescan cadence and
  reconciliation window remain a product trade-off to decide **after** a
  feasible completeness strategy is verified, not a provider-imposed number.
- **Client/secrets:** Finite timeouts, bounded retries, and conservative
  fallback backoff for 429 without usable `Retry-After`; a long wait must be
  safely abandonable and reported as failure, not success. Before storing a
  real key, implement maintained authenticated encryption at rest with keys
  outside DB/repo, separate from JWT keys; missing or invalid encryption
  configuration fails closed.
  Document key backup and rotation/re-encryption. Never send key to browser,
  logs, fixtures or domain payloads (ADR-023). Personal-key scope is
  single-user only; inviting friends requires revisiting ADR-002 for OAuth.

## 3. Evidence blockers (must resolve before claiming contract complete)

1. **Range/completeness:** Confirm activity and wellness `oldest`/`newest`
   boundary behavior; test window edges, limits, truncation, tied timestamps
   and whether any reliable continuation/complete-enumeration method exists.
   Without it neither full backfill nor absence/deletion detection is proven.
2. **Corrections:** Verify edits moved across windows, removed/deleted
   activities, inaccessible/missing ids and Strava stubs; establish evidence
   for absence and a bounded historical rescan policy. Bulk lookup silently
   ignoring missing ids is not a deletion signal.
3. **Field updates:** Establish whether unfiltered listing/detail preserves
   explicit null versus omission for cleared activity/wellness fields, and
   whether `fields` filtering changes it; define a safe clear/update path.
4. **Parity/time:** Verify account-specific field availability and exact units
   for selected metrics, CTL/ATL/ramp-rate dates and values against sanitized
   source evidence, including zero/absent/carried-over values and week/DST
   edges. Define timezone-change regrouping/rekeying policy before migration.
   Public schemas alone are not account evidence; ADR-010's historical account
   observations do not establish this API contract.

## 4. Acceptance gates for subsequent Stage 2 implementation

- Record authorized, **sanitized** provider evidence and the conclusions for
  blockers above before finalizing mappings/reconciliation. Do not describe
  fictitious responses as recorded fixtures or put personal data/secrets in
  the repo. If a blocker has no safe answer, scope implementation accordingly
  and keep that requirement open; do not declare sync correct by assumption.
- Through the supported API/read path, verify per-user ownership, stable
  athlete binding, reruns without duplicates/lost updates, moved/deleted
  activity handling with complete evidence, explicit clears versus omissions,
  null/zero/carried-over display, timezone/DST/week comparisons and date/metric
  parity. Incomplete or uncertain listings must not silently change totals or
  advance the full-success marker; failure/retry must preserve checkpoint
  integrity and expose stale/incomplete state.
- Use the dedicated disposable PostgreSQL instance for migration, uniqueness,
  partial-failure and concurrent-sync checks (ADR-019); no app credentials,
  root `.env`, real-use storage, or production commands without separate
  approval. Verify secret serialization/error redaction, encrypted-key
  fail-closed behavior and backup/rotation procedure before any real key is
  stored. A local or CI test is not proof of live provider parity.
