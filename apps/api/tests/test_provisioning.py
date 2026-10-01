"""Domain boundary tests; SQLite covers portable rules, Postgres covers constraints.

Failure inventory: invalid/empty email or out-of-range passwords must not write;
case/whitespace and Unicode normalization must be consistent; accepted passwords
must retain spaces/Unicode and be hashed, never stored verbatim. Hashing failures
must leave no pending account. Named email uniqueness failures must roll back and
be distinguished from other integrity failures. The disposable-Postgres tests
exercise actual persistence, duplicates and competing inserts. CLI tests cover
input cancellation and secret-safe failures. Auth, network retries and schema
changes are outside this slice.
"""

from collections.abc import Generator

import pytest
from alembic import command
from argon2 import PasswordHasher
from argon2.exceptions import HashingError, VerifyMismatchError
from email_validator import EmailNotValidError
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api.models import User
from velolab_api.provisioning import DuplicateEmailError, normalize_email, provision_user


@pytest.fixture(params=["sqlite", "postgres"])
def user_engine(request: pytest.FixtureRequest) -> Generator[Engine]:
    if request.param == "postgres":
        config, engine = request.getfixturevalue("isolated_database")
        command.upgrade(config, "head")
        yield engine
    else:
        engine = create_engine("sqlite://")
        try:
            # Fast portable domain checks, not proof of Postgres constraint behavior.
            User.metadata.tables["users"].create(engine)
            yield engine
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        (" \tRider@Example.COM\n", "rider@example.com"),
        ("U\u0308SER@BÜCHER.DE", "üser@bücher.de"),
        ("Rider@xn--bcher-kva.de", "rider@bücher.de"),
    ],
)
def test_normalize_email(email: str, expected: str) -> None:
    assert normalize_email(email) == expected


@pytest.mark.parametrize("email", ["", "   ", "rider", "rider@", "a b@example.com"])
def test_normalize_email_rejects_invalid_addresses(email: str) -> None:
    with pytest.raises(EmailNotValidError):
        normalize_email(email)


@pytest.mark.parametrize("password", ["a" * 10, "界" * 128, "  pässword  ", " " * 10])
def test_provision_user_commits_normalized_email_and_exact_password_hash(
    user_engine: Engine, password: str
) -> None:
    with Session(user_engine) as session:
        provision_user(session, " \tRider@Example.COM ", password)

    # A fresh session observes committed data rather than cached ORM state.
    with Session(user_engine) as reader:
        user = reader.scalars(select(User)).one()
        assert user.email == "rider@example.com"
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != password
        assert PasswordHasher().verify(user.password_hash, password)
        with pytest.raises(VerifyMismatchError):
            PasswordHasher().verify(user.password_hash, password + "!")
        if password.strip() != password:
            with pytest.raises(VerifyMismatchError):
                PasswordHasher().verify(user.password_hash, password.strip())


@pytest.mark.parametrize(
    ("email", "password", "error"),
    [
        ("not-an-email", "valid password", EmailNotValidError),
        ("rider@example.com", "", ValueError),
        ("rider@example.com", "a" * 9, ValueError),
        ("rider@example.com", "界" * 129, ValueError),
    ],
)
def test_invalid_input_leaves_no_account_or_pending_write(
    user_engine: Engine, email: str, password: str, error: type[Exception]
) -> None:
    with Session(user_engine) as session:
        with pytest.raises(error):
            provision_user(session, email, password)
        assert not session.new
        # Even an accidental later commit cannot write a rejected account.
        session.commit()
    with Session(user_engine) as reader:
        assert reader.scalars(select(User)).all() == []


def test_hashing_failure_leaves_no_pending_account(
    user_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_hash(*args: object, **kwargs: object) -> str:
        raise HashingError("hashing unavailable")

    monkeypatch.setattr(PasswordHasher, "hash", fail_hash)
    with Session(user_engine) as session:
        with pytest.raises(HashingError):
            provision_user(session, "rider@example.com", "valid password")
        assert not session.new
        session.commit()
        assert session.scalars(select(User)).all() == []


@pytest.mark.parametrize(
    ("driver_error", "expected_error"),
    [
        (UniqueViolation("duplicate", info={ord("n"): b"uq_users_email"}), DuplicateEmailError),
        (UniqueViolation("other duplicate", info={ord("n"): b"pk_users"}), IntegrityError),
        (ForeignKeyViolation("foreign key", info={ord("n"): b"uq_users_email"}), IntegrityError),
        (UniqueViolation("no diagnostic"), IntegrityError),
    ],
)
def test_database_failure_rolls_back_and_only_email_uniqueness_is_translated(
    user_engine: Engine, driver_error: Exception, expected_error: type[Exception]
) -> None:
    # Inject a real psycopg diagnostic at the DB boundary. Actual Postgres
    # duplicate/race tests below remain necessary to verify driver behavior.
    failure = IntegrityError("INSERT", {}, driver_error)

    def fail_insert(*args: object) -> None:
        raise failure

    event.listen(user_engine, "before_cursor_execute", fail_insert)
    try:
        with Session(user_engine) as session:
            with pytest.raises(expected_error) as caught:
                provision_user(session, "rider@example.com", "valid password")
            if expected_error is IntegrityError:
                assert caught.value is failure
            assert not session.new
            assert not session.in_transaction()
            event.remove(user_engine, "before_cursor_execute", fail_insert)
            session.commit()
            assert session.scalars(select(User)).all() == []
    finally:
        if event.contains(user_engine, "before_cursor_execute", fail_insert):
            event.remove(user_engine, "before_cursor_execute", fail_insert)
