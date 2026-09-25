# infra

Stage 1 runs only Postgres in Docker. nginx and the full application stack are
not configured yet (see [Stage 7](../docs/roadmap.md#stage-7--containerised-and-lan-reachable)).

- `compose/compose.yaml` — local Postgres service with a named persistent volume
- `nginx/` — reserved for the later reverse proxy

## Start Postgres

Run these commands **from the repository root**. Docker Desktop (or another
running Docker daemon) and the Docker Compose plugin are required. The example
credentials are placeholders; create a local `.env` with a random password
without printing it to your terminal (run once; this refuses to overwrite an
existing `.env`):

```sh
python3 - <<'PY'
import os
import secrets

fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as env:
    env.write('POSTGRES_USER=velolab\nPOSTGRES_DB=velolab\n'
              'POSTGRES_PASSWORD=' + secrets.token_urlsafe(48) + '\n')
PY
docker compose --env-file .env -f infra/compose/compose.yaml config --quiet
docker compose --env-file .env -f infra/compose/compose.yaml up -d --wait
```

[`.env.example`](../.env.example) documents the required variables. The root
`.env` is ignored by git. Keep it private: `docker compose config` *without*
`--quiet` expands and prints the password. Postgres is available only on this
machine at `127.0.0.1:5434` (container port `5432`); ports `5432` and `5433`
are not used by this project.

Verify readiness and execute a real query (the latter checks more than the
healthcheck does):

```sh
docker compose --env-file .env -f infra/compose/compose.yaml exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose --env-file .env -f infra/compose/compose.yaml exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1"'
```

The query prints `1`. To stop and remove the container **without deleting** the
named `velolab_postgres_data` volume:

```sh
docker compose --env-file .env -f infra/compose/compose.yaml down
```

Postgres uses the `.env` credentials when initializing an **empty** data volume.
Keep the same `.env` when restarting with an existing volume; editing it alone
does not change existing database credentials. Do not use `down -v` unless you
intend to delete your local database.
