# velolab

Personal cycling training dashboard. Reads training data from
[intervals.icu](https://intervals.icu) and presents it with a faster, denser and
better-looking UI than the source web app.

Built as a learning project: FastAPI backend, React + TypeScript frontend,
Docker Compose infrastructure, growing from a laptop-only app to a
LAN-reachable one and eventually a VPS deployment.

## Status

Stage 0 complete; Stage 1 backend foundation in progress. FastAPI health endpoint
is available. See [docs/roadmap.md](docs/roadmap.md).

## What it does (target v1)

- **Dashboard** — weekly training volume, fitness/fatigue/form curves, recovery indicators.
- **Activity list** — fast, filterable, scannable ride history.
- **Activity detail** — power/HR streams, intervals, zone distribution.

Single user first, designed so adding a few friends later is incremental
rather than a rewrite.

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
Postgres, nginx and frontend are not set up yet. Planned local workflow (hybrid):

- Postgres and nginx run in Docker
- FastAPI and Vite run natively with hot reload
- Full Compose run used to verify before pushing

## Secrets

The intervals.icu API key is a secret. It lives in a local `.env` file or
Docker secrets, is never committed, and is never sent to the browser.

## Contributing

See [docs/workflow.md](docs/workflow.md) for branching, commits, pull requests
and CI conventions.
