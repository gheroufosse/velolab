#!/usr/bin/env bash
set -euo pipefail

: "${TEST_POSTGRES_PASSWORD:?A separate test runner password is required}"
if [[ "$TEST_POSTGRES_PASSWORD" == "$POSTGRES_PASSWORD" ]]; then
  echo 'Test runner and bootstrap passwords must differ.' >&2
  exit 1
fi

# psql quotes the password as a SQL literal; never interpolate it into SQL.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=runner_password="$TEST_POSTGRES_PASSWORD" <<'SQL'
CREATE ROLE velolab_test_runner WITH LOGIN CREATEDB NOSUPERUSER NOCREATEROLE
    NOREPLICATION NOBYPASSRLS PASSWORD :'runner_password';
ALTER DATABASE velolab_test_admin OWNER TO velolab_test_runner;
REVOKE ALL ON DATABASE velolab_test_admin FROM PUBLIC;
COMMENT ON DATABASE velolab_test_admin IS 'velolab-disposable-test-postgres-v1';
SQL
