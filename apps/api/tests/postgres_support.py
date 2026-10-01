"""Dedicated test-cluster configuration and disposable database lifecycle.

This module never imports application settings or loads dotenv files. Its
configuration is explicitly passed from the process environment by conftest.
"""

import re
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import URL, Connection, Engine, create_engine, text

TEST_ROLE = "velolab_test_runner"
TEST_ADMIN_DATABASE = "velolab_test_admin"
TEST_CLUSTER_MARKER = "velolab-disposable-test-postgres-v1"
DATABASE_NAME = re.compile(r"velolab_test_[0-9a-f]{32}\Z")


def database_url_from_env(environ: Mapping[str, str]) -> URL:
    values = {}
    for field in ("HOST", "PORT", "USER", "PASSWORD", "DB"):
        name = f"TEST_POSTGRES_{field}"
        value = environ.get(name)
        if value is None or not value.strip() or "\0" in value:
            raise ValueError(f"{name} must be explicitly set for database integration tests")
        values[field] = value

    if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", values["HOST"]):
        raise ValueError("TEST_POSTGRES_HOST must be a hostname or IP address")
    if not re.fullmatch(r"[0-9]+", values["PORT"]) or not 1 <= int(values["PORT"]) <= 65535:
        raise ValueError("TEST_POSTGRES_PORT must be an integer between 1 and 65535")
    if values["USER"] != TEST_ROLE or values["DB"] != TEST_ADMIN_DATABASE:
        raise ValueError(
            "TEST_POSTGRES_USER/DB must identify the dedicated test role/admin database"
        )
    return URL.create(
        "postgresql+psycopg",
        username=values["USER"],
        password=values["PASSWORD"],
        host=values["HOST"],
        port=int(values["PORT"]),
        database=values["DB"],
        query={"connect_timeout": "5"},
    )


def verify_test_cluster(connection: Connection) -> None:
    identity = (
        connection.execute(
            text(
                "SELECT current_database() AS database, current_user AS role, "
                "current_setting('server_version_num')::int AS version, "
                "r.rolcreatedb, r.rolsuper, r.rolcreaterole, r.rolreplication, r.rolbypassrls, "
                "shobj_description(d.oid, 'pg_database') AS marker "
                "FROM pg_roles r JOIN pg_database d ON d.datname = current_database() "
                "WHERE r.rolname = current_user"
            )
        )
        .mappings()
        .one()
    )
    if (
        identity["database"] != TEST_ADMIN_DATABASE
        or identity["role"] != TEST_ROLE
        or identity["marker"] != TEST_CLUSTER_MARKER
        or not 170000 <= identity["version"] < 180000
        or not identity["rolcreatedb"]
        or any(
            identity[field]
            for field in ("rolsuper", "rolcreaterole", "rolreplication", "rolbypassrls")
        )
    ):
        raise ValueError(
            "refusing database tests: target is not the dedicated PostgreSQL 17 cluster"
        )


def database_statement(operation: str, database_name: str) -> str:
    # Only these UUID-generated identifiers may appear in administrative DDL.
    if not DATABASE_NAME.fullmatch(database_name):
        raise ValueError("refusing unsafe disposable database identifier")
    if operation == "create":
        return f'CREATE DATABASE "{database_name}" TEMPLATE template0'
    if operation == "drop":
        return f'DROP DATABASE "{database_name}" WITH (FORCE)'
    raise ValueError("unsupported disposable database operation")


@contextmanager
def disposable_database(admin_url: URL) -> Generator[Engine]:
    database_name = f"velolab_test_{uuid4().hex}"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            verify_test_cluster(connection)
            connection.execute(text(database_statement("create", database_name)))
        try:
            engine = create_engine(admin_url.set(database=database_name))
            try:
                yield engine
            finally:
                engine.dispose()
        finally:
            with admin_engine.connect() as connection:
                connection.execute(text(database_statement("drop", database_name)))
    finally:
        admin_engine.dispose()
