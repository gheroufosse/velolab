# Roadmap

Ordered build sequence. Each stage is built together, explained as it goes, and
ends in something runnable. Stages are not started in parallel.

---

## Stage 0 — Repository foundations

- [x] Decisions recorded
- [x] Working agreements recorded
- [ ] Monorepo skeleton: `apps/api`, `apps/web`, `infra/`
- [ ] Python project via `uv`, with `ruff` and `ty` configured
- [ ] CI running lint, type check and tests on pull requests

**Learning focus.** GitHub pull request flow, GitHub Actions, monorepo layout.

---

## Stage 1 — Backend foundation

- [ ] FastAPI application skeleton with health endpoint
- [ ] Postgres running in Docker
- [ ] SQLAlchemy 2 models: `users`, `user_integrations`
- [ ] Alembic migrations wired up
- [ ] Registration and login
- [ ] JWT issuance, refresh endpoint, protected route dependency

**Done when.** A user can log in and call a protected endpoint, verified by
integration tests.

**Learning focus.** SQLAlchemy 2 typed models, migrations, JWT mechanics,
FastAPI dependency injection.

---

## Stage 2 — intervals.icu client and sync

- [ ] Typed API client with API-key auth
- [ ] `activities` and `wellness` models
- [ ] One-shot sync endpoint, 24-month backfill
- [ ] Incremental and idempotent re-sync
- [ ] Daily throttle plus manual force
- [ ] Sync state and error reporting
- [ ] Integration tests against recorded responses

**Done when.** Two consecutive syncs produce no duplicates and no lost updates.

**Learning focus.** Idempotency, incremental sync, rate limits, external API
failure handling.

---

## Stage 3 — Frontend foundation

- [ ] Vite + React + TypeScript app
- [ ] Tailwind and base design tokens
- [ ] TanStack Query client, typed API layer
- [ ] Login screen, in-memory access token, silent refresh
- [ ] Protected routing

**Done when.** Logging in from the browser reaches a protected page and
survives a refresh.

**Learning focus.** React components and state, TypeScript in practice, query
caching, auth on the client.

---

## Stage 4 — Dashboard

- [ ] Weekly volume summary with previous-week comparison
- [ ] Fitness/fatigue/form chart (CTL, ATL, TSB)
- [ ] Ramp rate indicator
- [ ] Resting HR and weight trends, gaps and estimates marked
- [ ] Empty and loading states

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
- [ ] Security headers and basic rate limiting
- [ ] Reachable from another device on the local network

**Learning focus.** Docker networking, service discovery, reverse proxy
routing, internal versus exposed ports.

---

## Stage 8 — VPS deployment

- [ ] Real domain
- [ ] TLS certificates
- [ ] Secrets handling in production
- [ ] Backups
- [ ] Deployment procedure

**Learning focus.** TLS, production hardening, operating a deployed service.

---

## Later

- Background sync worker, once sync latency justifies it
- Invited friends: signup flow, per-user integrations, sharing rules
- Strava and Garmin connectors with OAuth token storage
- Saved views, custom zones, notes and tags
- Ride comparison tools
- Custom CTL/ATL time constants computed from stored per-activity load
- Kubernetes on kind or k3d, once Compose is second nature
