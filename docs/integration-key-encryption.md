# Integration API-key encryption — Stage 2 implementation

**Status:** Owner-authorized crypto/settings slice implemented and tested locally;
**not deployed or approved for real credentials**. ADR-023 remains the gate before
real-key storage, including the operational approvals below. This is independent
of the unresolved sync evidence in [the data contract](intervals-data-contract.md).
The implementation adds `cryptography`, environment-only integration-key settings
and a small crypto module. The existing text column requires no schema change or
migration. ADR-025's backend-only connection test/save routes are now implemented
and synthetically tested; sync and a database rotation command are not implemented.
Real-key entry remains operationally gated.

## Scope and threat boundary

Protect `UserIntegration.encrypted_api_key` from a **database-only** leak,
including copies of database backups without the separate encryption key.
The backend must decrypt briefly to call the provider. A compromised running
backend, its process memory, key store or both DB and key backup defeat this
protection. Existing DB metadata (`user_id`, integration `id`, `provider`,
`external_athlete_id`, timestamps), ciphertext length and existence remain
visible. Authenticated encryption detects altered ciphertext/binding, but not
rollback of an earlier valid ciphertext for the *same* identity, nor deletion
of rows; database audit/backup integrity is a separate concern. This does not
encrypt JWTs, passwords, session digests or training data.

## Cryptographic format and identity binding

- Use the maintained Python `cryptography` library's `AESGCM` with a random
  256-bit key per key ID. `AESGCM.generate_key(bit_length=256)` yields suitable
  key bytes. For each encryption, generate a fresh 12-byte random nonce; **never
  reuse a nonce under a key**, including during replacement or rotation.
  `AESGCM.encrypt(nonce, plaintext, associated_data)` returns ciphertext with
  a 16-byte authentication tag appended; `decrypt` raises `InvalidTag` for a
  wrong key, nonce, ciphertext or associated data. See the
  [official AESGCM API](https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).
- One strictly parsed ASCII envelope in the existing `encrypted_api_key` text
  column: `v1:<kid>:<nonce-b64url>:<ciphertext-and-tag-b64url>`. `kid` is a
  short opaque, unique, nonsecret identifier chosen by the operator (not a
  fingerprint or key bytes); canonical unpadded URL-safe base64 encodes the
  random nonce and ciphertext+tag. Reject unknown versions, unknown IDs,
  duplicate IDs in configuration, malformed/noncanonical encoding, incorrect
  nonce or tag length, or oversized fields; never interpret legacy plaintext
  or attempt other keys if the selected key is missing.
- Construct **associated data** deterministically with a domain/version
  discriminator and length-delimited UTF-8 fields: envelope `v1`, `kid`,
  `user_integrations`, row `user_id` UUID, row `id` UUID, exact `provider` and
  exact `external_athlete_id`. Use canonical UUID formatting and unambiguous
  field boundaries; reconstruct from the persisted row, *not* IDs copied
  from the envelope or supplied by the caller. Version and ID are therefore
  authenticated too. AAD is **not encrypted**; these fields are already DB
  metadata. A ciphertext copied into another integration/user/provider or
  athlete namespace cannot authenticate there. Binding is not authorization:
  enforce caller ownership of the integration independently before decrypting
  or using its key. [AESGCM associated-data semantics](https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).
- Initial enrollment has no persisted row yet: validate key configuration
  and authorize the operation first; use the transient submitted credential
  only to resolve athlete identity through the fixed trusted provider endpoint.
  Allocate the integration UUID explicitly, form AAD from the verified athlete
  identity and intended immutable row values, then encrypt and insert the
  **complete** row atomically. Subsequent decryptions reconstruct AAD from the
  persisted row. Never persist plaintext/placeholder keys or relax nullability.
- Keep the integration UUID stable when replacing a credential for the *same
  verified athlete*: encrypt a new envelope with a fresh nonce and atomically
  replace the old value, never mutate the plaintext in place. Verify the new
  credential's athlete identity against `external_athlete_id` **before**
  accepting it. A changed athlete/provider/user is not a credential update:
  stop, explicitly decide namespace/data reconciliation, and create a new
  bound record or perform an approved migration. Changing any bound metadata
  without re-encrypting must fail to decrypt. DB-write adversaries with old
  ciphertext may still replay a prior credential into the *same* row; AAD
  cannot prevent that.

Fernet is also authenticated and `MultiFernet` supports ordered encrypt/decrypt
keys and rotation, but Fernet has no associated-data parameter and exposes the
encryption timestamp. Packing identifiers *inside* its plaintext plus an
explicit post-decrypt comparison could bind the row, but AES-GCM's native AAD
makes the nonsecret identity checks more direct. AES-GCM's nonce discipline
and envelope parser require careful implementation and review; no custom
cryptographic primitive is proposed. [Fernet and MultiFernet docs](https://cryptography.io/en/stable/fernet/).

## Key custody and failure behavior

- Provision independent random 32-byte keys **outside the DB and repository**;
  represent each as canonical unpadded URL-safe base64 of exactly 32 decoded
  bytes. Maintain a restricted server-only keyring (`kid` → key) and separate
  write-key ID; never reuse `AUTH_JWT_SECRET`, derive keys from human passwords,
  or ship a usable default. The owner must approve the deployment secret
  delivery method before deployment or real-key entry (restricted secret
  file/manager are options, not assumptions); do not place real material in docs,
  fixtures, examples or shell history. Root `.env` loading in current `Settings`
  is not an approved custody method. Validate keyring and write-key configuration
  before any integration-credential operation. Missing, malformed or
  duplicate material, unknown selected `kid`, failed authentication or
  unsupported envelope **fails closed**: no plaintext fallback, no write,
  no provider call and no success marker. Health/private provisioning need not
  depend on integration keys; health must not imply credential readiness.
- Exclude raw or decrypted secrets, key material, ciphertext, request auth
  headers, SQL parameter dumps and cryptographic exception detail from HTTP
  serialization, logs, tracebacks and telemetry. Return a generic integration
  unavailable/error result; log only safe error categories/opaque IDs if needed.
  Never embed secrets in domain payloads or browser responses. This must hold
  on validation, provider and database exceptions as well as happy paths.

## Implemented interface and conservative choices

- `velolab_api.integration_secrets.IntegrationKeyCipher(IntegrationKeySettings())`
  validates the complete keyring and selected write key before use. Its
  `encrypt(row, api_key: SecretStr) -> SecretStr` and
  `decrypt(row) -> SecretStr` methods perform no row mutation, I/O, authorization
  or commit. Decryption reads both the envelope and all bound metadata from the
  supplied `UserIntegration`; callers must fetch/authorize the persisted row.
  Enrollment must explicitly allocate a UUID before encryption (SQLAlchemy's
  column default otherwise runs only at insertion).
- `IntegrationKeySettings` is separate from database/auth `Settings`, which
  avoids requiring DB credentials to validate crypto configuration. It accepts
  explicit constructor injection or **process environment only**:
  `INTEGRATION_KEYRING` is a JSON object mapping key IDs to encoded 32-byte keys;
  `INTEGRATION_WRITE_KEY_ID` selects one of those IDs. No dotenv/file-secret
  source is used, even if `_env_file` is supplied. Missing values are permitted
  at settings construction for health/provisioning, but cipher construction
  fails closed. This selects a narrow configuration interface, **not** an
  approved deployment key-delivery method; do not store real keys in root `.env`.
- Key IDs are 1–32 ASCII letters, digits, `_` or `-`. Reject repeated JSON IDs
  and identical decoded key material under multiple IDs; rotation must install
  genuinely new material. Limit the keyring to 32 keys and 16,384 JSON characters.
  Every configured key is validated, not only the selected write key.
- Credentials are exact, nonempty UTF-8 strings of at most 4,096 encoded bytes:
  no trimming or normalization. Bound provider/athlete strings are exact and
  nonempty, within the existing 50/255-character column limits. UUIDs must be
  UUID objects and are encoded canonically. AAD starts with the bytes
  `velolab:integration-api-key` followed by a zero byte; each documented field
  follows in order, prefixed by its UTF-8 byte length as a four-byte unsigned
  big-endian integer. This fixes the v1 wire contract without delimiter ambiguity.
- Reject envelopes over 5,600 ASCII characters, noncanonical base64, non-12-byte
  nonces and ciphertext/tag fields outside 17–4,112 decoded bytes (16-byte tag
  plus nonempty bounded plaintext). Reads select only the envelope's key ID;
  writes always use the configured write key and fresh random nonce. Installing
  new configuration requires constructing a new cipher (or restarting callers);
  no hidden global key cache or automatic key retirement is introduced.
- Both plaintext and envelopes are returned as `SecretStr`, requiring explicit
  `.get_secret_value()` only at the provider/persistence boundary. Settings
  fields are excluded from repr and model serialization; the cipher has a
  redacted repr and refuses pickling. Expected configuration/parser/AEAD failures
  become `IntegrationSecretError` with the message
  `Integration credentials unavailable.`, with no original exception context/cause. The module emits no logs. The application
  SQLAlchemy engine uses `hide_parameters=True` to redact bound parameters in
  SQL errors/logs; ORM rows still contain the stored envelope, not plaintext.

**Caller obligations:** never serialize an integration ORM row, dump its
attributes, log explicitly unwrapped values, or enable traceback/telemetry
capture of local variables. `SecretStr` is an accidental-disclosure guard, not
process-memory isolation or an HTTP response contract. Future enrollment/sync
must use explicit response allowlists and generic error handling across provider
and database failures, and must never expose either ciphertext or plaintext to
the browser. This slice neither accepts nor stores real credentials.

## Local key-file delivery for the ADR-025 preview

> **ADR-026 waives key backup/restore** for single-owner local use. Steps
> below about separate backup, restore proof, retention and downtime are not
> required; loss recovery is: new keyring + re-enter the API key. Real-use DB
> commands and live provider calls still need explicit approval.

Proposed custody flow, **not permission to generate/use a real-use key or start
an API against real-use PostgreSQL**:

1. Choose an absolute custody path outside the repository and database,
   in an owner-only directory (0700) (`~/.config/velolab/keyring.json` for
   `scripts/dev-api.sh`). Backup/restore/retention waived (ADR-026).
2. Run `./scripts/gen-integration-key.sh /absolute/private/path/keyring.json` once.
   It generates 256 random bits, stores the unpadded base64url key under
   `local-v1` in a JSON keyring, creates a 0600 file without printing material,
   refuses overwrite/symlinks and repository paths, and requires an owner-only
   parent directory. Do not use shell tracing, paste/cat the file, commit it,
   or generate replacement keys automatically at startup. Losing it makes the
   stored credentials unrecoverable.
3. After approval, an owner-maintained **backend-only** launcher reads the file
   into process environment. For example, the following is a Bash script (run
   it as a script, not as fish/zsh rc-file configuration); replace the nonsecret
   path and launch command for the approved deployment:

   ```bash
   #!/usr/bin/env bash
   set -euo pipefail  # never add set -x
   key_file=/absolute/private/path/keyring.json
   python3 - "$key_file" <<'PY'
   import os, stat, sys
   from pathlib import Path
   p = Path(sys.argv[1])
   if p.is_symlink():
       sys.exit("Invalid key custody")
   for path, mode in ((p, 0o600), (p.parent, 0o700)):
       metadata = path.stat()  # missing files fail; never regenerate
       if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != mode:
           sys.exit("Invalid key custody")
   PY
   export INTEGRATION_KEYRING="$(< "$key_file")"
   export INTEGRATION_WRITE_KEY_ID=local-v1
   exec /absolute/path/to/backend-launch-command
   ```

   The secret is not a command argument/history entry and is not written to
   root `.env`. The environment remains readable to privileged/local process
   inspection; this is not protection from a compromised host. Never launch
   Vite from this environment or create any `VITE_*` secret variable. The
   launcher must check custody permissions remain 0700/0600 before use and
   fail if the file is missing; it must not regenerate it.
4. ~~Before entering a real provider key, back up this keyring **separately and
   encrypted**, then prove restore using generated secrets on the dedicated
   disposable PostgreSQL instance. Restore both the matching DB and keyring;
   validate AES-GCM decryption against the restored row IDs/owner/provider/
   athlete binding. Record owner approval and verification evidence here; the
   current synthetic connection tests are not backup/restore verification.~~
   Waived by ADR-026.

Backend preview configuration is server-only:
`INTEGRATION_PREVIEW_ENABLED=true`, `INTEGRATION_PREVIEW_OWNER_ID=<owner UUID>`,
`AUTH_TRUSTED_ORIGIN=<exact loopback browser origin>`, `AUTH_JWT_SECRET` and
`AUTH_COOKIE_PATH=/api/auth` behind the preview proxy, plus ordinary backend
`POSTGRES_*` settings. Crypto accepts only process environment
`INTEGRATION_KEYRING` and `INTEGRATION_WRITE_KEY_ID`. Missing opt-in/owner or a
non-loopback origin disables the preview; a different authenticated user is
rejected. Bind all listeners to loopback separately; configured origin checking
cannot establish actual listener/network safety.

`GET /integrations/intervals` returns only configured/athlete/static-error
metadata. Test accepts transient submitted credentials but stores nothing;
Save independently re-verifies and stores only an authenticated envelope.
Same-athlete replacement retains its integration UUID; rebinding and an active
sync lease return 409. All connection responses are no-store, including static
validation errors (malformed JSON and mounted paths included). Provider errors
never return their bodies, and httpx/httpcore loggers are WARNING. Do not enable
request-body, exception-local, auth-header or SQL-parameter capture in logging/
telemetry. No telemetry integration is currently configured.

## Small single-instance rotation and recovery plan (future procedure)

1. With sync/credential writes **paused under a maintenance window**, take a
   database backup and verify that the separately protected key backup can
   recover every `kid` referenced by that backup. Owner chooses secure,
   encrypted, access-controlled backup location/media independently of the DB
   backup and repository; test restore with generated disposable secrets before
   first real use. A DB backup without its historical decrypt keys is useless.
2. Install a new key with a new ID alongside old decrypt keys; **before**
   switching the write-key ID or rewriting rows, securely back up the new key
   separately from the DB and verify recovery (the old backup cannot restore
   new-key rows). Then switch only the write-key ID to new; verify both old and
   new decrypt keys are present on every backend instance before resuming any
   integration operation. For
   the initial single-instance deployment, **keep sync and credential writes
   paused throughout the rewrite** (or shut down API writes), then re-encrypt
   rows in short transactions using persisted row metadata for AAD. Read,
   decrypt, encrypt with fresh nonce, and commit each replacement atomically;
   never delete the old ciphertext before the new value is committed. If
   maintenance cannot enforce exclusivity, use a row lock plus compare-and-
   swap on the original ciphertext and retry stale rows instead; do not race
   ordinary credential replacement.
3. A crash leaves a mixed-key dataset: rerun the idempotent scan of rows whose
   envelope `kid` is old. Verify decrypt of each newly written row, count and
   independently scan **all** rows for old IDs before declaring completion.
   Never log plaintext while checking. Resume writes only with all referenced
   decrypt keys available. On failure, retain old keys, restore the previous
   write-key choice if necessary, or restore DB **and its matching historical
   keyring together**; do not roll back the DB alone after key retirement.
4. Retire an old key only after zero live rows reference it **and** all retained
   DB backups requiring it have expired or been securely re-encrypted/retired,
   with restore verification and owner sign-off. Keep both keys during the
   bounded rotation/backup-retention period; record deadline and defer removal
   rather than discard a key prematurely. A compromised key calls for a
   separate incident response and credential replacement, not just rewrapping.

## Verification and outstanding approval

`apps/api/tests/test_integration_secrets.py` exercises the public crypto/settings
boundary with real AES-GCM and transient ORM rows, using only generated encryption
keys and synthetic disposable credentials. Coverage includes round-trip and
replacement with fresh nonces; tampered ciphertext/tag/nonce; wrong key, user,
row, provider and athlete; authenticated key IDs and unambiguous AAD fields;
malformed/unknown/noncanonical/oversized envelopes and key configuration; duplicate
IDs/material; UTF-8 bounds and unallocated row IDs; mixed-key reads, rewrapping
and historical-key retirement failure; environment-only loading with no JWT or
dotenv fallback; repr/JSON/pickle safeguards and generic unchained errors.

Initial crypto-slice verification: **44 crypto/settings tests passed**;
`env -u VELOLAB_TEST_DATABASE ./scripts/check.sh` passed lint, formatting, types
and the default suite: **214 passed, 25 PostgreSQL-dependent tests skipped**
(two dependency deprecation warnings). No PostgreSQL instance was started or
touched; existing fast tests use disposable in-memory SQLite where applicable.
That initial run claimed no PostgreSQL persistence, deployment, backup/restore
or maintenance verification.

ADR-025 backend connection-slice verification: `VELOLAB_TEST_DATABASE=1
./scripts/check.sh` passed lint, formatting, types and **323 tests, zero skips**
on a fresh dedicated PostgreSQL 17 instance (127.0.0.1:5436, tmpfs, separate
Compose project). The 17 connection integration tests use synthetic credentials,
real auth/AES-GCM/persistence and `httpx.MockTransport`: test/save separation,
independent verification, immutable identity and stable-UUID replacement with
fresh nonces, provider/crypto/database failures without storage changes,
redacted responses/validation on mounted paths, preview/owner isolation,
concurrent first enrollment and active-lease replacement exclusion. Existing
crypto tests cover restoration-compatible binding and mixed-key reads; no
actual backup/restore workflow was run. Key-generator manual checks verified
256-bit material, 0600/0700 permissions, no printed material and refusal of
overwrite, symlink and repository paths. Temporary generated keys were removed.
Zero fixture databases remained; the disposable container/network and temporary
test configuration were removed. Three existing dependency/test warnings remain.
No live provider, real-use DB, frontend, deployment or publication was exercised.

Future rotation workflows must still exercise interrupted rotation/resume,
maintenance exclusion, mixed-key rollback and backup/key mismatch against the
dedicated disposable PostgreSQL instance (ADR-019), never the real-use DB.

**Operational gate (amended by ADR-026):** backup/restore/retention waived for
single-owner local use; still required before deployment or any second user. Publication requires
separate approval. Autonomous authorization covered this implementation/test
slice only: this document is not authorization to run production commands,
publish the branch, or bypass the sync contract's independent evidence blockers.
