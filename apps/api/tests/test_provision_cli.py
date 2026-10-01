"""CLI boundary: hidden input, exact credentials, and sanitized failures.

Failure inventory: non-TTY/getpass fallback/cancellation must not open the DB;
mismatch must not write; rejected command arguments must not echo secrets;
validation/duplicate/hash/config/DB failures must not expose exception text or
claim success. A real CLI-to-domain success checks committed data and exact
password preservation, with only the terminal and configured engine replaced.
"""

import getpass
import warnings
from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import HashingError
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api import provision_cli
from velolab_api.models import User


@pytest.fixture
def cli_engine() -> Generator[Engine]:
    engine = create_engine("sqlite://")
    try:
        User.metadata.tables["users"].create(engine)
        yield engine
    finally:
        engine.dispose()


def test_cli_requires_a_terminal_before_prompting_or_using_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: False)
    engine = MagicMock()
    monkeypatch.setattr(provision_cli, "get_engine", engine)
    assert provision_cli.main(["--email", "rider@example.com"]) == 1
    engine.assert_not_called()
    assert "interactive terminal" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--password", "SECRET_PASSWORD"],
        ["--password=SECRET_PASSWORD"],
        ["--unexpected", "SECRET_PASSWORD"],
    ],
)
def test_cli_refuses_password_arguments_before_prompting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    prompt = MagicMock()
    monkeypatch.setattr(provision_cli.getpass, "getpass", prompt)
    with pytest.raises(SystemExit) as error:
        provision_cli.main(["--email", "rider@example.com", *arguments])
    assert error.value.code == 2
    prompt.assert_not_called()
    output = capsys.readouterr()
    assert "SECRET_PASSWORD" not in output.err + output.out


def test_mismatched_confirmation_never_opens_a_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)
    prompt = MagicMock(side_effect=["first", "second"])
    monkeypatch.setattr(provision_cli.getpass, "getpass", prompt)
    engine = MagicMock()
    monkeypatch.setattr(provision_cli, "get_engine", engine)
    assert provision_cli.main(["--email", "rider@example.com"]) == 1
    assert prompt.call_count == 2
    engine.assert_not_called()
    assert "do not match" in capsys.readouterr().err


def test_cli_commits_normalized_email_and_preserves_exact_password(
    cli_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)
    password = "  p\u00e4ssword  "
    monkeypatch.setattr(provision_cli.getpass, "getpass", MagicMock(side_effect=[password] * 2))
    monkeypatch.setattr(provision_cli, "get_engine", lambda: cli_engine)

    assert provision_cli.main(["--email", " Rider@Example.COM "]) == 0
    with Session(cli_engine) as reader:
        user = reader.scalars(select(User)).one()
        assert user.email == "rider@example.com"
        assert PasswordHasher().verify(user.password_hash, password)
        stored_hash = user.password_hash
    output = capsys.readouterr()
    assert output.out == "User provisioned.\n"
    assert password not in output.out + output.err
    assert stored_hash not in output.out + output.err


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt(), getpass.GetPassWarning()])
def test_insecure_or_cancelled_prompt_never_opens_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: BaseException
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)

    def fail_prompt(prompt: str) -> str:
        if isinstance(failure, getpass.GetPassWarning):
            warnings.warn("UNSAFE_FALLBACK", getpass.GetPassWarning, stacklevel=2)
            return "SECRET_PASSWORD"
        raise failure

    monkeypatch.setattr(provision_cli.getpass, "getpass", fail_prompt)
    engine = MagicMock()
    monkeypatch.setattr(provision_cli, "get_engine", engine)
    assert provision_cli.main(["--email", "rider@example.com"]) == 1
    engine.assert_not_called()
    output = capsys.readouterr()
    assert "securely" in output.err
    assert "UNSAFE_FALLBACK" not in output.err


@pytest.mark.parametrize(
    ("email", "password"), [("SECRET_PASSWORD", "valid password"), ("rider@example.com", "short")]
)
def test_cli_validation_failure_does_not_write_or_echo_inputs(
    cli_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    email: str,
    password: str,
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(provision_cli.getpass, "getpass", MagicMock(side_effect=[password] * 2))
    monkeypatch.setattr(provision_cli, "get_engine", lambda: cli_engine)
    assert provision_cli.main(["--email", email]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err
    assert email not in output.err
    assert password not in output.err
    with Session(cli_engine) as reader:
        assert reader.scalars(select(User)).all() == []


def test_cli_sanitizes_configuration_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        provision_cli.getpass, "getpass", MagicMock(side_effect=["SECRET_PASSWORD"] * 2)
    )
    monkeypatch.setattr(
        provision_cli,
        "get_engine",
        MagicMock(side_effect=RuntimeError("postgresql://user:DB_SECRET@localhost/db")),
    )
    assert provision_cli.main(["--email", "rider@example.com"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err
    assert "DB_SECRET" not in output.err
    assert "SECRET_PASSWORD" not in output.err


def test_cli_sanitizes_database_failure_with_sql_parameters_and_hash(
    cli_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        provision_cli.getpass, "getpass", MagicMock(side_effect=["SECRET_PASSWORD"] * 2)
    )
    monkeypatch.setattr(provision_cli, "get_engine", lambda: cli_engine)
    seen_hashes: list[str] = []

    def fail_insert(
        connection: object,
        cursor: object,
        statement: str,
        parameters: tuple[object, ...],
        context: object,
        executemany: bool,
    ) -> None:
        seen_hashes.extend(
            str(value) for value in parameters if str(value).startswith("$argon2id$")
        )
        raise IntegrityError(statement, parameters, RuntimeError("DB_SECRET"))

    event.listen(cli_engine, "before_cursor_execute", fail_insert)
    try:
        assert provision_cli.main(["--email", "rider@example.com"]) == 1
    finally:
        event.remove(cli_engine, "before_cursor_execute", fail_insert)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Provisioning failed; no success was confirmed.\n"
    assert seen_hashes
    assert all(stored_hash not in output.err for stored_hash in seen_hashes)
    assert "SECRET_PASSWORD" not in output.err
    assert "DB_SECRET" not in output.err
    assert "INSERT" not in output.err
    with Session(cli_engine) as reader:
        assert reader.scalars(select(User)).all() == []


def test_cli_sanitizes_hashing_failure_and_does_not_write(
    cli_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        provision_cli.getpass, "getpass", MagicMock(side_effect=["SECRET_PASSWORD"] * 2)
    )
    monkeypatch.setattr(provision_cli, "get_engine", lambda: cli_engine)
    monkeypatch.setattr(
        PasswordHasher, "hash", MagicMock(side_effect=HashingError("SECRET_PASSWORD"))
    )
    assert provision_cli.main(["--email", "rider@example.com"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Provisioning failed; no success was confirmed.\n"
    with Session(cli_engine) as reader:
        assert reader.scalars(select(User)).all() == []
