"""Safety seam: explicit test configuration and disposable database lifecycle.

Failure inventory (isolated cases are needed because no service may be started
before review): absent/partial test config must fail before a connection, even
when application credentials or a dotenv file exist; malformed host/port and
wrong role/database must fail without exposing secrets; wrong server identity
or privilege flags must prohibit DDL; unsafe identifiers must never reach SQL;
body/engine failures must drop only the generated database and dispose engines.
A failed CREATE must not trigger DROP of an existing database. External database
connections are faked only for these fault paths. Real opt-in coverage checks
independent databases and cleanup after failure; provisioning retains its real
concurrency test. Time, auth, retries and application data are out of scope.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import URL, create_engine, text

from velolab_api import settings

from . import postgres_support
from .postgres_support import (
    TEST_CLUSTER_MARKER,
    database_statement,
    database_url_from_env,
    disposable_database,
)


@pytest.fixture
def explicit_test_env() -> dict[str, str]:
    return {
        "TEST_POSTGRES_HOST": "test-db",
        "TEST_POSTGRES_PORT": "5432",
        "TEST_POSTGRES_USER": "velolab_test_runner",
        "TEST_POSTGRES_DB": "velolab_test_admin",
        "TEST_POSTGRES_PASSWORD": "p@ss:/word%",
    }


def test_test_configuration_never_falls_back_to_application_settings(
    explicit_test_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_env = tmp_path / ".env"
    root_env.write_text(
        "POSTGRES_USER=real_user\nPOSTGRES_PASSWORD=real_secret\nPOSTGRES_DB=real\n"
    )
    monkeypatch.setattr(settings, "ENV_FILE", root_env)

    def forbidden() -> None:
        pytest.fail("test configuration consulted application settings")

    monkeypatch.setattr(settings, "get_settings", forbidden)
    application_env = {"POSTGRES_USER": "real_user", "POSTGRES_PASSWORD": "real_secret"}
    with pytest.raises(ValueError, match="TEST_POSTGRES_HOST"):
        database_url_from_env(application_env)
    url = database_url_from_env(application_env | explicit_test_env)
    assert (url.host, url.port, url.username, url.database) == (
        "test-db",
        5432,
        "velolab_test_runner",
        "velolab_test_admin",
    )
    assert url.password == "p@ss:/word%"
    assert "p@ss:/word%" not in str(url)
    for field in explicit_test_env:
        partial = explicit_test_env.copy()
        del partial[field]
        with pytest.raises(ValueError, match=field):
            database_url_from_env(application_env | partial)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("PORT", "65536"),
        ("DB", "training"),
        ("PASSWORD", "\0secret"),
    ],
)
def test_invalid_test_configuration_fails_closed_without_leaking_secrets(
    explicit_test_env: dict[str, str], field: str, value: str
) -> None:
    explicit_test_env[f"TEST_POSTGRES_{field}"] = value
    with pytest.raises(ValueError) as error:
        database_url_from_env(explicit_test_env)
    assert "secret" not in str(error.value)
    assert "p@ss:/word%" not in str(error.value)


def test_integration_opt_in_without_test_configuration_is_an_error_not_a_skip() -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("TEST_POSTGRES_")}
    env.update(VELOLAB_TEST_DATABASE="1", POSTGRES_USER="real_user", POSTGRES_DB="real")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_initial_migration.py::"
            "test_initial_migration_matches_models_and_enforces_ownership",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "TEST_POSTGRES_HOST must be explicitly set" in result.stdout
    assert "1 error" in result.stdout


@pytest.mark.parametrize(
    "name",
    ["postgres", "velolab_test_admin", "velolab_test_short", 'x"; DROP DATABASE postgres;--'],
)
def test_administrative_ddl_rejects_non_disposable_identifiers(name: str) -> None:
    for operation in ("create", "drop"):
        with pytest.raises(ValueError, match="unsafe"):
            database_statement(operation, name)


@pytest.fixture
def cluster_identity() -> dict[str, object]:
    return {
        "database": "velolab_test_admin",
        "role": "velolab_test_runner",
        "version": 170000,
        "marker": TEST_CLUSTER_MARKER,
        "rolcreatedb": True,
        "rolsuper": False,
        "rolcreaterole": False,
        "rolreplication": False,
        "rolbypassrls": False,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [("marker", None), ("rolsuper", True)],
)
def test_wrong_cluster_identity_or_privileges_prevent_database_creation(
    explicit_test_env: dict[str, str],
    cluster_identity: dict[str, object],
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster_identity[field] = value
    admin = MagicMock()
    connection = admin.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one.return_value = cluster_identity
    monkeypatch.setattr(postgres_support, "create_engine", lambda *args, **kwargs: admin)
    with (
        pytest.raises(ValueError, match="dedicated PostgreSQL 17"),
        disposable_database(database_url_from_env(explicit_test_env)),
    ):
        pytest.fail("wrong target was allowed")
    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert all(not sql.startswith(("CREATE", "DROP")) for sql in statements)
    admin.dispose.assert_called_once()


def test_failure_cleans_up_only_created_database_and_releases_resources(
    explicit_test_env: dict[str, str],
    cluster_identity: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin, engine = MagicMock(), MagicMock()
    connection = admin.connect.return_value.__enter__.return_value
    result = MagicMock()
    result.mappings.return_value.one.return_value = cluster_identity
    connection.execute.side_effect = [result, None, None]
    factory = MagicMock(side_effect=[admin, engine])
    monkeypatch.setattr(postgres_support, "create_engine", factory)
    with (
        pytest.raises(RuntimeError, match="test body failed"),
        disposable_database(database_url_from_env(explicit_test_env)),
    ):
        raise RuntimeError("test body failed")
    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    creates = [sql for sql in statements if sql.startswith("CREATE")]
    drops = [sql for sql in statements if sql.startswith("DROP")]
    assert len(creates) == 1
    name = creates[0].split('"')[1]
    assert name.startswith("velolab_test_") and len(name) == 45
    assert drops == [f'DROP DATABASE "{name}" WITH (FORCE)']
    engine.dispose.assert_called_once()
    admin.dispose.assert_called_once()


def test_independent_databases_and_cleanup_after_body_failure(test_postgres_url: URL) -> None:
    names = []
    with disposable_database(test_postgres_url) as first:
        names.append(first.url.database)
        with (
            pytest.raises(RuntimeError, match="test body failed"),
            disposable_database(test_postgres_url) as second,
        ):
            names.append(second.url.database)
            assert names[0] != names[1]
            with second.connect() as connection:
                assert (
                    connection.execute(text("SELECT current_database()")).scalar_one() == names[1]
                )
            raise RuntimeError("test body failed")
        with first.connect() as connection:
            assert connection.execute(text("SELECT current_database()")).scalar_one() == names[0]
    admin = create_engine(test_postgres_url)
    try:
        with admin.connect() as connection:
            for name in names:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                        {"name": name},
                    ).scalar_one()
                    == 0
                )
    finally:
        admin.dispose()
