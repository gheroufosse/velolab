"""Opt-in PostgreSQL tests use only explicitly configured disposable test storage."""

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import URL, Engine

from .postgres_support import database_url_from_env, disposable_database

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture
def test_postgres_url() -> URL:
    if os.environ.get("VELOLAB_TEST_DATABASE") != "1":
        pytest.skip(
            "set VELOLAB_TEST_DATABASE=1 and TEST_POSTGRES_* for the dedicated test cluster"
        )
    try:
        return database_url_from_env(os.environ)
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)


@pytest.fixture
def isolated_database(test_postgres_url: URL) -> Generator[tuple[Config, Engine]]:
    with disposable_database(test_postgres_url) as engine, engine.connect() as connection:
        config = Config(str(ALEMBIC_INI))
        config.attributes["connection"] = connection
        yield config, engine
