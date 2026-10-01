"""Alembic must honor a caller's connection without consulting application settings.

Seam: real Alembic commands using a supplied SQLAlchemy connection. SQLite is
sufficient for connection routing/ownership, not PostgreSQL schema semantics
(which remain covered by the opt-in migration test).
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from velolab_api import settings


def test_migrations_use_supplied_connection_without_loading_application_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("supplied-connection migration accessed application settings or new engine")

    monkeypatch.setattr(settings, "get_settings", forbidden)
    monkeypatch.setattr("sqlalchemy.create_engine", forbidden)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert "users" in inspect(connection).get_table_names()
            command.downgrade(config, "base")
            assert "users" not in inspect(connection).get_table_names()
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
            assert not connection.closed
    finally:
        engine.dispose()
