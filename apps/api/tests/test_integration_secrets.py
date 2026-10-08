"""Credential boundary tests with real AES-GCM and transient ORM rows.

Failure inventory (no HTTP/provider boundary exists yet):
- Missing/malformed/duplicate config or missing selected/historical keys: must
  fail closed with generic errors; isolate here rather than add an endpoint.
- Copied ciphertext, altered identity/nonce/tag/key ID or wrong keys: must not
  decrypt; real crypto + model objects exercise the complete public boundary.
- Noncanonical/unknown/oversized envelopes and invalid plaintext: reject without
  fallback or row mutation; bounds also prevent unbounded parsing/allocation.
- Rotation/replacement: retain mixed-key readability, bind stable identities,
  generate fresh nonces and fail when a historical key is unavailable.
- Repr/JSON/errors: no key material, plaintext, ciphertext or chained exception
  details; environment settings must not consult dotenv or JWT material.
- DB persistence, transaction rollback and concurrent maintenance are outside
  this module (no writes); future workflows require disposable PostgreSQL tests.
  Network timeouts/cancellation, authorization and provider validation likewise
  belong to future callers; crypto binding is not authorization. Same-identity
  ciphertext rollback is explicitly not prevented by AEAD.
"""

import base64
import json
import pickle
import traceback
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr, TypeAdapter

from velolab_api.integration_secrets import IntegrationKeyCipher, IntegrationSecretError
from velolab_api.models import UserIntegration
from velolab_api.settings import IntegrationKeySettings


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def configuration(keys: dict[str, bytes], write: str = "primary") -> IntegrationKeySettings:
    return IntegrationKeySettings(
        keyring=SecretStr(json.dumps({kid: encode(key) for kid, key in keys.items()})),
        write_key_id=write,
    )


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INTEGRATION_KEYRING", raising=False)
    monkeypatch.delenv("INTEGRATION_WRITE_KEY_ID", raising=False)


@pytest.fixture
def row() -> UserIntegration:
    return UserIntegration(
        id=uuid4(), user_id=uuid4(), provider="intervals.icu", external_athlete_id="athlete-1"
    )


@pytest.fixture
def cipher() -> IntegrationKeyCipher:
    return IntegrationKeyCipher(configuration({"primary": AESGCM.generate_key(bit_length=256)}))


def test_missing_configuration_fails_closed() -> None:
    with pytest.raises(IntegrationSecretError, match="Integration credentials unavailable"):
        IntegrationKeyCipher(IntegrationKeySettings())


def test_roundtrip_and_replacement_do_not_mutate_row(
    row: UserIntegration, cipher: IntegrationKeyCipher
) -> None:
    secret = SecretStr("generated-disposable-credential")
    envelope = cipher.encrypt(row, secret)
    assert row.encrypted_api_key is None
    row.encrypted_api_key = envelope.get_secret_value()
    assert row.encrypted_api_key.startswith("v1:primary:")
    assert secret.get_secret_value() not in row.encrypted_api_key
    assert cipher.decrypt(row).get_secret_value() == secret.get_secret_value()

    replacement = cipher.encrypt(row, SecretStr("disposable-replacement"))
    assert row.encrypted_api_key == envelope.get_secret_value()
    assert replacement.get_secret_value().split(":")[2] != row.encrypted_api_key.split(":")[2]
    row.encrypted_api_key = replacement.get_secret_value()
    assert cipher.decrypt(row).get_secret_value() == "disposable-replacement"


@pytest.mark.parametrize("field", ["user_id", "id", "provider", "external_athlete_id"])
def test_changed_binding_fails(
    row: UserIntegration, cipher: IntegrationKeyCipher, field: str
) -> None:
    row.encrypted_api_key = cipher.encrypt(row, SecretStr("disposable")).get_secret_value()
    setattr(row, field, uuid4() if field in {"id", "user_id"} else "different")
    with pytest.raises(IntegrationSecretError):
        cipher.decrypt(row)


@pytest.mark.parametrize("field,offset", [(2, 0), (3, 0), (3, -1)])
def test_nonce_ciphertext_and_tag_tampering_fail(
    row: UserIntegration, cipher: IntegrationKeyCipher, field: int, offset: int
) -> None:
    parts = cipher.encrypt(row, SecretStr("disposable")).get_secret_value().split(":")
    value = parts[field]
    decoded = bytearray(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    decoded[offset] ^= 1
    parts[field] = encode(bytes(decoded))
    row.encrypted_api_key = ":".join(parts)
    with pytest.raises(IntegrationSecretError):
        cipher.decrypt(row)


def test_key_id_is_authenticated_even_with_identical_key_material(row: UserIntegration) -> None:
    key = AESGCM.generate_key(bit_length=256)
    original = IntegrationKeyCipher(configuration({"primary": key}))
    renamed = IntegrationKeyCipher(configuration({"renamed": key}, "renamed"))
    row.encrypted_api_key = original.encrypt(row, SecretStr("disposable")).get_secret_value()
    row.encrypted_api_key = row.encrypted_api_key.replace(":primary:", ":renamed:")
    with pytest.raises(IntegrationSecretError):
        renamed.decrypt(row)


def test_binding_field_boundaries_are_unambiguous(
    row: UserIntegration, cipher: IntegrationKeyCipher
) -> None:
    row.provider, row.external_athlete_id = "x:y", "z"
    row.encrypted_api_key = cipher.encrypt(row, SecretStr("disposable")).get_secret_value()
    row.provider, row.external_athlete_id = "x", "y:z"
    with pytest.raises(IntegrationSecretError):
        cipher.decrypt(row)


def test_rotation_mixed_keys_rewrap_and_retirement(row: UserIntegration) -> None:
    old, new = AESGCM.generate_key(bit_length=256), AESGCM.generate_key(bit_length=256)
    original = IntegrationKeyCipher(configuration({"old": old}, "old"))
    rotating = IntegrationKeyCipher(configuration({"old": old, "new": new}, "new"))
    retired = IntegrationKeyCipher(configuration({"new": new}, "new"))
    wrong = IntegrationKeyCipher(configuration({"old": new}, "old"))
    old_envelope = original.encrypt(row, SecretStr("disposable")).get_secret_value()
    row.encrypted_api_key = old_envelope
    with pytest.raises(IntegrationSecretError):
        wrong.decrypt(row)
    assert rotating.decrypt(row).get_secret_value() == "disposable"
    rewrapped = rotating.encrypt(row, rotating.decrypt(row)).get_secret_value()
    assert rewrapped.startswith("v1:new:")
    assert rewrapped.split(":")[2] != old_envelope.split(":")[2]
    row.encrypted_api_key = rewrapped
    assert rotating.decrypt(row).get_secret_value() == "disposable"
    assert retired.decrypt(row).get_secret_value() == "disposable"
    row.encrypted_api_key = old_envelope
    with pytest.raises(IntegrationSecretError):
        retired.decrypt(row)


@pytest.mark.parametrize(
    "case",
    [
        "plaintext",
        "version",
        "unknown-id",
        "parts",
        "padding",
        "alphabet",
        "noncanonical",
        "short-nonce",
        "short-tag",
        "oversized",
    ],
)
def test_malformed_envelopes_fail_without_fallback(
    row: UserIntegration, cipher: IntegrationKeyCipher, case: str
) -> None:
    parts = cipher.encrypt(row, SecretStr("disposable")).get_secret_value().split(":")
    if case == "plaintext":
        row.encrypted_api_key = "legacy-plaintext"
    elif case == "oversized":
        row.encrypted_api_key = "x" * 5601
    else:
        if case == "version":
            parts[0] = "v2"
        elif case == "unknown-id":
            parts[1] = "missing"
        elif case == "parts":
            parts.append("extra")
        elif case == "padding":
            parts[3] += "="
        elif case == "alphabet":
            parts[2] = "+" + parts[2][1:]
        elif case == "noncanonical":
            # 26 bytes => final sextet has two unused bits. Change only an
            # unused bit: decoded ciphertext/tag still authenticate unless the
            # envelope parser enforces canonical encoding.
            alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            index = alphabet.index(parts[3][-1])
            parts[3] = parts[3][:-1] + alphabet[index + 1]
        elif case == "short-nonce":
            parts[2] = encode(b"short")
        elif case == "short-tag":
            parts[3] = encode(b"short")
        row.encrypted_api_key = ":".join(parts)
    with pytest.raises(IntegrationSecretError):
        cipher.decrypt(row)


@pytest.mark.parametrize(
    "case",
    [
        "missing-write",
        "unknown-write",
        "bad-json",
        "duplicate-id",
        "duplicate-material",
        "short-key",
        "long-key",
        "padded-key",
        "noncanonical-key",
        "invalid-id",
        "empty",
        "array",
        "nested-value",
        "oversized",
        "too-many-keys",
        "deeply-nested",
    ],
)
def test_invalid_configuration_fails_closed(case: str) -> None:
    key = AESGCM.generate_key(bit_length=256)
    settings = configuration({"primary": key})
    if case == "missing-write":
        settings.write_key_id = None
    elif case == "unknown-write":
        settings.write_key_id = "missing"
    else:
        encoded = encode(key)
        invalid = {
            "bad-json": "{invalid-disposable-input",
            "duplicate-id": f'{{"primary":"{encoded}","primary":"{encoded}"}}',
            "duplicate-material": json.dumps({"primary": encoded, "alias": encoded}),
            "short-key": json.dumps({"primary": encode(key[:-1])}),
            "long-key": json.dumps({"primary": encode(key + b"x")}),
            "padded-key": json.dumps({"primary": encoded + "="}),
            "noncanonical-key": json.dumps({"primary": "A" * 42 + "B"}),
            "invalid-id": json.dumps({"not:an:id": encoded}),
            "empty": "{}",
            "array": "[]",
            "nested-value": '{"primary":{"key":"disposable"}}',
            "oversized": " " * 16385,
            "too-many-keys": json.dumps({str(i): encoded for i in range(33)}),
            "deeply-nested": "[" * 1500 + "]" * 1500,
        }
        settings.keyring = SecretStr(invalid[case])
    with pytest.raises(IntegrationSecretError):
        IntegrationKeyCipher(settings)


@pytest.mark.parametrize("value", ["", "x" * 4097, "é" * 2049])
def test_invalid_plaintext_does_not_mutate_row(
    row: UserIntegration, cipher: IntegrationKeyCipher, value: str
) -> None:
    with pytest.raises(IntegrationSecretError):
        cipher.encrypt(row, SecretStr(value))
    assert row.encrypted_api_key is None


def test_utf8_boundary_and_unallocated_row(
    row: UserIntegration, cipher: IntegrationKeyCipher
) -> None:
    value = "é" * 2048
    row.encrypted_api_key = cipher.encrypt(row, SecretStr(value)).get_secret_value()
    assert cipher.decrypt(row).get_secret_value() == value
    unallocated = UserIntegration(
        user_id=row.user_id, provider=row.provider, external_athlete_id=row.external_athlete_id
    )
    with pytest.raises(IntegrationSecretError):
        cipher.encrypt(unallocated, SecretStr("disposable"))


def test_settings_are_environment_only_and_do_not_fall_back_to_jwt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = configuration({"primary": AESGCM.generate_key(bit_length=256)})
    assert settings.keyring is not None
    raw = settings.keyring.get_secret_value()
    env_file = tmp_path / ".env"
    env_file.write_text(f"INTEGRATION_KEYRING='{raw}'\nINTEGRATION_WRITE_KEY_ID=primary\n")
    monkeypatch.setenv("AUTH_JWT_SECRET", raw)
    # Even an explicit _env_file cannot opt this settings class into dotenv.
    missing = IntegrationKeySettings(_env_file=env_file)
    assert missing.keyring is None
    with pytest.raises(IntegrationSecretError):
        IntegrationKeyCipher(missing)
    monkeypatch.setenv("INTEGRATION_KEYRING", raw)
    monkeypatch.setenv("INTEGRATION_WRITE_KEY_ID", "primary")
    loaded = IntegrationKeySettings()
    assert loaded.keyring is not None
    assert loaded.keyring.get_secret_value() == raw
    IntegrationKeyCipher(loaded)


def test_repr_serialization_and_errors_are_redacted(
    row: UserIntegration, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    key = AESGCM.generate_key(bit_length=256)
    settings = configuration({"primary": key})
    cipher = IntegrationKeyCipher(settings)
    secret = SecretStr("disposable-sensitive-value")
    envelope = cipher.encrypt(row, secret)
    row.encrypted_api_key = envelope.get_secret_value()
    decrypted = cipher.decrypt(row)
    visible = " ".join(
        [
            repr(settings),
            settings.model_dump_json(),
            str(settings.model_dump()),
            repr(cipher),
            repr(row),
            repr(envelope),
            repr(decrypted),
            TypeAdapter(SecretStr).dump_json(envelope).decode(),
            TypeAdapter(SecretStr).dump_json(decrypted).decode(),
        ]
    )
    with pytest.raises(TypeError) as serialization:
        pickle.dumps(cipher)
    visible += str(serialization.value)
    settings.keyring = SecretStr("{malformed-disposable-sensitive-configuration")
    with pytest.raises(IntegrationSecretError) as invalid_config:
        IntegrationKeyCipher(settings)
    row.external_athlete_id = "wrong-athlete"
    with pytest.raises(IntegrationSecretError) as invalid_binding:
        cipher.decrypt(row)
    for caught in (invalid_config, invalid_binding):
        error = caught.value
        assert error.__cause__ is None
        assert error.__context__ is None
        visible += repr(error) + "".join(traceback.format_exception(error))
    for sensitive in (
        secret.get_secret_value(),
        envelope.get_secret_value(),
        encode(key),
        "malformed-disposable-sensitive-configuration",
    ):
        assert sensitive not in visible
    assert settings.model_dump() == {}
    assert not caplog.records
    assert capsys.readouterr() == ("", "")
