"""Real constraint/race coverage in opt-in disposable Postgres databases.

Failure inventory: repeated normalized emails or concurrent inserts must yield
one account without changing its original hash; rejection must leave the session
usable. Unrelated uniqueness failures (e.g. a UUID collision) must propagate as
integrity errors, not email duplicates, and leave no partially written account.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import Engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api import provision_cli
from velolab_api.models import User
from velolab_api.provisioning import DuplicateEmailError, provision_user


@pytest.fixture
def postgres_user_engine(isolated_database: tuple[Config, Engine]) -> Engine:
    config, engine = isolated_database
    command.upgrade(config, "head")
    return engine


def test_normalized_duplicate_preserves_existing_account_and_session_is_usable(
    postgres_user_engine: Engine,
) -> None:
    with Session(postgres_user_engine) as session:
        provision_user(session, "Rider@Example.COM", "original password")
        original = session.scalars(select(User)).one()
        original_id, original_hash = original.id, original.password_hash
        with pytest.raises(DuplicateEmailError):
            provision_user(session, " \tRIDER@example.com ", "replacement password")
        # Successful use after rejection also verifies rollback recovery.
        provision_user(session, "friend@example.com", "friend password")

    with Session(postgres_user_engine) as reader:
        users = reader.scalars(select(User)).all()
        assert len(users) == 2
        stored = reader.get(User, original_id)
        assert stored is not None
        assert stored.password_hash == original_hash
        assert PasswordHasher().verify(stored.password_hash, "original password")


def test_competing_normalized_inserts_create_only_one_account(
    postgres_user_engine: Engine,
) -> None:
    ready = Barrier(2)

    def create(email: str, password: str) -> tuple[bool, str]:
        with Session(postgres_user_engine) as session:

            def rendezvous(*args: object) -> None:
                ready.wait(timeout=10)

            # Both independent sessions reach the insert with valid inputs.
            event.listen(session, "before_flush", rendezvous, once=True)
            try:
                provision_user(session, email, password)
            except DuplicateEmailError:
                assert not session.in_transaction()
                assert session.scalars(select(User)).one().email == "rider@example.com"
                return False, password
            return True, password

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = [
            executor.submit(create, "Rider@Example.COM", "first password"),
            executor.submit(create, " RIDER@example.com ", "second password"),
        ]
        results = [attempt.result(timeout=20) for attempt in attempts]
    assert sorted(created for created, _ in results) == [False, True]
    winner_password = next(password for created, password in results if created)
    with Session(postgres_user_engine) as reader:
        user = reader.scalars(select(User)).one()
        assert user.email == "rider@example.com"
        assert PasswordHasher().verify(user.password_hash, winner_password)


def test_unrelated_integrity_error_is_not_reported_as_duplicate_email(
    postgres_user_engine: Engine,
) -> None:
    with Session(postgres_user_engine) as session:
        provision_user(session, "original@example.com", "original password")
        original = session.scalars(select(User)).one()
        original_id, original_hash = original.id, original.password_hash

        def collide_ids(*args: object) -> None:
            for pending in session.new:
                if isinstance(pending, User):
                    pending.id = original_id

        event.listen(session, "before_flush", collide_ids, once=True)
        with pytest.raises(IntegrityError):
            provision_user(session, "other@example.com", "other password")
        assert not session.in_transaction()
        session.commit()

    with Session(postgres_user_engine) as reader:
        user = reader.scalars(select(User)).one()
        assert user.id == original_id
        assert user.password_hash == original_hash


def test_cli_reports_duplicate_without_exposing_database_exception(
    postgres_user_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with Session(postgres_user_engine) as session:
        provision_user(session, "rider@example.com", "original password")
    monkeypatch.setattr(provision_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(provision_cli.getpass, "getpass", lambda prompt: "replacement password")
    monkeypatch.setattr(provision_cli, "get_engine", lambda: postgres_user_engine)

    assert provision_cli.main(["--email", " Rider@Example.COM "]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "An account with this email already exists.\n"
    with Session(postgres_user_engine) as reader:
        user = reader.scalars(select(User)).one()
        assert PasswordHasher().verify(user.password_hash, "original password")
        assert user.password_hash not in output.err
