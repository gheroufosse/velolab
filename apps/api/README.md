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
It does not start services; pytest skips the Postgres integration tests unless
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
The `encrypted_api_key` column stores text; encryption itself is **not
implemented yet**. HTTP access-token auth is described below. Keep sync state
for Stage 2.
Do not use `Base.metadata.create_all()` in the app: schema changes go through
Alembic.

Real-use/production commands require explicit owner approval, including
migrations and account provisioning. With the local Postgres from
`infra/README.md` running, use
`uv run alembic upgrade head` to apply revisions to a **known empty or
previously migrated** local database. Inspect an existing database before
migrating it; never reset a volume to make a migration work. For the migration
and provisioning integration tests, use only the separate disposable PostgreSQL
instance described in [`infra/test-db/README.md`](../../infra/test-db/README.md).
Set `VELOLAB_TEST_DATABASE=1` and all five explicit connection variables:
`TEST_POSTGRES_HOST`, `TEST_POSTGRES_PORT`, `TEST_POSTGRES_USER`,
`TEST_POSTGRES_PASSWORD`, and `TEST_POSTGRES_DB`. Tests never fall back to app
credentials or root `.env`, and never use the real-use database.

## Private account provisioning (Stage 1 slice)

`velolab-provision-user` is a local, interactive CLI with no HTTP route. It
accepts `--email`, prompts for a password twice without echoing it, and refuses
non-interactive input, insecure getpass fallback, or mismatched confirmation.
Passwords must never be supplied as CLI arguments, put in shell history,
printed, or committed. Rejected arguments and runtime errors are reported
without echoing input values, hashes, SQL parameters or database credentials.

`normalize_email` strips surrounding whitespace, validates with
`email-validator` without DNS/deliverability checks, then lowercases the
**whole normalized address**, including the local part. The library's default
Unicode support is retained. This is a deliberate identity policy, not a claim
that every email provider treats local-part case alike; validation does not
prove that a mailbox exists.

`provision_user` accepts 10–128 characters (Python Unicode code points) without
trimming or composition rules. Spaces and Unicode are preserved exactly. It
hashes with Argon2id using `argon2-cffi`'s `PasswordHasher` defaults and commits
one new user through a dedicated session. Postgres's `uq_users_email` constraint
arbitrates duplicates, including concurrent inserts; rejection rolls back and
never overwrites an account. Unrelated integrity errors are rolled back and
propagated rather than mislabeled as email duplicates.

Before creating a real account, review this slice, confirm the target database,
and apply migrations as above. Then run from `apps/api` in an interactive terminal:

```
uv run velolab-provision-user --email rider@example.com
```

Success prints `User provisioned.` only after the commit returns. An unexpected
failure reports that success was not confirmed: a connection lost during commit
can leave the outcome uncertain. A subsequent attempt cannot overwrite an
existing account.

Default tests exercise normalization, password boundaries, real hashing,
portable persistence in in-memory SQLite, injected driver failures, and the
CLI-to-domain path. SQLite/injected errors do **not** prove Postgres behavior.
Opt-in Postgres tests reuse the disposable database fixture in `tests/conftest.py`
and Alembic migrations to check committed persistence, validation/no-write,
duplicate preservation, race behavior and unrelated constraints. Run them with
the explicit `TEST_POSTGRES_*` configuration and `VELOLAB_TEST_DATABASE=1` from
[`infra/test-db/README.md`](../../infra/test-db/README.md); never test provisioning
against the real-use database.

There is no registration endpoint. Private provisioning and access-only login
are implemented; Stage 1 is not complete (refresh remains unimplemented).

## Login and protected identity (Stage 1 access-only slice)

- `POST /auth/login` accepts JSON with `email` and `password`, not an OAuth2
  password form. Email follows the provisioning normalization policy; passwords
  are preserved exactly and verified against Argon2id hashes. Invalid credentials
  return the same HTTP 401 message without revealing account existence.
- Success returns `access_token`, `token_type: "bearer"` and `expires_in: 600`.
  Treat the token as a secret: never log it, commit it, or place it in browser
  persistent storage. ADR-006 targets in-memory access-token storage.
- `GET /auth/me` requires `Authorization: Bearer <access-token>` and returns
  only `id` and `email`. Missing/invalid tokens or a missing user return HTTP
  401 with `WWW-Authenticate: Bearer`. Both successful endpoints send
  `Cache-Control: no-store`.
- Invalid login bodies return HTTP 422 without echoing credentials. Redaction
  identifies the matched login endpoint rather than comparing URL strings, so
  it also works when a reverse proxy or mounted app adds an `/api` prefix.
  Other endpoints retain FastAPI's standard validation response.

Configure `AUTH_JWT_SECRET` on the **server only**, through the environment or
ignored root `.env`. Use a randomly generated secret of at least **32 UTF-8
bytes**; `.env.example` documents generation without supplying a usable default.
Never expose it through frontend build variables, responses or logs. Missing or
short signing material makes auth return HTTP 503; `/health` and private
provisioning do not need a signing key.

Tokens use HS256 only and require `sub` (an existing user's UUID), integer `iat`
and `exp` exactly 600 seconds apart, `token_use: "access"`,
`iss: "velolab-api"` and `aud: "velolab-api"`. Verification checks the signature,
algorithm, expiration, issuance time, purpose, issuer, audience and exact lifetime
before resolving the user. See ADR-020.

No refresh cookie/endpoint, logout/revocation or UI is included. A token expires
rather than being renewed or explicitly revoked. Refresh lifetime/rotation and
logout policy remain undecided; ADR-006 remains the target refresh design.
Rate limiting and TLS are required before exposing authentication beyond
loopback. A successful local check is not deployment/security sign-off.

Fast HTTP tests cover the contract with real hashing and SQLite fixtures; they
are supplemental, not evidence of PostgreSQL persistence or concurrency.
Opt-in tests also verify login/protected identity and invalid credentials
against migrated disposable PostgreSQL. The API CI job uses that same dedicated
Compose definition and opts into PostgreSQL tests, rather than silently skipping
them. Use only the dedicated instance and explicit test settings described above
for integration checks. See `docs/session-handoff.md` at the repository root for
the latest local verification results; a local pass is not a GitHub Actions run.
