# Probe handoff — owner-reported success

## Resume here

The owner is stopping for sleep. Next session, read this handoff and inspect
`git status` before acting; explain the evidence and decide the next step with
the owner. **Do not request another probe by default:** the owner reported
“it worked!” after rerunning the full probe with the User-Agent change.
Prefer Codex `openai-codex/gpt-6.1-sol` when delegating.

No new live account requests, database/production commands, commits, pushes,
PRs or branch cleanup are authorized. Scoped Velolab temporary artifacts were
cleaned with owner approval by the parent agent; unrelated `/tmp` was untouched.
Do not rely on those removed artifacts or treat this as broader cleanup approval.

## Implemented fixes and evidence limits

- The owner-approved athlete-ID policy now accepts nonempty opaque Unicode
  strings, preserving exact identity without stripping, normalization, case
  folding or numeric conversion. It rejects whitespace, Unicode categories
  `Cc`/`Cf`/`Cs`, slash, backslash, literal percent and exact `.`, `..` or `0`.
  `000` is accepted. IDs are encoded once as a single path segment with
  `quote(id, safe='', encoding='utf-8', errors='strict')`; query encoding is
  separate. Profile identity must match the original input exactly.
- The owner reported that changing only the athlete ID from alias `0` to an
  explicit ID made the same curl request succeed. Why the alias failed remains
  unknown; this does not establish that the provider does not support it.
- With the explicit ID, the owner reported successful `/activities.csv` curl
  access and a separate profile curl HTTP 200, while the old urllib probe's
  profile request returned HTTP 403.
- The probe now explicitly sends `User-Agent: Mozilla/5.0`. The owner then
  reported success after rerunning the full probe. **This is owner-reported
  live success, not agent verification of raw output or field/metric data.**
- User-Agent filtering or Cloudflare was not conclusively isolated: the proposed
  curl comparison using Python's User-Agent was never reported, and curl versus
  urllib differs beyond that header. Do not claim a proven root cause.

Probe safeguards remain: owner-run only, hidden ID/key prompts, at most eight
bounded fixed-host HTTPS GETs over seven complete athlete-local days, no
redirects/proxies/retries, database, app settings, root `.env` or raw files.
Output excludes credentials, IDs, dates, timezone and metric values; failures
use safe static labels and numeric HTTP status. No new agent live requests
were made for this handoff.

## Workspace and recorded checks

Current local branch: `feat/intervals-evidence-probe`. These changes remain
unpublished:

- `scripts/probe_intervals.py` — explicit opaque athlete IDs and User-Agent.
- `apps/api/tests/test_probe_intervals.py` — synthetic regression coverage.
- `docs/probe-intervals.md` — usage, policy and evidence/privacy limits; its
  pending-owner-run wording predates the success report above.

Recorded implementation verification (not rerun for this docs-only update):

- Coder check: lint, formatting and types passed; **170 tests passed,
  25 database tests skipped**.
- Independent User-Agent review: **96 focused tests passed**, targeted lint,
  formatting and types passed; approved with no blockers.
- Earlier opaque-ID patch also received independent review with **96 tests
  passed**. Synthetic checks are not live-provider or data-contract proof.

Preserve unrelated drafts in `docs/roadmap.md`, `docs/session-handoff.md`,
`docs/intervals-data-contract.md` and `docs/integration-key-encryption.md`.
This handoff also remains uncommitted. No publication or further cleanup approval
carries forward from earlier work.

## Remaining scope

Stage 2's data contract remains draft; sync is not implemented. Successful
probe execution does not verify field/metric values, units/date parity,
range/completeness guarantees, historical corrections/deletions, explicit
clearing versus omission or timezone-change semantics. Review evidence and
scope any follow-up with the owner before proceeding; do not infer contract
completion from the success report.
