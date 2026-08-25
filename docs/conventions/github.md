# GitHub Conventions

This document defines branch, commit, and Pull Request rules for `ads-booster`. Follow this workflow
for code changes and inspect the current branch and remote state before starting Git work.

## Core rules

- Use a work branch and Pull Request for normal changes; do not commit directly to `main`.
- Use full branch-type names such as `feature/`, `fix/`, and `hotfix/`. Do not use `feat/` as a
  branch prefix.
- Use `<type>: <message>` for commit messages.
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
feat: add campaign health check (#123)
fix: handle missing database url (#123)
refactor: simplify database options (#123)
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

Make each commit a meaningful unit with one intent. Before committing, inspect the changed files and
diff, and ensure no passwords, tokens, `.env` files, or other secrets are included.

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

### 2. Commit meaningful units and push the branch

```bash
git diff --check
git diff --stat
git add <intended-files>
git commit -m "<type>: <message>"
git push -u origin <branch-name>
```

### 3. Create a Draft Pull Request

```bash
gh pr create --draft --base main --title "<type>: <summary>" --body "<description>"
```

Include at least the following in the Pull Request body:

- the purpose and problem being solved;
- the main changes;
- focused verification commands and results; and
- migration, environment-variable, or deployment notes.

### 4. Review and merge

1. Check CI and focused local verification.
2. Confirm that changed files and the Pull Request description match the current head.
3. Mark the Pull Request `Ready for review` when review preparation is complete.
4. Apply feedback and push to the same branch.
5. Squash Merge after approval.
6. Synchronize local `main` immediately after the merge.

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
