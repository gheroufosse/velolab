# velolab — agent instructions

Read this before acting in this repository.

## What this project is

Personal cycling training dashboard. Reads data from intervals.icu, presents it
better than the source web app. Single user now, a few invited friends later.

Learning is a first-class goal, equal to shipping.

## Required reading

- `docs/decisions.md` — every architectural decision and its reasoning. Do not
  contradict an ADR; propose a new one instead.
- `docs/roadmap.md` — build order. Work the current stage, do not skip ahead.
- `docs/workflow.md` — branching, commits, PRs, CI.

## How to work here

- **Build together, one piece at a time.** Scaffold infrastructure and
  boilerplate; leave domain logic and React components to the owner unless
  asked otherwise. Review and explain rather than silently rewriting.
- **Explain frontend concepts.** The owner is an experienced Python engineer
  and new to JavaScript, TypeScript and React. Do not explain Python basics.
  Do explain browser, bundler, React state and TypeScript type-system concepts
  when they first appear.
- **No unrequested scope.** Do not add features, endpoints or screens that were
  not asked for.
- **Stay in stage.** If something belongs to a later roadmap stage, say so
  rather than building it.

## Hard rules

- The intervals.icu API key never reaches the browser and never enters a
  commit.
- Every domain table carries `user_id`.
- Schema changes ship with an Alembic migration.
- Sync must be idempotent: running it twice creates no duplicates.
- Never invent training data. If a wellness field is absent from the account,
  the UI shows the gap rather than interpolating it. HRV and sleep are
  currently absent — see ADR-010.
- Charts use Apache ECharts.
- Commits follow Conventional Commits; PR titles must be valid Conventional
  Commit subject lines.

## Stack quick reference

Backend: FastAPI, SQLAlchemy 2, Pydantic v2, Alembic, Postgres.
Frontend: React, TypeScript, Vite, TanStack Query, Tailwind, ECharts.
Tooling: uv, ruff, ty, pytest.
Infra: Docker Compose, nginx.

Local development is hybrid: Postgres and nginx in Docker, FastAPI and Vite
native with hot reload. Verify with the full Compose stack before pushing.

## Commands

Filled in as the project grows. Currently none — no application code exists
yet.
