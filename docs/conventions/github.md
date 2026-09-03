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
- Squash Merge approved Pull Requests.
- After a change lands on `main`, update the tag and GitHub Release for that commit.

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

Do not paste a commit hash list or a commit-by-commit diary into the PR body. The commit history
should remain the atomic implementation record; the PR description explains the delivered behavior,
evidence, and operational impact.

### 4. Review and merge

1. Check CI and focused local verification.
2. Confirm that changed files and the Pull Request description match the current head.
3. Mark the Pull Request `Ready for review` when review preparation is complete.
4. Apply feedback and push to the same branch.
5. Squash Merge after approval.
6. Synchronize local `main` immediately after the merge.
7. After the merged work and any required release steps are verified, close each completed owning
   issue with a short PR/release reference. Do not close an issue when it still has deferred scope.

```bash
git switch main
git pull --ff-only origin main
```

## Tag and GitHub Release after `main` changes

A change on `main` is not released until its tag and GitHub Release are updated. This is a mandatory
post-merge step, not an optional follow-up. Target the actual remote `main` commit, not another
branch or an arbitrary local HEAD. Never move or overwrite an existing published tag; choose the
next version, and update package metadata/lockfiles in an issue-linked commit before tagging when
the release version changes.

1. Determine the next version and release notes from the final `main` change set.
2. Read remote `main` again and confirm the target SHA.
3. Create an annotated `v<version>` tag on that SHA and push it.
4. Create a GitHub Release for the same new tag.
5. Confirm that the remote tag's peeled SHA matches the target `origin/main` SHA and that the
   GitHub Release uses the intended `v<version>` tag.
6. Only then report the main change as released.

```bash
git fetch origin main --tags
git rev-parse origin/main
git tag -a v<version> <main-sha> -m "v<version>"
git push origin v<version>
# When creating a GitHub Release
gh release create v<version> --target <main-sha> --title "v<version>" --notes-file <release-notes-file>
# When the GitHub Release already exists
gh release edit v<version> --title "v<version>" --notes-file <release-notes-file>
git ls-remote --tags origin refs/tags/v<version> refs/tags/v<version>^{}
gh release view v<version> --json tagName,targetCommitish,url
```

After the required tag/release verification succeeds, close the completed issue(s):

```bash
gh issue close <issue-number> --comment "Completed in PR #<pr-number> and release v<version>."
```

Pushing tags and creating or editing GitHub Releases mutate external state. Perform them only when
the user explicitly requests a `main` merge or release. Do not claim release completion before
verification.

## Pre-merge checklist

- [ ] The current branch is the intended Pull Request head branch.
- [ ] `git status --short` and `git diff --stat` show the intended scope.
- [ ] `git diff --check` passes.
- [ ] Focused tests and checks for the changed behavior pass.
- [ ] The Pull Request is Draft and its description matches the actual change.
- [ ] No secrets or unrelated changes are included.
- [ ] The Pull Request is ready for Squash Merge after approval.
- [ ] After landing on `main`, the new tag and GitHub Release point to the same `main` commit.
