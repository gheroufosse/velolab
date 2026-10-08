# Session handoff — Stage 2 contract draft

## Current handoff — Stage 2 research (ADR-022)

Stage 1 auth PR #13 merged as `8c5268977878d75e0786a2b7d97552cf515640d2`;
Stage 1 evidence PR #14 merged as `c72357a79cd9a694904d86dfad38df08f7d8a5df`.
Final-main CI [run 37061380496](https://github.com/gheroufosse/velolab/actions/runs/37061380496)
passed API checks and 99 PostgreSQL-enabled tests with zero skips and cleanup.
The Stage 2 [Intervals data contract](intervals-data-contract.md) is a **draft**
from public sources, not proof of account-specific behavior or sync completion.
Next: resolve range/completeness, historical corrections/deletions, explicit
clearing versus omission, units/date parity and timezone-change policy with
approved sanitized provider evidence before finalizing sync semantics. ADR-023
key encryption/backup/rotation is a gate before storing a real API key. No
sync/UI, personal API calls, production commands or publication in this draft.
The Stage 1 records below remain historical and are not Stage 2 authorization.

## Historical handoff — refresh/session slice (ADR-021)

The owner explicitly approved the seven-day fixed refresh policy and autonomous
Codex implementation, disposable PostgreSQL tests and independent review,
including minimal session-specific logout. `AGENTS.md` records that narrow
Stage 1 authorization, not standing permission for future domain work.

- The owner explicitly approved the Stage 1 auth PR and squash merge only after
  green GitHub CI. Auth PR [#13](https://github.com/gheroufosse/velolab/pull/13)
  was merged as `8c5268977878d75e0786a2b7d97552cf515640d2` on 2026-10-02.
  Its final head was `ddf0bf19fb25f43caded9d045d450c75298dd0c2` on
  `feat/auth-sessions` (based on `main` after docs PR #12). The earlier
  publication approval below applied only to the already-merged access-only
  slice. No production operations, Stage 2 work or unrelated PR publication
  were approved.
- Docs PR [#12](https://github.com/gheroufosse/velolab/pull/12), clarifying
  dashboard MVP and future data contracts, merged on 2026-10-02 at 20:14:49 UTC
  as `0e13d54aefd3754df9cd629d1a78df2c14cc270e`; its three checks passed.
  This did not publish or verify the separate auth slice.
- Login now sets an opaque HttpOnly/SameSite=Strict refresh cookie. Only token
  hashes are stored in new user-owned session/token tables, shipped with
  Alembic revision `c437ce183e2b`. Refresh rotates tokens under a parent-row lock;
  spent-token replay revokes only its session. Logout uses the same lock and
  leaves existing 600-second access JWTs valid until expiry.
- Configure exact `AUTH_TRUSTED_ORIGIN` and the public `AUTH_COOKIE_PATH`.
  Login/refresh/logout require that Origin and `X-Velolab-CSRF: 1`. HTTPS sets
  Secure; insecure cookies are allowed only for configured HTTP loopback origins.
  Missing/invalid configuration fails closed without blocking health/provisioning.
- Historical independent PostgreSQL 17 `./scripts/check.sh`: lint, formatting
  and types passed; **99 tests passed, zero skips**, including migration
  upgrade/downgrade and refresh/replay/logout concurrency tests. Three warnings:
  two dependency deprecations and an expected SQLAlchemy identity conflict.
- Current-session independent verification: non-database lint/format/types passed
  with 74 tests passed, 25 skipped and two dependency deprecations. A fresh
  dedicated PostgreSQL 17 full check passed lint/format/types and **99 tests,
  zero skips**, with three warnings (two dependency deprecations and the expected
  identity conflict). It covered migration upgrade/downgrade, persistence,
  provisioning concurrency, replay serialization and refresh/logout races.
  Loopback `127.0.0.1:5435`, tmpfs, restricted runner and cluster marker were
  confirmed; zero fixture databases and zero exact-project containers, networks
  or volumes remained after cleanup. These are local results, not GitHub CI or
  deployment sign-off.
- Historical verification used distinct generated test-only secrets, explicit
  `TEST_POSTGRES_*`, a unique Compose project, `--env-file /dev/null`, tmpfs and
  loopback-only `127.0.0.1:5435`. Zero fixture databases before teardown; zero
  exact-project containers, networks or volumes afterward.
- Independent Codex security/spec review approved with no material findings.
  Standards review requested narrowing the persistent authorization record
  (addressed) and noted the roughly 400-line PR guideline. This refresh/session
  PR stays cohesive because splitting origin/CSRF, cookie issuance, rotation,
  replay and session revocation into intermediate PRs risks publishing incomplete
  security contracts. Migration and critical concurrency tests stay in the PR.
- Final-head GitHub CI [run 37060700250](https://github.com/gheroufosse/velolab/actions/runs/37060700250)
  passed: API lint, formatting, types and **99 PostgreSQL-enabled tests, zero
  skips**; disposable PostgreSQL container and network were removed by the
  successful cleanup step. `changes` and stable `ci` passed; web was intentionally
  skipped by path filtering. First-head CI
  [run 37060343473](https://github.com/gheroufosse/velolab/actions/runs/37060343473)
  also passed before a documentation-only status update. Post-merge main CI
  [run 37060921206](https://github.com/gheroufosse/velolab/actions/runs/37060921206)
  passed API lint/format/types, 99 PostgreSQL-enabled tests, cleanup and `ci`.
- Stage 1 is merged and test-verified, not deployment-approved. No frontend/sync,
  production commands, real account provisioning or new dependencies. Full
  Compose/proxy checks remain unperformed. TLS and rate limiting are still
  required before network exposure.

**Learning notes.** A refresh cookie survives a page reload while an in-memory
access JWT does not; HttpOnly prevents JavaScript reading the refresh secret.
SHA-256 is appropriate here because tokens have high random entropy, unlike
human passwords. Locking a stable parent session serializes operations even as
individual tokens rotate. Strict replay revocation can force re-login after an
innocent concurrent refresh; Stage 3 must coordinate refresh requests across tabs.

**Next.** Scope Stage 2's intervals.icu client/sync together; do not start UI
early. Real-use migration/provisioning needs separate explicit approval.

Everything below is historical evidence for the earlier access-only slice;
old authorization/status statements do not apply to the refresh work above.

## Publication and merge handoff

The owner approved committing, publishing and squash-merging this slice after
GitHub CI passes. The complete diff received independent Codex Standards and
Spec reviews with no actionable findings; model edits are schema-neutral.

- Pull request: [#10](https://github.com/gheroufosse/velolab/pull/10).
- GitHub CI [run 36929823129](https://github.com/gheroufosse/velolab/actions/runs/36929823129)
  passed lint, formatting, types and **83 PostgreSQL-enabled tests, zero skips**
  on implementation commit `38195ff`. Disposable Compose cleanup also passed.
- Subsequent edits in this handoff record that verification only. The PR is the
  source of truth for its final head, merge state and commit.
- Stage 1 remains incomplete pending the separately scoped refresh/session work.
  No real account was provisioned and no production commands were performed.

The sections below are historical session snapshots. Their statements about
uncommitted work or unverified GitHub CI describe those earlier checkpoints,
not the publication status above.

## Autonomous hardening pass

The owner requested autonomous work with Codex-only subagents. Work stayed in
Stage 1 and preserved the existing uncommitted provisioning/auth slice; no
refresh/session policy, UI, production operation, commit or push was added.

- Fixed a reproduced credential-reflection bug: login validation previously
  compared the raw URL to `/auth/login`, so mounted or `root_path` deployments
  could fall back to FastAPI's default error response and echo passwords.
  `app.py` now identifies the matched login endpoint. HTTP regression tests
  cover direct, mounted and root-path requests, and preserve default validation
  for unrelated endpoints. Mounted/root-path cases failed before the fix.
- `.github/workflows/ci.yml` now starts the existing disposable PostgreSQL
  Compose definition under a run-specific project, uses explicit test-only
  settings and generated masked credentials, and cleans up even on failure.
  Bootstrap credentials are scoped to startup; tests receive only the runner
  password. Dependency installation enforces the lockfile.
- CI now includes check-script/test-infrastructure paths, grants the path-filter
  job PR read permission, and rejects failed/cancelled/unexpectedly skipped
  jobs through the stable `ci` gate.
- Codex review found no material issues in normal provisioning/auth paths and
  approved the credential-redaction fix. A deliberately corrupted non-ASCII
  stored password hash can produce HTTP 500; no normal write/input path creates
  that value, so no speculative corruption-handling patch was added.

### Latest verification

- Codex verifier ran the final `./scripts/check.sh` on fresh dedicated
  **PostgreSQL 17**: lint, format, type check and **83 tests passed, zero skips**.
  An earlier run caught a transient import-order issue during editing; the
  completed all-in-one rerun above supersedes it.
- Verifier confirmed loopback-only binding, tmpfs, cluster marker and restricted
  runner privileges; zero fixture databases and dedicated Docker resources
  remained after teardown. No root `.env` or production resources were accessed.
- Parent independently ran non-database `./scripts/check.sh`: lint/format/type
  checks passed, **62 passed / 21 intentionally skipped**.
- Parent parsed workflow YAML, checked shell syntax and exercised **576** CI-gate
  combinations; all passed. `git diff --check` passed. `actionlint` was unavailable
  and GitHub Actions itself has not run; local verification is not CI sign-off.
- Three warnings remain in the full suite: two framework deprecations and the
  intentional primary-key-collision warning. No dependencies were added.

The sections below record the original implementation handoff; historical test
counts are superseded by the latest verification above.

## Original implementation state

- Branch: `feat/auth-provisioning`. All work is local and uncommitted:
  **no commit, push or PR**. Inspect `git status --short` before resuming;
  existing dirty model/scaffold edits were preserved.
- The owner authorized assistant domain implementation for this session:
  private provisioning, database separation, login, 10-minute access JWT and one
  protected route. ADR-017's learning-first working agreement remains the
  default; explain the implementation with the owner rather than skipping
  learning because code now exists.
- Provisioning, login and the access-JWT/protected-route slice are implemented
  and verified on disposable PostgreSQL. No public registration, refresh,
  logout/revocation or UI. Stage 1 remains incomplete pending refresh endpoint
  and policy; local verification is not deployment sign-off.
- No real account was provisioned or production commands performed. No new
  schema revision or CI change was made. Migration connection plumbing changed
  to support explicit test-only connections.

## Implementation

- `provisioning.py`: strip/validate email without DNS checks, lowercase the
  whole normalized address, preserve 10–128-character Unicode passwords, hash
  with Argon2id and explicitly commit. PostgreSQL's `uq_users_email` constraint
  arbitrates duplicates/concurrent inserts; only that named unique violation
  becomes `DuplicateEmailError`. Rollback never overwrites an existing account.
- `provision_cli.py`: local interactive CLI, hidden password confirmation,
  terminal/fallback/cancellation safeguards and sanitized errors. No password
  argument. A lost connection during commit is reported as uncertain, not as
  proof that no write occurred.
- `auth.py`: JSON `POST /auth/login`; bearer-protected `GET /auth/me` returns
  only id/email. Generic credential failures, Argon2id dummy verification for
  missing accounts, no-store success responses and a current-user dependency.
- Tokens are HS256 only, exactly 600 seconds, with required `sub`, `iat`, `exp`,
  `token_use: access`, `iss: velolab-api` and `aud: velolab-api`. The subject must
  resolve to an existing user. Server-only `AUTH_JWT_SECRET` must be random and
  at least 32 UTF-8 bytes; no usable default exists. Unconfigured auth returns
  503 while health/private provisioning remain usable without a signing key.
- `app.py`, settings, dependency/lockfile and `.env.example` wire the slice;
  validation responses do not echo rejected credential inputs.
- Provisioning/CLI tests cover normalization, real hashing, boundaries,
  duplicate preservation, driver failures and sanitized error paths. Opt-in
  PostgreSQL tests cover migrated persistence, no-write failures, constraints
  and competing inserts. Fast auth HTTP tests use SQLite; two PostgreSQL auth
  cases reuse the existing disposable fixture for login/protected identity and
  invalid credentials.
- ADR-018 records provisioning; ADR-019 database separation; ADR-020 access-only
  auth. ADR-006 remains the target access-in-memory/refresh-cookie design.
  `apps/api/README.md` documents commands, auth configuration and limitations.

## Database separation and safety

- Exactly two data environments: real-use/production and a dedicated disposable
  real PostgreSQL test instance, not a third development/QA copy.
- `infra/test-db/` uses a separate Compose project, test-only credentials,
  tmpfs storage and a distinct loopback port (default `127.0.0.1:5435`). See its
  README for explicit startup/check/cleanup instructions.
- Opt-in integration tests require all five `TEST_POSTGRES_*` connection fields;
  no app `POSTGRES_*` or root `.env` fallback. Startup disables Compose's implicit
  root `.env` load. Fixture guards validate the test marker, role and PostgreSQL
  version before creating unique databases; cleanup drops them on exit.
- The restricted runner has CREATEDB, not superuser or server-file privileges.
  Bootstrap credentials are separate and never passed to tests. Alembic receives
  the fixture connection rather than resolving application settings.
- Automated data is fixture-generated. Optional sanitized dumps are manual-only;
  raw production backups are excluded from tests, agent context and the repo.
- There is deliberately **no enforced agent sandbox**. A host agent can
  technically access the real-use database; configuration/target guards prevent
  accidental misuse, not deliberate host access. Every real-use/production
  command requires explicit owner approval.
- SQLite is allowed for supplemental fast unit/HTTP tests, not as a substitute
  for PostgreSQL persistence, migration or concurrency evidence.

## Original verification reported

These runtime results were relayed by the implementation agents, not rerun by
this documentation worker.

- Final fresh dedicated **PostgreSQL 17** `./scripts/check.sh` run:
  **80 passed, zero skips**; lint, formatting and type checks passed.
- Migration/persistence, provisioning concurrency and both PostgreSQL auth
  cases (login/protected identity and invalid credentials) passed. This final
  run supersedes the earlier 78-pass run that had SQLite-only auth coverage.
- **Three warnings:** two existing framework deprecations and an intentional
  primary-key-collision warning in a constraint test.
- Zero leftover test databases; the test container and network were removed.
  Verification used ephemeral test secrets, with no production access or root
  `.env` reads. No production data was imported.
- Astra auth security review approved with **no material findings**. This is
  review of the access-only slice, not full deployment hardening/sign-off.
- Full application Compose/proxy verification remains unperformed; Stage 7
  deployment infrastructure is not complete.

## Next steps

1. Review/explain the code with the owner, including JWT claims/dependencies,
   secret configuration and constraint-based duplicate arbitration.
2. Only with explicit owner approval, confirm the real-use target, migrate it
   and provision an account interactively. No real account has been created.
3. Continue Stage 1 with a separately approved refresh/session slice. Refresh
   lifetime/rotation and logout/revocation policy remain **undefined**; do not
   invent them or mark the combined JWT/refresh roadmap item complete.
4. Add rate limiting and TLS before exposing authentication beyond loopback.
   No commit, push or PR has been performed.

No tokens or real secrets are recorded here.
