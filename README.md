# velolab

Personal cycling training dashboard. Reads training data from
[intervals.icu](https://intervals.icu) and presents it with a faster, denser and
better-looking UI than the source web app.

Built as a learning project: FastAPI backend, React + TypeScript frontend,
Docker Compose infrastructure, growing from a laptop-only app to a
LAN-reachable one and eventually a VPS deployment.

## Status

Stage 0 complete. Stage 1 backend auth/session implementation is locally
verified; publication and fresh GitHub CI remain pending. Frontend and sync are
not implemented yet. See [docs/roadmap.md](docs/roadmap.md) for current status
and verification boundaries.

## Product direction

**First useful milestone: a great single-user, read-only dashboard.** Weekly
training volume, fitness/fatigue/form curves and honest recovery-data trends,
with clear visual hierarchy, data freshness and missing/estimated values shown.
Training-load models describe training history; they do not establish medical
or physiological readiness.

Activity lists and detailed power/HR analysis follow once the dashboard proves
useful for the daily check. They are not prerequisites for that first milestone.

**Later: contextual chat for personal coaching and training-status discussion.**
It will reuse the dashboard's backend metrics and source evidence. Coaching,
workout planning and chat infrastructure are not part of the dashboard MVP;
their scope and privacy requirements will be decided before implementation.

Single user first, with per-user data ownership from day one. Friend onboarding
and additional data providers remain deferred.

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI, SQLAlchemy 2, Pydantic v2 |
| Database | Postgres + Alembic (JSONB-leaning schema) |
| Auth | JWT: in-memory access token, httpOnly refresh cookie |
| Frontend | React, TypeScript, Vite, TanStack Query, Tailwind |
| Charts | Apache ECharts |
| Python tooling | uv, ruff, ty, pytest |
| Infra | Docker Compose, nginx reverse proxy |

Full reasoning in [docs/decisions.md](docs/decisions.md).

## Repository layout

```
apps/
  api/          FastAPI backend
  web/          React + TypeScript frontend
infra/
  nginx/        reverse proxy config
  compose/      Docker Compose files
docs/           decisions, roadmap, working agreements
```

## Development

API runs locally with Python 3.14 and `uv`:

```sh
cd apps/api
uv run velolab-api
```

`GET http://127.0.0.1:8000/health` returns `{"status":"ok"}`.

Postgres runs separately in Docker. From the repository root, follow the
[Postgres setup and verification instructions](infra/README.md#start-postgres)
to create a private `.env` and start it:

```sh
docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait
```

It listens only on `127.0.0.1:5434` and retains data in a named Docker volume.
The API now has database/session plumbing, `User` and `UserIntegration` models,
and an initial Alembic migration; `/health` still works without database configuration.
See [the API database scaffold notes](apps/api/README.md#database-scaffold-stage-1)
for session and migration conventions. nginx and the frontend are not set up;
the full Compose stack is planned for Stage 7.

## Secrets

The local root `.env` holds the Postgres password and is git-ignored;
[`.env.example`](.env.example) contains placeholders only. Never commit
credentials. The intervals.icu API key will also remain server-side when that
integration is built; it must never reach the browser. Encryption at rest must
be implemented before storing a real integration key (ADR-023); the existing
column name alone does not provide encryption. TLS and authentication rate
limiting are required before login is exposed beyond loopback, including LAN use.

## Contributing

See [docs/workflow.md](docs/workflow.md) for branching, commits, pull requests
and CI conventions.
