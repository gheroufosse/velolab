# Decisions

Architecture decision record for velolab. Each entry states the decision, why
it was taken, what was rejected, and what would make us revisit it.

Newest decisions go at the bottom. Decisions are amended, not deleted — a
superseded decision keeps its entry and gains a `Superseded by` line.

---

## Context

Personal cycling training and review app. The data already exists in
intervals.icu; the value added here is presentation, speed and
customisability, not new metrics.

Primary user: the repository owner. Expected to expand to a small number of
invited friends. Not a public product.

Learning is an explicit goal alongside daily usefulness: backend, frontend,
auth, Docker networking, reverse proxies, and later Kubernetes.

---

## ADR-001 — First job is review, not planning

**Decision.** v1 answers "how is my training going?". Workout planning,
calendar scheduling and coaching advice are out of scope.

**Why.** A narrow first version reaches daily use faster, and daily use is what
tells us which features matter.

**Rejected.** Plan-and-review in one release — doubles scope before any
feedback exists.

**Longer-term direction.** Add a contextual chat window for personal coaching
and training-status discussion. This is not an MVP requirement. Build the
read-only dashboard first; later coaching should reuse its backend data and
metric definitions rather than introduce a second interpretation of training.
No chat infrastructure, model provider or planning engine is selected now.

**Revisit when.** The review dashboard is in daily use and the first useful
coaching tasks can be specified.

---

## ADR-002 — intervals.icu is the only data source in v1

**Decision.** All training data comes from the intervals.icu API, authenticated
with an API key. Strava and Garmin are deferred.

**Why.** The key is already available and the account already holds complete
history. intervals.icu uses an API key rather than OAuth, which removes an
entire auth flow from v1.

**Consequence.** Integration storage is designed per user and per provider from
day one, so adding OAuth-based providers later is additive.

**Rejected.** Starting with Strava — OAuth complexity with no extra value,
since intervals.icu already ingests Strava data.

---

## ADR-003 — Single user now, multi-user schema from day one

**Decision.** Every domain table carries `user_id`. Auth and integration
credentials are per user. Public signup is not built.

**Why.** Retrofitting ownership onto a single-user schema is a rewrite.
Carrying `user_id` from the start costs almost nothing.

**Rejected.** Hardcoding a single athlete — cheap now, expensive later.

---

## ADR-004 — Python backend, TypeScript frontend

**Decision.** FastAPI backend with a separate React + TypeScript frontend,
rather than server-rendered templates with HTMX.

**Why.** The owner is experienced in Python and new to frontend work; learning
the browser side is an explicit goal. The dashboard is chart-heavy and
interactive, which favours a real frontend application.

**Rejected.** Django (explicitly excluded by the owner). HTMX-first — less
frontend learning, weaker fit for interactive charts.

---

## ADR-005 — Stack selection

**Decision.**

| Layer | Choice | Note |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2 + Pydantic v2 | typed, modern, good DX |
| Migrations | Alembic | standard |
| Database | Postgres | JSONB used heavily |
| Frontend | React + TypeScript + Vite | fast iteration |
| Data fetching | TanStack Query | caching, refetch, request state |
| Styling | Tailwind | quick iteration on a dense UI |
| Charts | Apache ECharts | see ADR-009 |
| Reverse proxy | nginx | networking fundamentals are a learning goal |
| Containers | Docker Compose | Kubernetes deliberately deferred |

---

## ADR-006 — JWT auth, access token in memory, refresh token in cookie

**Decision.** JWT-based authentication. The access token is short-lived and
held in JavaScript memory only. The refresh token is stored in an httpOnly,
SameSite cookie and exchanged at a dedicated refresh endpoint.

**Why.** The owner wants to learn JWT mechanics. This layout keeps that
learning while avoiding the well-known weakness of the naive approach.

**Security note.** Tokens placed in `localStorage` are readable by any script
that runs on the page, so a single cross-site-scripting flaw leaks a valid
session. An httpOnly cookie is not reachable from JavaScript, which removes
that class of theft. Losing an in-memory access token on refresh is acceptable
because the refresh endpoint restores the session silently.

**Rejected.** Server-side cookie sessions — simpler and arguably better here,
but teaches less of what the owner wants to learn.

**Revisit when.** A mobile client or a second origin appears, or session
revocation becomes a real requirement.

---

## ADR-007 — Sync strategy: 24-month backfill, throttled to once per day

**Decision.** First sync pulls 24 months of activities and wellness records.
Afterwards, the app triggers a sync on launch but skips it if the last
successful sync is less than 24 hours old. A manual "sync now" action bypasses
the throttle.

**Why.** Full career history is unnecessary for reviewing current form, and an
unthrottled sync-on-launch would hammer the API during development.

**Consequence.** Sync state (`last_sync_at`, status, error) is persisted per
user and per provider.

**Guardrail.** Sync correctness — incremental updates, idempotency, rate
limits, changed and deleted activities — is treated as first-class work, not an
afterthought. It is harder than the UI.

**Revisit when.** Longer history is wanted for year-over-year comparison.

---

## ADR-008 — Store per-activity load, display intervals.icu curves

**Decision.** Persist each activity's training load, and also store the daily
CTL/ATL series returned by intervals.icu. Display the intervals.icu values in
v1. Compute TSB ourselves as `CTL - ATL`, since it is not returned.

**Why.** Reading their values guarantees the dashboard agrees with the source
app and avoids subtle modelling bugs on day one. Persisting per-activity load
keeps the door open to computing custom curves — different time constants,
what-if scenarios — as a deliberate later feature.

**Rejected.** Computing everything immediately — a debugging burden before
anything is on screen. Reading only — would block custom metrics later.

---

## ADR-009 — Apache ECharts over Recharts

**Decision.** Apache ECharts for all charts.

**Why.** Ride detail views render per-second power and heart-rate streams,
which reach tens of thousands of points. ECharts renders to canvas and ships
zoom and brush interactions; Recharts renders SVG and degrades badly at that
scale.

**Cost.** Larger bundle and a less React-idiomatic API than Recharts.

---

## ADR-010 — Recovery panel built on data that actually exists

**Decision.** v1 surfaces training-derived fatigue (ATL, TSB, ramp rate) plus
resting heart rate and weight trends. No HRV panel, no sleep panel.

**Why.** The account was inspected before designing. CTL, ATL and ramp rate are
present every day. Resting HR appears on roughly a third of days with noisy
values. Weight is sparse and sometimes flagged as estimated rather than
measured. HRV and sleep are entirely absent.

**Consequence.** Trend charts must visibly mark missing days and
estimated values rather than interpolating over them. Wellness records are
stored as JSONB so that connecting Garmin later can add HRV and sleep without a
schema migration.

**Revisit when.** A device starts supplying HRV or sleep data.

---

## ADR-011 — Data model leans on JSONB

**Decision.** Activities keep a small set of real columns for frequently
queried fields — user, source, external id, start time, core metrics — with the
full provider payload in JSONB. Streams and interval breakdowns start in JSONB
and are fetched lazily, then cached per activity.

**Why.** Provider payloads are rich and evolve. Over-normalising early locks in
guesses about which fields matter. Columns get extracted from JSONB once real
queries prove the need.

**Rejected.** Fully normalised schema up front. Storing every per-second stream
eagerly — large, slow to sync, rarely read.

---

## ADR-012 — Infrastructure sequence: Compose, then LAN, then VPS

**Amended by ADR-023:** TLS and rate limiting are required at the first
non-loopback authentication exposure, including LAN use; not deferred to VPS.

**Decision.** Everything runs in Docker Compose behind nginx. The app is first
laptop-only over plain HTTP, then reachable on the local network, then deployed
to a VPS with a real domain and real certificates. Kubernetes (kind or k3d) is
a later exercise, never a starting point.

**Why.** Each step introduces one new set of concepts — container networking,
then exposure and host access, then TLS and hardening. Self-signed certificates
on day one add browser friction without teaching much.

**Rejected.** Kubernetes first. TLS before the app works.

---

## ADR-013 — No background worker until proven necessary

**Decision.** Sync runs as a one-shot endpoint. No Redis, no ARQ, no scheduler
in the initial build.

**Why.** A once-daily sync for one user does not need a queue. Adding Redis and
a worker early means three more moving parts to debug before anything works.

**Revisit when.** Sync takes long enough to block a request, or multiple users
sync concurrently.

---

## ADR-014 — Development workflow: hybrid local, Compose to verify

**Decision.** Daily development runs Postgres and nginx in Docker while FastAPI
and Vite run natively with hot reload. The full Compose stack is run to verify
changes before pushing. ADR-019 clarifies that the app database is real-use,
not a third development/QA environment; integration tests use a separate
disposable PostgreSQL instance. Commands touching real-use/production require
explicit user approval, including full-stack verification.

**Why.** Native processes give the fastest edit-reload cycle; the Compose run
catches the container and proxy problems that hybrid mode hides.

---

## ADR-015 — Python tooling

**Decision.** `uv` for dependency and environment management, `ruff` for
linting and formatting, `ty` for type checking, `pytest` for both unit and
integration tests.

**Why.** Fast, modern, minimal configuration. Integration tests are explicitly
in scope because sync correctness cannot be verified by unit tests alone.

---

## ADR-016 — Visual direction: dark-first, dense, charts lead

**Decision.** Dark-first interface, high information density with clear
structure, one accent colour reserved for training-state signals such as form.
Chrome recedes; charts and numbers lead.

**Why.** The stated product goal is better visuals than intervals.icu for the
same data. The direction is a starting anchor and will be refined against real
screens rather than argued in the abstract.

---

## ADR-017 — Build together, one piece at a time

**Decision.** The assistant scaffolds infrastructure and boilerplate. The owner
writes domain logic and React components. The assistant reviews and explains.
Features are built one at a time, not generated wholesale.

**Why.** Learning is a primary goal of the project. Generated code that is not
understood defeats the purpose.

---

## ADR-018 — Private owner provisioning and credential policy

**Decision.** Stage 1 creates accounts through a local, interactive provisioning
CLI, not an HTTP registration endpoint. There is no public signup. Email is
validated with `email-validator`, stripped of surrounding whitespace, and
lowercased across the *whole address* as an intentional identity policy; an
existing normalized address is rejected, never overwritten. Passwords are
entered with a hidden prompt and confirmation, never a command-line password
argument; accept 10–128 characters without trimming, composition requirements,
or restrictions on spaces or Unicode. Hash passwords using Argon2id via
`argon2-cffi`, never plaintext. The access-token lifetime for the later JWT
slice is **10 minutes**. Refresh-token lifetime and rotation are not decided
here; ADR-006 still governs token storage.

**Why.** Single-user now and invite-only later does not justify a registration
API. Explicit identity and password rules keep initial provisioning consistent
with later login. A short-lived access token supports ADR-006 without placing
it in persistent browser storage.

**Revisit when.** Invited-friend signup is built or a second origin/client
requires revisiting credential and token policies.

---

## ADR-019 — Real-use database and disposable PostgreSQL tests

**Decision.** Keep exactly two data environments: real-use/production and a
separate, disposable **real PostgreSQL** test instance. No third development/QA
copy. The test instance has its own Compose project, test-only credentials,
isolated disposable storage (tmpfs), and a distinct loopback-only host port
(default `127.0.0.1:5435`); it never shares app credentials or production volumes.
Tests require explicit `TEST_POSTGRES_HOST`, `TEST_POSTGRES_PORT`,
`TEST_POSTGRES_USER`, `TEST_POSTGRES_PASSWORD` and `TEST_POSTGRES_DB`, with no
fallback to app `POSTGRES_*` settings or root `.env`. Test startup must also
avoid Compose's implicit root `.env` loading. See `infra/test-db/README.md`.

**Data policy.** PostgreSQL integration tests use generated fixtures in unique
databases created and dropped inside the test instance. An optional sanitized
dump is a manual-only aid, reviewed and imported by the owner, never an automated
test input. Raw production backups are excluded from test inputs, agent context and
the repository; real credentials never enter fixtures or sanitized dumps.

**Safety boundary.** Every command against real-use/production requires
explicit user approval. No enforced agent sandbox is introduced deliberately:
a host agent remains technically capable of accessing the real-use instance.
Separate settings, credentials, storage and targeting guards protect against
accidental misuse, not deliberate host access. Configuration and documentation
alone are not evidence of live verification; a disposable-instance run must be
reported before claiming that verification.

**Why.** Real PostgreSQL preserves migration, constraint and concurrency
semantics without exposing daily-use data to destructive tests. Persistence,
migration and concurrency verification require real PostgreSQL; fast
supplemental unit/HTTP tests may use SQLite but do not provide that evidence.
Two instances provide sufficient separation with less operational overhead.

**Rejected.** Disposable databases on the real-use instance, app-credential
fallbacks, SQLite as a substitute for PostgreSQL integration verification, a
third QA environment, and an enforced agent sandbox for this slice.

**Revisit when.** Untrusted automation or multiple operators require an
actually enforced access boundary.

---

## ADR-020 — Login and access-token-only authentication slice

**Decision.** Implement JSON `POST /auth/login` (email/password) and bearer-
protected `GET /auth/me`, returning only user `id` and `email`. Login follows
ADR-018 normalization and Argon2id verification, with a generic credential
failure. Successful login returns `access_token`, `token_type: "bearer"` and
`expires_in: 600`; successful auth responses use `Cache-Control: no-store`.

Access tokens use **HS256 only**, with exactly **10 minutes** between integer
`iat` and `exp` timestamps. Required claims are `sub` (an existing user's UUID),
`iat`, `exp`, `token_use: "access"`, `iss: "velolab-api"` and
`aud: "velolab-api"`. Verification checks signature, expiry, issuance time,
algorithm, purpose, issuer, audience and the exact lifetime before resolving
the user. `AUTH_JWT_SECRET` is server-only, must be random and at least **32
UTF-8 bytes**, and has no usable default. Missing/short signing material disables
auth with HTTP 503, without preventing health checks or private provisioning.

**Scope.** No refresh endpoint/cookie, logout/revocation or UI in this slice.
ADR-006 remains the target access-in-memory/refresh-in-httpOnly-cookie design;
this is a partial implementation, not a replacement. Rate limiting and TLS are
later work required before exposing authentication beyond loopback.

**Why.** A small, owner-authorized domain implementation lets us learn and
review login, JWT validation and FastAPI dependencies before adding session
renewal. ADR-017's learning-first goal still applies: explain the implementation,
not just ship it. Auth security review reported no material findings;
a fresh disposable PostgreSQL 17 run verified login/protected identity and
invalid credentials. This completes the access-only slice, not Stage 1;
refresh remains unimplemented.

**Rejected.** Public registration, form-based OAuth2 login, configurable token
lifetimes/algorithms and implementing refresh/logout/UI in the same slice.

**Revisit when.** The refresh/session slice starts or authentication is prepared
for network exposure.

---

## ADR-021 — Rotating, session-scoped refresh cookies

**Decision.** Continue ADR-006/020 without changing access JWTs or the login/me
JSON contract. Login creates a per-user database session with an absolute
seven-day expiry and sends a cryptographically random opaque refresh token as a
host-only HttpOnly SameSite=Strict cookie. Only its SHA-256 digest is persisted;
the raw token never appears in models, persistence, logs or error messages.
Every successful refresh atomically spends the previous digest and issues a new
cookie plus the same 600-second access JWT. Spent digests remain until their
session expires: replay revokes that session (including innocent concurrent
reuse), not other sessions. Unknown tokens cannot revoke sessions. PostgreSQL
locks the parent session row during refresh/logout so rotations, replays and
logout serialize. Logout revokes only the cookie's session and clears the cookie;
previously issued access JWTs remain valid until their normal expiry. Expired
session rows and token digests can be pruned in later maintenance work, never
before expiry where replay detection matters.

**Browser boundary.** Login, refresh and logout require the exact configured
trusted `Origin` and `X-Velolab-CSRF: 1`; there is no credentialed CORS. No
trusted origin (or an invalid one) disables auth with HTTP 503, without blocking
health or private provisioning. Cookie `Secure` is chosen exclusively from the
configured origin: HTTPS is secure; HTTP is permitted only for an explicitly
configured loopback origin. Do not select this flag from request/forwarded
headers. Cookie path is explicitly configured for the public API prefix
(`/auth` direct, `/api/auth` behind the future proxy). TLS and rate limiting
remain prerequisites for exposing auth beyond loopback. No browser storage/UI,
global logout, access-token blacklist or rate limiting is part of this slice.

**Why.** Hashed opaque values reduce database-leak utility; a stable parent
row is the serialization point even as token rows rotate. An absolute expiry
limits stolen-session lifetime and replay revocation bounds the damage of
reuse. The explicit origin and non-simple custom header protect cookie-writing
requests against cross-site form requests, without opening CORS. These controls
do not replace TLS or rate limiting.

**Revisit when.** A second origin/client, production proxy or session management
UI is needed. ADR-020 records the prior access-only slice as historical evidence,
not the current completed refresh scope.

---

## ADR-022 — Shared training data contract before sync implementation

**Decision.** Stage 2 establishes a backend-owned training data contract shared
by dashboard APIs and eventual coaching. React formats and visualises values;
it does not independently define training metrics. A future model explains
backend results rather than becoming the source of numerical truth.

Before implementing sync, document the provider mapping and these semantics:

- **Identity and provenance.** Domain records retain `user_id`, provider and
  external identity. Enforce appropriate database uniqueness for activities
  and daily wellness records. Preserve relevant raw provider payloads and
  measured/estimated flags; exclude credentials from domain payloads.
- **Time and units.** Define the athlete's IANA timezone, activity instant versus
  provider-local date handling, week boundary, units and daily metric alignment.
  Verify CTL/ATL/TSB dates against source records before claiming source parity.
  Weekly comparisons explicitly distinguish complete weeks from week-to-date
  comparisons over equivalent elapsed periods.
- **Missingness.** Missing, explicit null, zero and estimated values are not
  interchangeable. Define how partial provider responses update stored fields;
  do not erase known values merely because a response omitted a field. Preserve
  genuine upstream clearing of values when the API provides that distinction.
- **Freshness.** Persist last successful sync separately from attempts/errors.
  Expose data freshness and incomplete-backfill state to consumers. Cached data
  remains readable after sync failure but must not appear newly refreshed.
- **Corrections.** Incremental sync must reconcile edits/deletions of older
  activities and revised daily metrics, not only append new records. Establish
  the reconciliation window and historical rescan policy from verified provider
  behaviour. Never infer deletion from an incomplete or failed listing.
- **Failure and concurrency.** Define transaction/checkpoint boundaries so retries
  resume safely and failed work cannot advance the success marker. Serialize or
  coalesce overlapping sync requests for the same integration; uniqueness alone
  does not prevent stale concurrent updates.

ADR-007's 24-month backfill and daily automatic throttle remain the MVP policy;
manual sync bypasses the throttle, not concurrency safeguards. Show last sync
and the manual action prominently. Revisit refresh frequency if post-ride use
shows stale data is impairing usefulness; no scheduler or worker is added now.

Training-load models are not direct measurements of physiological readiness.
Dashboard labels and later coaching must distinguish modelled load, reported
wellbeing and unavailable evidence. Sparse recovery data must not become an
unqualified readiness recommendation.

**Why.** Clear ownership, source evidence and correction semantics prevent
costly data repair and divergent dashboard/coach calculations. Future goals,
events and conversations can be added without designing their schemas now.

**Verification.** Stage 2 covers reruns, historical corrections, explicit field
clearing, partial failure/retry, concurrent sync, timezone/week boundaries and
missing-versus-zero behaviour. Recorded responses must be sanitized; tests use
the dedicated disposable database, never real-use credentials or storage.

**Revisit when.** A second provider or concrete coaching feature requires
additional context. Change the contract deliberately, not through UI formulas.

---

## ADR-023 — Integration secrets and first network exposure gates

**Decision.** Before storing a real intervals.icu API key, implement authenticated
encryption at rest using a maintained cryptography library. Keep encryption
key material outside the database and repository, separate from JWT signing
material. Document key backup and rotation/re-encryption procedures. Do not
silently fall back to plaintext when configuration is missing or invalid.
The existing `encrypted_api_key` text column is scaffolding, not evidence that
encryption is implemented. Keys and decrypted values never enter browser
responses, logs, fixtures or domain payloads. Encryption protects a database-only
leak; it does not protect against compromise of the running backend.

TLS and basic authentication rate limiting are prerequisites for the first
non-loopback exposure, including LAN use. Loopback-only HTTP remains permitted
for local development under ADR-021. Stage 7 must establish browser-trusted TLS
for the chosen LAN access method, or remain loopback-only until it can. Stage 8
adds the public domain and VPS certificate/deployment operations, not the first
transport protection.

Stage 3 must account for ADR-021's deliberate refresh-replay behaviour. Coordinate
refreshes within and across browser tabs; do not blindly retry an ambiguously
completed refresh request. Verify simultaneous requests, tab startup, logout,
expiry and lost refresh responses. If session recovery is unsafe or impossible,
return to login clearly rather than loop or repeatedly replay a spent cookie.
This is a frontend integration requirement, not a change to the replay policy.

**Why.** A single-user app still holds sensitive credentials and health-related
data. These gates close storage and exposure gaps without adding public signup,
new infrastructure services or an alternative authentication system.

**Revisit when.** Deployment access method, secret management or browser session
requirements change. Any relaxation of ADR-021 requires a separate decision.

---

## Open questions

- Offline or PWA support — wanted eventually?
- Which two or three customisation features genuinely change how rides get
  reviewed, beyond raw presentation quality.
- Whether custom CTL/ATL time constants are worth building once the read-only
  curves are in daily use.
