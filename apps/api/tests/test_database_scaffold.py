"""Database scaffold contracts without requiring a running Postgres server.

Failure inventory: missing credentials must fail when settings are used, not at
health-check import; punctuation in passwords must survive URL construction;
invalid ports must fail validation. A session must be released on normal exit
and errors, and writes must not be implicitly committed. Health is covered by
its existing API test; connectivity and migration execution need a real DB and
are checked separately. Concurrent requests, retries, auth, and schema state
are outside this scaffold's contract.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from velolab_api import db
from velolab_api.settings import ENV_FILE, Settings


@pytest.fixture
def clean_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("USER", "DB", "PASSWORD", "HOST", "PORT"):
        monkeypatch.delenv(f"POSTGRES_{name}", raising=False)


def test_settings_builds_sync_url_and_validates_port(clean_database_env: None) -> None:
    assert Path(__file__).resolve().parents[3] / ".env" == ENV_FILE
    settings = Settings(
        _env_file=None,
        postgres_user="rider",
        postgres_db="training",
        postgres_password="p@ss:/word%",
    )
    url = settings.database_url
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "rider"
    assert url.database == "training"
    assert url.host == "127.0.0.1"
    assert url.port == 5434
    assert url.password == "p@ss:/word%"
    assert "p%40ss%3A%2Fword%25" in url.render_as_string(hide_password=False)
    assert "p@ss:/word%" not in str(url)

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            postgres_user="rider",
            postgres_db="training",
            postgres_password="password",
            postgres_port=65536,
        )


def test_settings_loads_env_file_and_explicit_values_take_precedence(
    tmp_path: Path, clean_database_env: None
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_USER=from_file\nPOSTGRES_DB=training\nPOSTGRES_PASSWORD=sample\n")
    settings = Settings(_env_file=env_file, postgres_user="explicit")
    assert settings.database_url.username == "explicit"
    assert settings.database_url.database == "training"


def test_session_is_closed_without_implicit_commit_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    engine = object()
    session_factory = MagicMock(return_value=session)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(db, "Session", session_factory)

    dependency = db.get_session()
    assert next(dependency) is session
    with pytest.raises(StopIteration):
        next(dependency)
    session.__exit__.assert_called_once_with(None, None, None)
    session.commit.assert_not_called()
    session_factory.assert_called_once_with(engine)

    session.reset_mock()
    dependency = db.get_session()
    assert next(dependency) is session
    error = RuntimeError("route failed")
    with pytest.raises(RuntimeError, match="route failed"):
        dependency.throw(error)
    session.__exit__.assert_called_once()
    assert session.__exit__.call_args.args[:2] == (RuntimeError, error)
    session.commit.assert_not_called()
