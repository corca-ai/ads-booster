# GitHub Conventions

This document defines branch, commit, and Pull Request rules for `ads-booster`. Follow this workflow
for code changes and inspect the current branch and remote state before starting Git work.

## Core rules

- Use a work branch and Pull Request for normal changes; do not commit directly to `main`.
- Use full branch-type names such as `feature/`, `fix/`, and `hotfix/`. Do not use `feat/` as a
  branch prefix.
- Use `<type>: <specific-responsibility> (#<issue-number>)` for commit messages.
- Create every Pull Request as Draft, then mark it `Ready for review` when review preparation is
  complete.
- Use a Merge commit by default to preserve individual responsibility commits on `main`, including
  for `feature/`, `fix/`, and `hotfix/` branches deleted after merge. Use Squash Merge only when the
  user explicitly requests it for that Pull Request.
- After a change lands on `main`, verify the applicable server update path
  described in [Post-merge verification](#post-merge-verification).

## Authorization and existing work

Commit only when the user requests it. Push, create or merge Pull Requests, and change GitHub state
only within the user's explicit request. Reading an existing Issue is preparation; creating or closing
one changes GitHub state. A request to edit local files does not authorize publication.

Preserve existing dirty files, untracked files and `tasks/` records. Do not stash, restore, delete or
mix them into the requested change without authorization. Stage only the requested paths or hunks.

## Branches

### Default branch

- `main` is the integration and release baseline.
- Do not commit normal work directly to `main`.
- Isolate urgent production fixes on a `hotfix/` branch.

### Work branch names

```text
<type>/<short-description>
```

| Prefix | Purpose | Example |
| --- | --- | --- |
| `feature/` | Add a feature | `feature/add-campaign-health-check` |
| `fix/` | Fix a normal defect | `fix/handle-missing-database-url` |
| `hotfix/` | Fix an urgent production incident | `hotfix/restore-health-endpoint` |

Keep branch names short and descriptive. Use lowercase words separated by hyphens. Do not mix
unrelated changes on one branch.

## Commit messages

### Format

```text
<type>: <message> (#<issue-number>)
```

Every implementation, bug fix, test, or repository-policy change starts from a GitHub Issue. Every
commit must include its issue reference in the subject using `(#<issue-number>)`; one logical commit
maps to one issue, and each commit subject may contain exactly one issue reference. Never list
multiple issue numbers in one commit message; split the work into separate logical commits instead.

Examples:

```text
feat: add campaign status enum (#123)
fix: reject missing database url in config parser (#123)
refactor: extract database options builder (#123)
docs: add GitHub conventions (#123)
test: cover health endpoint failure (#123)
chore: update development dependencies (#123)
```

### Types

- `feat`: add a feature
- `fix`: fix a defect
- `refactor`: improve structure without changing behavior
- `docs`: change documentation
- `test`: add or update tests
- `chore`: maintain build tooling, dependencies, or repository configuration

### Commit boundaries

Split commits by the smallest responsibility that a reviewer can understand, verify, and revert on its
own. An Issue or feature is a delivery scope, not a commit boundary. Subjects such as `develop image
generation feature` or `support candidate deletion` are too broad when the change contains separable
parts.

Separate these responsibilities by default, even when they belong to the same feature or Issue:

- enum, constant, or shared type additions;
- request, response, event, or persistence contracts;
- controller, route, command, or entry-point wiring;
- service or domain behavior;
- repository, migration, or external-adapter behavior;
- test-only changes for one behavior boundary;
- documentation and repository policy.

For example, do not commit an entire candidate-deletion feature as one `feat` commit. Prefer a sequence
like this, using the same owning Issue when appropriate:

```text
feat: add candidate deletion status enum (#123)
feat: define candidate deletion request contract (#123)
feat: implement candidate deletion service (#123)
feat: add candidate deletion controller route (#123)
test: cover candidate deletion authorization (#123)
docs: document candidate deletion API (#123)
```

A file boundary alone does not make a commit atomic. One responsibility may require several files, and
one file may contain several independently committable hunks. Keep multiple files together only when
splitting them would leave an invalid build, an incomplete single responsibility, or an implementation
without the direct regression test needed to prove it. This exception overrides the category split.
Order dependent commits from foundation to consumer so every commit builds and retains its focused
verification; for example, enum, contract, implementation, then controller wiring.

Before every commit:

1. State the exact responsibility in the commit subject. Name the enum, contract, controller, behavior,
   test boundary, or document instead of the whole feature.
2. Stage only that responsibility with explicit paths or `git add -p`. Do not use `git add .` or
   `git add -A` to assemble a commit.
3. Read the complete staged diff with `git diff --cached` and run `git diff --cached --check`.
4. Split again if the staged diff contains another independently reviewable or revertible change.
5. Confirm no passwords, tokens, `.env` files, generated runtime state, or unrelated user changes are
   staged.

## Workflow

### 0. Create or identify the issue

Before implementation or staging, create or identify the GitHub Issue that owns the change and record
its number. Use the issue number in every related commit subject, for example:

```text
fix: wait for launchd teardown before workspace restart (#123)
```

### 1. Start from current `main`

```bash
git status --short
git switch main
git pull --ff-only origin main
git switch -c feature/<short-description>
```

If the worktree already contains changes, inspect them first. Do not overwrite or mix other work.

### 2. Commit fine-grained responsibilities and push the branch

```bash
git diff --check
git diff --stat
git add <intended-paths> # or: git add -p
git diff --cached --check
git diff --cached
git commit -m "<type>: <specific-responsibility> (#<issue-number>)"
git push -u origin <branch-name>
```

### 3. Create a Draft Pull Request

```bash
gh pr create --draft --base main --title "<type>: <summary>" --body "<description>"
```

The PR title is a short change summary; an issue number is optional in the title. Link the owning
issue(s) in the body instead. The body must be concrete enough for a reviewer to understand the
user-visible flow and verify the change without reading the commit history. Include:

- `## Issues`: links to the owning issue(s), with one primary issue identified;
- `## User flow`: the before/after behavior and the exact path a user takes;
- `## Implementation`: changed contracts, routes, state boundaries, and important file areas;
- `## Security and limits`: authorization, secret handling, migrations, compatibility, and known
  limitations;
- `## Verification`: exact focused commands and observed results;
- `## Deployment`: environment variables, data migrations, release/tag impact, and rollback notes.

#### Write for a first-time reviewer

Assume the reviewer has not read the originating conversation or worked on this feature before.

- Lead with the problem and the user-visible outcome. Give a concrete usage example and explain
  what happens before and after the change before introducing implementation details.
- Explain unfamiliar domain terms, abbreviations, and component names when they first appear.
  Use a short glossary when several terms are needed to understand the flow.
- Include Mermaid flowcharts or sequence diagrams for multi-stage or cross-component behavior.
  Show the real participants, data movement, and relevant authority or asynchronous boundaries.
  Split distinct flows, such as storing knowledge and using it in generation, into separate diagrams
  instead of one oversized graph. Pure copy or metadata changes do not require a diagram.
- Render and inspect Mermaid diagrams before publishing. Use short labels and deliberate line
  breaks so text remains readable; verify that the diagram agrees with the implemented flow.
- For changes spanning multiple areas, provide an ordered review guide: the question each area
  answers and links to its main code entry points. Follow the user or data flow, not commit order.
- Separate local tests, installed-product checks, actual provider calls, CI, and deployed external
  behavior. State the observed result and verification scope; keep failed, pending, and unverified
  items visible. A passing subset or local run must not imply that CI or deployment passed.
- Keep the main narrative focused on behavior and decisions. Put lengthy reproduction commands
  and supporting logs in collapsible details, while leaving key results and blockers visible.

Do not paste a commit hash list or a commit-by-commit diary into the PR body. The commit history
should remain the atomic implementation record; the PR description explains the delivered behavior,
evidence, and operational impact.

### 4. Review and merge

1. Check CI and focused local verification.
2. Confirm that changed files and the Pull Request description match the current head.
3. Mark the Pull Request `Ready for review` when review preparation is complete.
4. Apply feedback and push to the same branch.
5. After approval, use **Create a merge commit** on GitHub or `gh pr merge <number> --merge`.
   - Preserve the individual responsibility commits regardless of branch prefix or whether the
     source branch will be deleted after merge.
   - Use Squash Merge only when the user explicitly requests it for this Pull Request. General
     permission to merge does not authorize squashing.
   - If repository settings block Merge commits, report the blocker; do not silently switch to
     Squash Merge or Rebase Merge.
6. Synchronize local `main` immediately after the merge.
7. After the merged work and any required release steps are verified, close each completed owning
   issue with a short PR/release reference. Do not close an issue when it still has deferred scope.

```bash
git switch main
git pull --ff-only origin main
```

## Post-merge verification

A merged commit and an installed server update are separate results. On-prem updates follow the
exact `main` SHA's successful `Verify on-prem agent` check and the installed updater. Use the
[server operation guide](../operations/agent-server/slack-launch-guide.md) to verify the installed
SHA; passing CI alone does not prove activation.

Only publish releases within an explicit release request. A permission to merge does not authorize
rewriting existing published tags or release history. Preserve package metadata compatibility unless
renaming or migration is explicitly part of the change.

After authorized delivery and its verification, close the owning Issue with the PR reference and
observed installed result. Do not close deferred scope.

## Pre-merge checklist

- [ ] The current branch is the intended Pull Request head branch.
- [ ] `git status --short` and `git diff --stat` show the intended scope.
- [ ] `git diff --check` passes.
- [ ] Focused tests and checks for the changed behavior pass.
- [ ] The Pull Request is Ready for review, approved, and its description matches the actual change.
- [ ] No secrets or unrelated changes are included.
- [ ] Merge commit is selected to preserve individual commits, or the user explicitly requested
  Squash Merge for this Pull Request.
- [ ] The applicable post-merge server verification path is identified.

## On-prem package releases

`Release on-prem agent` publishes a new stable `vX.Y.Z` version after the exact main SHA passes
`Verify on-prem agent` on push. Release intent is the reviewed version bump in `pyproject.toml`
and `uv.lock`. Unchanged published versions are skipped; published tags/assets are never rewritten.
Only the current main SHA is eligible. A stale completion defers to the newer main check.

The workflow builds the wheel and sdist, smoke-tests the wheel in a fresh environment, and uploads
both with `SOURCE_COMMIT` and `SHA256SUMS` to a draft before publication. If upload/publication fails,
rerun the workflow on the same main SHA; only a draft with that exact target can resume. A conflicting
tag/draft fails closed. If main advanced after a failed draft, inspect it and resolve the draft
explicitly before retrying; do not retarget published history.

For recovery, run `gh workflow run release-on-prem.yml --ref main`; it still requires successful
push verification of that exact SHA. The workflow has no server credentials and does not activate
an installation. Verify server health separately through the post-merge operator path above.
