"""Private account provisioning rules (no public registration)."""

from argon2 import PasswordHasher
from email_validator import validate_email
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from velolab_api.models import User


class InvalidPasswordError(ValueError):
    """The supplied password is outside the accepted character-length range."""


class DuplicateEmailError(ValueError):
    """An account already owns this normalized email address."""


def normalize_email(email: str) -> str:
    """Validate without DNS checks and apply our whole-address identity policy."""
    return validate_email(email.strip(), check_deliverability=False).normalized.lower()


def provision_user(session: Session, email: str, password: str) -> None:
    """Commit a new account using a dedicated session and the exact password."""
    normalized_email = normalize_email(email)
    if not 10 <= len(password) <= 128:
        raise InvalidPasswordError("Password must be 10–128 characters.")
    password_hash = PasswordHasher().hash(password)
    session.add(User(email=normalized_email, password_hash=password_hash))
    try:
        # Let the DB arbitrate duplicates, including concurrent inserts. A
        # pre-insert lookup alone cannot guarantee uniqueness.
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if isinstance(error.orig, UniqueViolation) and (
            error.orig.diag.constraint_name == "uq_users_email"
        ):
            raise DuplicateEmailError("An account with this email already exists.") from error
        raise
