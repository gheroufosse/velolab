"""Server-only integration credentials; no persistence, provider calls or logging.

Callers must authorize ownership before use. Enrollment must allocate the row
UUID and verify athlete identity before encryption. Decryption always binds to
metadata on the supplied persisted row, never metadata from an envelope.
"""

import base64
import json
import secrets
from re import fullmatch
from typing import Never, SupportsIndex
from uuid import UUID

from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from velolab_api.models import UserIntegration
from velolab_api.settings import IntegrationKeySettings

_MAX_CREDENTIAL_BYTES = 4096
_MAX_ENVELOPE_CHARS = 5600
_MAX_KEYRING_CHARS = 16384
_MAX_KEYS = 32
_KEY_ID_PATTERN = r"[A-Za-z0-9_-]{1,32}"
_ERRORS = (ValueError, TypeError, KeyError, RecursionError, InvalidTag, UnsupportedAlgorithm)


class IntegrationSecretError(Exception):
    """Safe public failure; never contains inputs or cryptographic details."""

    def __init__(self) -> None:
        super().__init__("Integration credentials unavailable.")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str, minimum: int, maximum: int) -> bytes:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= (maximum * 4 + 2) // 3
        or not fullmatch(r"[A-Za-z0-9_-]+", value)
    ):
        raise ValueError
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if not minimum <= len(decoded) <= maximum or _encode(decoded) != value:
        raise ValueError
    return decoded


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _keyring(settings: IntegrationKeySettings) -> tuple[str, dict[str, AESGCM]]:
    if settings.keyring is None:
        raise ValueError
    raw = settings.keyring.get_secret_value()
    if not 1 <= len(raw) <= _MAX_KEYRING_CHARS:
        raise ValueError
    parsed = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(parsed, dict) or not 1 <= len(parsed) <= _MAX_KEYS:
        raise ValueError
    keys: dict[str, AESGCM] = {}
    material: set[bytes] = set()
    for kid, encoded in parsed.items():
        if not isinstance(kid, str) or not fullmatch(_KEY_ID_PATTERN, kid):
            raise ValueError
        key = _decode(encoded, 32, 32)
        # Reusing material under multiple IDs obscures actual key rotation.
        if key in material:
            raise ValueError
        material.add(key)
        keys[kid] = AESGCM(key)
    write = settings.write_key_id
    if not isinstance(write, str) or write not in keys:
        raise ValueError
    return write, keys


def _associated_data(row: UserIntegration, kid: str) -> bytes:
    if not isinstance(row.user_id, UUID) or not isinstance(row.id, UUID):
        raise ValueError
    if (
        not isinstance(row.provider, str)
        or not 1 <= len(row.provider) <= 50
        or not isinstance(row.external_athlete_id, str)
        or not 1 <= len(row.external_athlete_id) <= 255
    ):
        raise ValueError
    fields = (
        "v1",
        kid,
        "user_integrations",
        str(row.user_id),
        str(row.id),
        row.provider,
        row.external_athlete_id,
    )
    parts = [b"velolab:integration-api-key\x00"]
    for field in fields:
        encoded = field.encode("utf-8")
        parts.extend((len(encoded).to_bytes(4, "big"), encoded))
    return b"".join(parts)


class IntegrationKeyCipher:
    """Validated keyring. Reconstruct after installing/changing configuration.

    SecretStr requires explicit unwrapping at the persistence/provider boundary;
    both encrypted and decrypted values are redacted by repr and JSON encoding.
    Operations return values without mutating rows or committing transactions.
    """

    __slots__ = ("_write_key_id", "_keys")

    def __init__(self, settings: IntegrationKeySettings) -> None:
        try:
            self._write_key_id, self._keys = _keyring(settings)
            return
        except _ERRORS:
            pass
        # Raise outside the handler: not even __context__ retains unsafe errors.
        raise IntegrationSecretError()

    def __repr__(self) -> str:
        return "<IntegrationKeyCipher [redacted]>"

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> Never:
        raise TypeError("IntegrationKeyCipher cannot be serialized.")

    def encrypt(self, row: UserIntegration, api_key: SecretStr) -> SecretStr:
        try:
            if not isinstance(api_key, SecretStr):
                raise TypeError
            # Check characters before encoding to bound allocation even for UTF-8.
            value = api_key.get_secret_value()
            if not 1 <= len(value) <= _MAX_CREDENTIAL_BYTES:
                raise ValueError
            plaintext = value.encode("utf-8")
            if len(plaintext) > _MAX_CREDENTIAL_BYTES:
                raise ValueError
            kid = self._write_key_id
            aad = _associated_data(row, kid)
            nonce = secrets.token_bytes(12)
            ciphertext = self._keys[kid].encrypt(nonce, plaintext, aad)
            return SecretStr(f"v1:{kid}:{_encode(nonce)}:{_encode(ciphertext)}")
        except _ERRORS:
            pass
        raise IntegrationSecretError()

    def decrypt(self, row: UserIntegration) -> SecretStr:
        try:
            envelope = row.encrypted_api_key
            if not isinstance(envelope, str) or not 1 <= len(envelope) <= _MAX_ENVELOPE_CHARS:
                raise ValueError
            fields = envelope.split(":")
            if len(fields) != 4:
                raise ValueError
            version, kid, encoded_nonce, encoded_ciphertext = fields
            if version != "v1" or not fullmatch(_KEY_ID_PATTERN, kid):
                raise ValueError
            key = self._keys[kid]  # Select exactly this key; no fallback attempts.
            nonce = _decode(encoded_nonce, 12, 12)
            ciphertext = _decode(encoded_ciphertext, 17, _MAX_CREDENTIAL_BYTES + 16)
            plaintext = key.decrypt(nonce, ciphertext, _associated_data(row, kid))
            return SecretStr(plaintext.decode("utf-8"))
        except _ERRORS:
            pass
        raise IntegrationSecretError()
