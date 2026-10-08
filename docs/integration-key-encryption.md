# Integration API-key encryption — proposed Stage 2 design

**Status:** Design only (2026-10-02); not implemented, verified or approved for
real credentials. ADR-023 is the gate before storing a real intervals.icu key.
This is independent of the unresolved sync evidence in
[the data contract](intervals-data-contract.md). No dependency, schema,
settings, endpoint or migration changes are made by this document.

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

## Proposed cryptographic format and identity binding

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
  delivery method before implementation (restricted secret file/manager are
  options, not assumptions); do not place real material in docs, fixtures,
  examples or shell history. Root `.env` loading in current `Settings` is not
  an approved custody method. Validate keyring and write-key configuration
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

Future tests use generated disposable keys/credentials only: round-trip;
changed ciphertext/tag/nonce/AAD; wrong key, user, row, provider and athlete;
malformed/unknown version or `kid`/key configuration; serialization and error
redaction; replacement binding; interrupted rotation, resume, conflicting
write/maintenance exclusion, mixed-key rollback and backup/key mismatch.
Exercise persistence/concurrency against the dedicated disposable PostgreSQL
instance (ADR-019), never the real-use DB. No live verification is claimed.

**Unresolved operational gate:** owner approval of server key delivery, separate
encrypted backup and restore process, maintenance access/downtime, retention
window and publication is required before implementation or real-key entry.
This document is not authorization to run production commands, publish the
branch, or bypass the sync contract's independent evidence blockers.
