"""Private, local-only CLI for provisioning an account (no HTTP registration)."""

import argparse
import getpass
import sys
import warnings
from typing import Never

from email_validator import EmailNotValidError
from sqlalchemy.orm import Session

from velolab_api.db import get_engine
from velolab_api.provisioning import DuplicateEmailError, InvalidPasswordError, provision_user


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse's default error includes rejected values, which could be a
        # mistakenly supplied password. Never echo any argument values.
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid arguments. Use --help; enter passwords only at the hidden prompt.\n")


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(description="Provision a velolab user locally", allow_abbrev=False)
    parser.add_argument(
        "--email", required=True, help="Account email (never supply a password here)"
    )
    args = parser.parse_args(argv)

    # getpass can fall back to echoing input when no terminal is available.
    if not sys.stdin.isatty():
        print("An interactive terminal is required for password entry.", file=sys.stderr)
        return 1
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Password: ")
            confirmation = getpass.getpass("Confirm password: ")
    except EOFError, KeyboardInterrupt, OSError, getpass.GetPassWarning:
        print("Could not read password securely.", file=sys.stderr)
        return 1

    if password != confirmation:
        print("Passwords do not match; no account was created.", file=sys.stderr)
        return 1

    try:
        with Session(get_engine()) as session:
            provision_user(session, args.email, password)
    except EmailNotValidError:
        message = "Invalid email address."
    except InvalidPasswordError:
        message = "Password must be 10–128 characters."
    except DuplicateEmailError:
        message = "An account with this email already exists."
    except Exception, KeyboardInterrupt:
        # This CLI is the error-reporting boundary. Exception text/tracebacks
        # may contain SQL parameters, hashes or database credentials. A lost
        # connection during commit can also leave its outcome uncertain.
        message = "Provisioning failed; no success was confirmed."
    else:
        print("User provisioned.")
        return 0
    print(message, file=sys.stderr)
    return 1
