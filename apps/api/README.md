# velolab-api

FastAPI backend for velolab. See repo root README and `docs/` for context.

## Development

From `apps/api`:

```
uv sync
uv run velolab-api          # dev server with reload, http://localhost:8000
```

Run `./scripts/check.sh` from the repository root for API lint, formatting
check, type check and pytest (or call it by absolute path from anywhere).
It does not start services; pytest skips the database integration test unless
`VELOLAB_TEST_DATABASE=1` is set.

## Database scaffold (Stage 1)

From `apps/api`, settings read the repository-root `.env` regardless of the
working directory. `POSTGRES_USER`, `POSTGRES_DB`, and `POSTGRES_PASSWORD` are
required **only when a database connection is requested**; `/health` does not
require database configuration. `POSTGRES_HOST` defaults to `127.0.0.1` and
`POSTGRES_PORT` to `5434` for native API development against the Compose
Postgres port. Environment variables override the `.env` file. The URL uses
synchronous `postgresql+psycopg` and is shared by the API and Alembic; do not
put credentials in `alembic.ini` or log a URL with its password visible.

`get_session()` supplies one session per dependency use and closes it on exit,
rolling back any unfinished transaction. Write handlers must explicitly call
`session.commit()`; nothing commits automatically. The engine is lazy and
cached. `User` and `UserIntegration` are registered on `Base.metadata` for
Alembic. The initial revision creates `users` and `user_integrations` with
UUID primary keys, unique email, unique `(user_id, provider)`, a cascading
foreign key, and database-generated timezone-aware creation timestamps.
The `encrypted_api_key` column stores text; encryption itself, email
normalization, and auth are **not implemented yet**. Keep sync state for Stage 2.
Do not use `Base.metadata.create_all()` in the app: schema changes go through
Alembic.

With the local Postgres from `infra/README.md` running, use
`uv run alembic upgrade head` to apply revisions to a **known empty or
previously migrated** local database. Inspect an existing database before
migrating it; never reset a volume to make a migration work. For the constraint
integration test, run `VELOLAB_TEST_DATABASE=1 ./scripts/check.sh` from the
repository root (or `env VELOLAB_TEST_DATABASE=1 uv run pytest` from `apps/api`).
The opt-in test needs permission to create/drop a disposable database on that
Postgres server; it does not alter tables in the configured development DB.
