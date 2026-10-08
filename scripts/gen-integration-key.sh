#!/usr/bin/env bash
# Generate once, never print material, and never overwrite an existing key file.
set -euo pipefail
if [[ $# != 1 ]]; then
  echo 'Usage: gen-integration-key.sh /absolute/private/path/keyring.json' >&2
  exit 2
fi
umask 077
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 - "$1" "$repo_root" <<'PY'
import base64
import json
import os
import secrets
import stat
import sys
from pathlib import Path

try:
    supplied = Path(sys.argv[1]).expanduser()
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValueError
    path = supplied.resolve()
    if path.is_relative_to(Path(sys.argv[2]).resolve()):
        raise ValueError
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
        raise ValueError
    # O_EXCL also refuses symlinks/racing creation; mode is restricted at creation.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
            json.dump({"local-v1": key}, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink()
        raise
except (OSError, ValueError):
    print("Key creation failed: use a new absolute path outside the repository in an owner-only directory.", file=sys.stderr)
    sys.exit(1)
PY
