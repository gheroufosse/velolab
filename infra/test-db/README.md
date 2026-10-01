# Disposable test PostgreSQL

Exactly two data environments exist: the real-use app database and this test
cluster. This is not a development/QA copy. It contains fixture-generated data
only: no real accounts, API credentials, production volumes or dumps.

PostgreSQL 17 runs in a separate Compose project, with **tmpfs** database storage.
Stopping/recreating the container destroys all test data. Its only published
port is `127.0.0.1:5435`; set `TEST_POSTGRES_PORT` to another unused port if needed.
The Compose service name is `test-db`, on its separate `test-only` network.
It is not Docker `internal` mode: that suppresses host port publication. Only
loopback is published; the test database is not exposed to the LAN.

## Human-reviewed startup and checks

Review `compose.yaml` and `init.sh` before executing. From the repository root,
replace the two placeholders with **different test-only secrets**, not app
credentials. `env` syntax works in fish, bash and zsh. These commands never load
the root `.env` (`--env-file /dev/null` disables Compose's implicit env file).
Avoid pasting actual secrets into shared logs or committing them.

```sh
env TEST_POSTGRES_BOOTSTRAP_PASSWORD='<bootstrap-test-secret>' \
    TEST_POSTGRES_PASSWORD='<runner-test-secret>' \
    docker compose --env-file /dev/null -f infra/test-db/compose.yaml up -d --wait

env VELOLAB_TEST_DATABASE=1 \
    TEST_POSTGRES_HOST=127.0.0.1 TEST_POSTGRES_PORT=5435 \
    TEST_POSTGRES_USER=velolab_test_runner TEST_POSTGRES_DB=velolab_test_admin \
    TEST_POSTGRES_PASSWORD='<runner-test-secret>' ./scripts/check.sh
```

For an overridden port, supply the same `TEST_POSTGRES_PORT` to **both** commands.
Container-based clients use `TEST_POSTGRES_HOST=test-db` and
`TEST_POSTGRES_PORT=5432` on this network instead.

Without `VELOLAB_TEST_DATABASE=1`, database integration tests are skipped.
With it, all five `TEST_POSTGRES_*` fields above are mandatory and validated;
application `POSTGRES_*` and root `.env` are never used for test connections.
Before database creation, tests check the maintenance database marker, role
identity/privileges and PostgreSQL major version. Each fixture creates a unique
`velolab_test_<uuid>` database, supplies its connection to Alembic, and drops it
on exit (including test failure), terminating leftover connections if needed.
Existing migration, persistence and provisioning-concurrency tests run there.

The runner has `CREATEDB`, but **not** superuser, role-creation, replication,
server-file access or RLS-bypass privileges. Bootstrap credentials initialize
the dedicated container only; do not pass them to tests. Mounted init scripts
contain no credentials. These guards prevent accidental app targeting, but are
not a security boundary against someone deliberately reconfiguring the server.

## Stop and discard everything

`down` needs interpolation values, but does not use them to authenticate:

```sh
env TEST_POSTGRES_BOOTSTRAP_PASSWORD=unused TEST_POSTGRES_PASSWORD=unused \
    docker compose --env-file /dev/null -f infra/test-db/compose.yaml down
```

The ordinary non-database checks remain `./scripts/check.sh`. No service is
started by that script.

## CI

The API job starts this same Compose service with `--env-file /dev/null` and a
run-specific test-only project name. It generates and masks distinct bootstrap
and runner passwords. Only the Compose startup receives the bootstrap password;
the API checks receive only the runner password and the other four explicit
`TEST_POSTGRES_*` connection fields. After PostgreSQL reports healthy, CI runs
`./scripts/check.sh` with `VELOLAB_TEST_DATABASE=1`. Startup has a timeout; cleanup
runs even if startup or checks fail and removes that exact disposable project. No app credentials, root `.env`, or
real-use database are involved. CI runs the API job when `apps/api/**`,
`scripts/check.sh`, `infra/test-db/**`, or the CI workflow changes.

## Previous local verification

Runtime verification completed after independent review: the full check passed
with 80 tests and no skips against a fresh PostgreSQL 17 test cluster, including
migration, committed persistence, provisioning concurrency, failure cleanup,
HTTP login/protected identity and invalid credentials on PostgreSQL.
The loopback binding, tmpfs storage and restricted runner identity were checked;
no temporary databases remained, and the test container/network were removed.
