#!/usr/bin/env bash
# Local dev API launcher (loopback only). Reads the integration keyring from a
# private file into the backend process env only; never printed, never in Vite.
# Usage: OWNER_ID=<uuid> ./scripts/dev-api.sh   (JWT secret kept in ~/.config/velolab/jwt-secret)
set -euo pipefail
cfg="$HOME/.config/velolab"
key_file="$cfg/keyring.json"
jwt_file="$cfg/jwt-secret"
[[ -n "${OWNER_ID:-}" ]] || { echo "Set OWNER_ID to your user UUID" >&2; exit 2; }
[[ -f "$key_file" && "$(stat -f %Lp "$key_file" 2>/dev/null || stat -c %a "$key_file")" == 600 ]] \
  || { echo "Keyring missing or not mode 0600" >&2; exit 1; }
umask 077
[[ -f "$jwt_file" ]] || python3 -c 'import secrets;print(secrets.token_urlsafe(48))' > "$jwt_file"
export INTEGRATION_KEYRING="$(< "$key_file")"
export INTEGRATION_WRITE_KEY_ID=local-v1
export INTEGRATION_PREVIEW_ENABLED=true
export INTEGRATION_PREVIEW_OWNER_ID="$OWNER_ID"
export AUTH_JWT_SECRET="$(< "$jwt_file")"
export AUTH_TRUSTED_ORIGIN=http://127.0.0.1:5173
export AUTH_COOKIE_PATH=/api/auth
cd "$(dirname "$0")/../apps/api"
exec uv run uvicorn velolab_api.app:app --host 127.0.0.1 --port 8000
