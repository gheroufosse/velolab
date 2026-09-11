# Working agreements

How work happens in this repository: branching, commits, pull requests, review
and CI. Written for someone who knows GitLab well and GitHub not at all.

---

## GitLab to GitHub translation

| GitLab | GitHub | Note |
|---|---|---|
| Merge Request (MR) | Pull Request (PR) | Same idea, different noun |
| `.gitlab-ci.yml` | `.github/workflows/*.yml` | Many workflow files instead of one |
| Pipeline | Workflow run | Triggered by `on:` events |
| Job / stage | Job / step | Jobs run in parallel unless `needs:` declares order |
| Runner | Runner | GitHub-hosted runners are free for public repos, metered for private |
| CI variables | Repository secrets and variables | Settings → Secrets and variables → Actions |
| Protected branch | Branch protection rule / ruleset | Rulesets are the modern form |
| Approvals required | Required reviewers | Solo repos usually set this to zero |
| `Closes #1` | `Closes #1` | Identical |
| Environments | Environments | Same concept, includes deployment protection rules |
| Issue boards | Projects | GitHub Projects v2 is the board equivalent |
| CODEOWNERS | CODEOWNERS | Same file, same syntax |

Three things that genuinely differ and catch GitLab users out:

- **Actions are composable units, not just scripts.** `uses: actions/checkout@v4`
  pulls in a published action. Much of a workflow is wiring existing actions
  together rather than writing shell.
- **Forks change permissions.** `pull_request` from a fork runs with a
  read-only token and no secrets. Not relevant while this repo is private and
  solo, but it explains a lot of workflow design seen in the wild.
- **Merge queue exists** and is unnecessary here.

---

## Branching

Trunk-based, short-lived branches off `main`.

```
main            always deployable, protected
feat/<slug>     new capability
fix/<slug>      bug fix
chore/<slug>    tooling, deps, config
docs/<slug>     documentation only
refactor/<slug> behaviour-preserving change
```

Rules:

- Branch from the latest `main`.
- One concern per branch. If a branch grows a second concern, split it.
- Rebase onto `main` rather than merging `main` in, so history stays linear.
- Delete the branch after merge (GitHub does this automatically once enabled).

---

## Commits

Conventional Commits. The type prefix is not decoration — it makes history
scannable and enables changelog generation later.

```
<type>(<scope>): <imperative summary>

<body: why, not what>

<footer: Closes #12>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `build`,
`ci`. Scopes used here: `api`, `web`, `infra`, `sync`, `auth`, `deps`.

```
feat(sync): backfill 24 months of activities on first run
fix(auth): reject refresh tokens after logout
docs: record ADR-009 chart library choice
```

Guidance:

- Summary in the imperative, under 72 characters, no trailing period.
- The body explains *why*. The diff already shows what.
- Commit early and often locally; tidy the branch before opening the PR.

---

## Pull requests

Every change reaches `main` through a PR. No direct pushes, including solo
work — the PR is where CI runs and where the reasoning gets recorded.

**Size.** Under roughly 400 changed lines. Large PRs get reviewed worse, not
better. Split by layer or by stage when a change grows.

**Draft first.** Open as a draft while working. Mark ready for review when CI
is green and self-review is done.

**Self-review is mandatory.** Read your own diff in the Files Changed tab
before requesting review. It catches debug statements, leftover comments and
accidental files with startling reliability.

**Description** follows the template in `.github/pull_request_template.md`:
what, why, how verified, and anything the reviewer should look at hardest.

**Merge strategy.** Squash merge. One logical change becomes one commit on
`main`, and the PR title becomes the commit subject — so the PR title must
itself be a valid Conventional Commit line.

**Linked issues.** `Closes #12` in the description so merging closes the issue.

---

## Review

While the project is solo, review is a self-review pass plus assistant review.
The assistant reviews for correctness, security and clarity, and explains
anything non-obvious rather than silently fixing it.

Review priorities, in order:

1. **Correctness** — does it do what the PR claims?
2. **Security** — secrets, auth boundaries, input validation, injection.
3. **Data integrity** — migrations, idempotency, partial-failure behaviour.
4. **Tests** — is the risky part actually covered?
5. **Clarity** — will this be readable in six months?
6. **Style** — last, and mostly automated away by `ruff` and formatters.

Comment conventions:

- `blocking:` must be resolved before merge.
- `suggestion:` worth considering, author decides.
- `question:` genuinely asking, not disguised criticism.
- `nit:` trivial, never blocking.

---

## CI

Runs on every PR and on pushes to `main`. A PR does not merge until it is
green.

Jobs:

- **api** — `ruff check`, `ruff format --check`, `ty`, `pytest`
- **web** — `tsc --noEmit`, `eslint`, `vitest`
- **build** — Docker images build cleanly (added at Stage 7)

Path filters keep frontend jobs from running on backend-only changes.

Failing CI is fixed, never bypassed. A flaky test is a bug with its own issue.

---

## Branch protection

Applied to `main`:

- Require a pull request before merging
- Require status checks to pass
- Require branches to be up to date before merging
- Require linear history
- Require conversation resolution before merging
- Block force pushes and deletion

Required approvals stay at zero while the project is solo, because GitHub will
not let you approve your own PR — a non-zero requirement would deadlock every
merge. It rises to one the moment a second person contributes.

---

## Issues

Issues track work worth remembering. Trivial work goes straight to a branch.

- Use the templates in `.github/ISSUE_TEMPLATE/`.
- Labels: `area:api`, `area:web`, `area:infra`, `type:bug`,
  `type:feature`, `type:chore`, `learning` (things worth understanding deeply
  rather than just shipping).
- Stage tracking lives in `docs/roadmap.md`, not in issues.

---

## Secrets

- Never commit `.env`, API keys, tokens or certificates.
- Local secrets in `.env`, which is git-ignored; `.env.example` documents the
  required keys with placeholder values.
- CI secrets in repository secrets, referenced as `${{ secrets.NAME }}`.
- A leaked key is rotated at intervals.icu immediately, not merely removed from
  the diff — anything pushed to a remote must be treated as compromised, even
  after a force push, because it may already be cached.

---

## Dependencies

- Dependabot opens grouped update PRs weekly.
- Security updates are reviewed promptly; routine bumps are batched.
- Lock files are committed: `uv.lock` and the frontend lock file.
