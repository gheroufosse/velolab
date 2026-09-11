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

**Revisit when.** The review dashboard is in daily use and feels complete.

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
changes before pushing.

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

## Open questions

- Offline or PWA support — wanted eventually?
- Which two or three customisation features genuinely change how rides get
  reviewed, beyond raw presentation quality.
- Whether custom CTL/ATL time constants are worth building once the read-only
  curves are in daily use.
