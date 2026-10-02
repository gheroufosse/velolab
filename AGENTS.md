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
- **Recorded Stage 1 authorization.** The owner authorized assistant work on
  provisioning, database isolation, login and the ADR-021 refresh/session slice,
  including session-specific logout and autonomous Codex implementation, testing,
  fixes and review under the approved refresh policy. This is a narrow record of
  that Stage 1 scope, not standing permission for future domain work. Preserve
  the learning/explanation policy. Frontend/sync work and production commands
  are not authorized; publication (commits, pushes, PRs) and branch cleanup
  require fresh owner approval. This record does not independently establish
  Stage 1 completion.
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
- Keep exactly two database environments: real-use/production and a dedicated
  disposable PostgreSQL test instance (ADR-019). Tests require explicit
  `TEST_POSTGRES_*` settings; never fall back to root `.env` or app credentials.
- Commands against real-use/production require explicit user approval,
  including provisioning, migrations, resets, backups and full-stack checks.
  The host agent is not sandboxed; these are accidental-misuse safeguards,
  not an enforced security boundary.
- Commits follow Conventional Commits; PR titles must be valid Conventional
  Commit subject lines.

## Stack quick reference

Backend: FastAPI, SQLAlchemy 2, Pydantic v2, Alembic, Postgres.
Frontend: React, TypeScript, Vite, TanStack Query, Tailwind, ECharts.
Tooling: uv, ruff, ty, pytest.
Infra: Docker Compose, nginx.

Local development is hybrid: Postgres and nginx in Docker, FastAPI and Vite
native with hot reload. Verify with the full Compose stack before pushing,
with explicit user approval for commands touching real-use/production.

## Commands

Run `./scripts/check.sh` from the repository root (or by absolute path from
any directory) for API lint, formatting check, type check and tests. By
default, database integration tests are skipped; the script does not start
Postgres. To include them, follow `infra/test-db/README.md` to start the dedicated
disposable test instance and run `VELOLAB_TEST_DATABASE=1 ./scripts/check.sh`
with all five explicit `TEST_POSTGRES_*` connection settings. Never use root
`.env` as a test fallback. Fixtures create and drop unique databases inside
that test instance; no real-use database or storage is used.

## Testing policy

Optimize for confidence per effort, not coverage percentage. Run relevant
existing tests and type/lint checks. Add tests for changed behavior and credible
regressions: normally one regression test for a fix, or a happy path and the
important failure path for a small feature. Auth, permissions, data loss,
migrations and concurrency need critical failure-mode coverage. Prefer
observable behavior; avoid redundant cases, elaborate mocks and new test
infrastructure without approval. Documentation-only changes need no new tests.
Never weaken tests to pass. Report checks run, results and unverified risks;
do not claim live database verification without a real disposable-instance run.
