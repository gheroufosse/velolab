# Roadmap

Ordered build sequence. Each stage is built together, explained as it goes, and
ends in something runnable. Stages are not started in parallel.

The first useful product milestone is Stage 4: a single-user, read-only dashboard
used for the daily training check. Activity browsing/detail are follow-on work,
not prerequisites for validating that dashboard. Contextual coaching chat is a
longer-term goal, not an MVP requirement (ADR-001/022).

---

## Stage 0 — Repository foundations

- [x] Decisions recorded
- [x] Working agreements recorded
- [x] Monorepo skeleton: `apps/api`, `apps/web`, `infra/`
- [x] Python project via `uv`, with `ruff` and `ty` configured
- [x] CI running lint, type check and tests on pull requests

**Learning focus.** GitHub pull request flow, GitHub Actions, monorepo layout.

---

## Stage 1 — Backend foundation

- [x] FastAPI application skeleton with health endpoint
- [x] Postgres running in Docker
- [x] SQLAlchemy 2 models: `users`, `user_integrations`
- [x] Alembic migrations wired up
- [x] Private owner provisioning (no public registration) and JSON login
  implemented and verified on disposable PostgreSQL — ADR-018/020
- [x] JWT issuance, refresh endpoint, protected route dependency
  - [x] Access-token issuance (10-minute HS256), dependency and `GET /auth/me`,
    verified on disposable PostgreSQL
  - [x] Refresh endpoint/cookie, rotation/replay policy and session-specific logout
    implemented and verified on disposable PostgreSQL (ADR-021)

**Current status.** Stage 1 backend implementation is complete and locally
verified. A previous dedicated PostgreSQL 17 full check passed lint,
formatting, types and **99 tests, zero skips**, including migration
upgrade/downgrade, persistence, refresh/replay and concurrent refresh/logout
behavior. Zero fixture databases remained and the disposable project's resources
were removed. Independent Codex security/spec review found no material issues.
Login validation continues to redact credentials under mounted/proxy path
prefixes. The owner approved publication after green GitHub CI. Auth PR
[#13](https://github.com/gheroufosse/velolab/pull/13) was squash-merged as
`8c5268977878d75e0786a2b7d97552cf515640d2` after final-head CI
[run 37060700250](https://github.com/gheroufosse/velolab/actions/runs/37060700250)
passed: API lint/format/types, 99 PostgreSQL-enabled tests with zero skips,
disposable cleanup and stable `ci`; web was intentionally path-filtered. Main
CI [run 37060921206](https://github.com/gheroufosse/velolab/actions/runs/37060921206)
also passed the same API checks, 99 tests and cleanup. The fresh local
dedicated PostgreSQL 17 full check passed 99 tests, zero skips (see
`docs/session-handoff.md`). CI and local checks are not deployment sign-off.
No real-use migration/account provisioning was performed. CI opts into
disposable PostgreSQL tests and rejects failed, cancelled or unexpectedly
skipped required jobs. Next build stage is Stage 2 sync; UI remains Stage 3.

**Done when.** A user can log in and call a protected endpoint, verified by
integration tests, and the remaining Stage 1 refresh work is complete.

**Learning focus.** SQLAlchemy 2 typed models, migrations, JWT mechanics,
FastAPI dependency injection.

---

## Stage 2 — intervals.icu client and sync

- [ ] Finalize the [provider/data contract draft](intervals-data-contract.md)
  before sync implementation: identity, timezone, units, week comparisons,
  missing/null/zero semantics and metric date alignment (ADR-022). Public
  schema research is recorded; evidence blockers remain open.
- [x] Implement and test API-key encryption; document key backup and rotation
  (ADR-023; [implementation and operational gates](integration-key-encryption.md)).
  Crypto/settings verified locally, **not deployed**: key delivery and backup/
  restore approval remain required before real-key storage; no credential
  enrollment, database re-encryption or sync workflow is implemented.
- [ ] Evidence-independent slices (ADR-024), in order, each a separate PR:
  1. [x] (implemented, synthetic-tested; follow-ups: set `httpx`/`httpcore`
     loggers to WARNING when app logging is configured, since INFO logs URLs
     with athlete ID/dates; duplicate IDs/dates rejected by slice 3) Typed
     intervals.icu client (httpx, no DB): safety policy, bounded 429
     retries, redacted errors, missing-vs-null preserved, truncation flagged;
     MockTransport synthetic tests
  2. [x] `activities` and `wellness_days` models + migration: composite owner
     FKs, uniqueness, JSONB payload as truth with re-derivable projections;
     disposable PostgreSQL migration/constraint tests
  3. [x] Idempotent non-destructive upsert layer: omission preserves, explicit
     null policy configurable (default preserve), no delete path; owner/athlete
     binding and duplicate-batch rejection, atomic merged-payload projections,
     no-op reruns, rollback and concurrent partial updates verified on disposable
     PostgreSQL. [Merge/bookkeeping policy](intervals-data-contract.md#5-evidence-independent-upsert-layer-adr-024-slice-3)
  4. [ ] Sync state with fenced lease and throttle decision; failures never
     advance `last_success_at`; concurrency tests on disposable PostgreSQL

  Unverified provider behaviour stays in named, configurable settings (see
  ADR-024). Orchestration, backfill, reconciliation and real-key enrollment
  wait for contract evidence and ADR-023 approvals.
- [ ] One-shot sync endpoint, 24-month backfill
- [ ] Incremental and idempotent re-sync, including historical edits/deletions and
  revised daily metrics; explicit reconciliation/rescan policy
- [ ] Daily throttle plus manual force; overlapping requests serialized or coalesced
- [ ] Sync state and error reporting, successful-sync timestamp and incomplete
  backfill state; failed attempts do not mark cached data fresh
- [ ] Integration tests against recorded responses

**Done when.** Reruns produce no duplicates or lost updates. Tests also verify
historical corrections/deletions, missing versus cleared fields, partial failure
and retry, concurrent requests and timezone/week-boundary semantics. Incomplete
responses cannot trigger deletions or incorrectly advance success markers.
Numerical/date mappings are checked against sanitized provider records, and
API keys cannot leak through serialization or error reporting.

**Learning focus.** Idempotency, incremental sync, rate limits, external API
failure handling.

---

## Stage 3 — Frontend foundation

- [ ] Vite + React + TypeScript app
- [ ] Tailwind and base design tokens
- [ ] TanStack Query client, typed API layer
- [ ] Login screen, in-memory access token, silent refresh
- [ ] Coordinate refresh across concurrent requests and browser tabs under ADR-021;
  handle ambiguous network failures without blind token replay (ADR-023)
- [ ] Protected routing and clear login recovery after expiry/revocation

**Done when.** Logging in from the browser reaches a protected page and
survives a refresh. Browser checks cover concurrent requests/tab startup,
logout, expiry and lost refresh responses without refresh loops.

**Learning focus.** React components and state, TypeScript in practice, query
caching, auth on the client.

---

## Stage 4 — Dashboard

- [ ] Define primary device, daily-check tasks and dashboard visual hierarchy;
  review composition using real data before expanding screens
- [ ] Weekly volume summary with explicitly labelled equivalent-period comparison
- [ ] Fitness/fatigue/form chart (CTL, ATL, TSB)
- [ ] Ramp rate indicator
- [ ] Resting HR and weight trends, gaps and estimates marked
- [ ] Last successful sync, manual sync and stale/partial/error states
- [ ] Distinguish modelled training load from physiological readiness; no implied
  certainty from missing recovery data
- [ ] Empty and loading states; responsive layout, keyboard access and chart
  meaning that does not depend on colour alone

**Done when.** It replaces opening intervals.icu for the daily check.

**Learning focus.** ECharts, time-series presentation, honest handling of
missing data.

---

## Stage 5 — Activity list

- [ ] Paginated ride list with core metrics
- [ ] Filters: date range, activity type, duration, load
- [ ] Sorting
- [ ] Fast scanning layout

**Learning focus.** Server-side pagination and filtering, query parameter
state, list performance.

---

## Stage 6 — Activity detail

- [ ] Lazy stream fetch and cache
- [ ] Power and heart-rate charts with zoom
- [ ] Interval breakdown
- [ ] Zone distribution

**Learning focus.** Large dataset rendering, downsampling, lazy loading.

---

## Stage 7 — Containerised and LAN-reachable

- [ ] Multi-stage Dockerfiles for api and web
- [ ] Compose stack: api, db, nginx
- [ ] nginx serves the built frontend and proxies `/api`
- [ ] Health and readiness checks
- [ ] Security headers and basic authentication rate limiting
- [ ] Browser-trusted TLS before exposing authentication beyond loopback (ADR-023)
- [ ] Reachable from another device on the local network only after TLS/rate-limit
  gates are met; otherwise remain loopback-only

**Learning focus.** Docker networking, service discovery, reverse proxy
routing, internal versus exposed ports.

---

## Stage 8 — VPS deployment

- [ ] Real domain
- [ ] Public-domain TLS certificates and renewal; preserve Stage 7's TLS gate
- [ ] Secrets handling in production
- [ ] Backups
- [ ] Deployment procedure

**Learning focus.** TLS, production hardening, operating a deployed service.

---

## Later

- Contextual chat for training-status discussion and personal coaching. First
  define useful tasks, advisory versus write permissions, athlete context,
  data/provider privacy, conversation retention, grounded-answer checks and cost
  limits. Reuse backend metrics and provenance; no chat infrastructure required
  for the dashboard MVP.
- Background sync worker, once sync latency justifies it
- Invited friends: signup flow, per-user integrations, sharing rules
- Strava and Garmin connectors with OAuth token storage
- Saved views, custom zones, notes and tags
- Ride comparison tools
- Custom CTL/ATL time constants computed from stored per-activity load
- Kubernetes on kind or k3d, once Compose is second nature
