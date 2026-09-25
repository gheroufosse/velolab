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

Postgres runs separately in Docker. From the repository root, follow the
[Postgres setup and verification instructions](infra/README.md#start-postgres)
to create a private `.env` and start it:

```sh
docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait
```

It listens only on `127.0.0.1:5434` and retains data in a named Docker volume.
The API is **not connected** to Postgres yet. nginx and the frontend are not
set up; the full Compose stack is planned for Stage 7.

## Secrets

The local root `.env` holds the Postgres password and is git-ignored;
[`.env.example`](.env.example) contains placeholders only. Never commit
credentials. The intervals.icu API key will also remain server-side when that
integration is built; it must never reach the browser.

## Contributing

See [docs/workflow.md](docs/workflow.md) for branching, commits, pull requests
and CI conventions.
