# Testing and Verification

Status: Active
Last reviewed: 2026-08-26

## Purpose

This document defines validation scope, commands, user-surface checks, and completion reporting for
code and documentation changes. The goal is to prove the changed behavior and its directly affected
boundaries, not the entire repository.

## Product baseline: fresh installation

For product behavior, prioritize a fresh installed `trace-agent` environment over the source
worktree.

- Use an isolated install location and a new `TRACE_AGENT_HOME`.
- Resolve `trace-agent` from the installed PATH; do not substitute `uv run` or an activated project
  virtual environment for installed-product proof.
- Verify first-run CLI exposure, defaults, state creation, service startup, and documented
  prerequisites from that environment.
- Treat worktree tests and `uv run` commands as development evidence for a candidate change, not as
  proof that a first-time installation works.
- For public-install claims, execute the actual published installer URL and ref. A local installer
  file or unpushed worktree does not prove the public command.

The fresh-install check must still remain focused on the changed product behavior. It does not
authorize unrelated end-to-end scenarios or the full test suite.

## Primary rule

Do not run the full test suite by default.

- Run the direct tests for the behavior being changed.
- Include only direct consumers of a changed contract, store, service, or runtime.
- Do not run tests for unrelated packages or user surfaces.
- When several areas are affected, select the relevant focused tests explicitly.
- Do not run a repository-wide command such as `uv run pytest` out of habit.
- Run the full test suite only when the user explicitly requests it.
- Do not repeat a passing check when neither its code nor inputs have changed.

Scope validation by behavior affected, not merely by files edited. A shared-contract change includes
its direct producers and consumers, but it does not justify unrelated tests.

## Selecting the validation scope

Before running a validation command:

1. State the changed user behavior or failure in one sentence.
2. Identify the package, service, store, or runtime that owns that behavior.
3. Inspect only direct callers, direct consumers, and siblings that share the same invariant.
4. Select the smallest test files and user surface that execute that boundary.
5. Exclude test files unrelated to the selected scope.

Do not choose the full suite because many files changed. Do not skip direct-consumer validation
merely because only one file changed.

## Test authoring gate

Do not create a test merely because production code changed. Add or modify a test only when it
protects the changed behavior or a directly affected owner contract.

Before adding a test:

1. Name the user behavior, failure, invariant, or public contract it protects.
2. Find the existing test file that owns that behavior.
3. Extend the existing scenario when it already exercises the same boundary.
4. Create a new test file only when no existing owner file is appropriate.
5. Confirm that the test fails for a credible regression in production behavior.

Do not create:

- orphan tests with no current production owner or consumer;
- tests unrelated to the requested change;
- duplicate happy-path tests that protect an invariant already covered;
- tests added only to increase coverage counts;
- hypothetical edge-case tests without a reachable boundary or observed risk;
- tests that read production source text and assert on strings, imports, or code shape;
- mock-only tests that never execute the owning production boundary;
- test-only production APIs, flags, branches, or dependency seams;
- snapshots or fixtures that freeze incidental output without protecting behavior; or
- broad integration tests when a focused owner-boundary test proves the change.

A test is orphaned when the production behavior it claims to protect has been removed, moved to a
different owner, or is no longer reachable. When changing ownership, move or rewrite the test with
the production boundary. Do not leave the old test behind as historical evidence.

Test names and placement must make the protected behavior and owner discoverable. If that mapping
cannot be stated clearly, do not add the test until the ownership question is resolved.

## Automated tests

Start with the nearest test file.

```bash
uv run pytest -q tests/<domain>/test_target.py
```

### Domain layout

The test tree mirrors the production owner or the user boundary it protects:

```text
tests/
├── agent/         # AgentSession, REPL, TUI, context, and tool-loop behavior
├── auth/          # OAuth and authorization URL contracts
├── automation/    # Campaign, queue, scheduler, and lease behavior
├── capture/       # Appium, capture workers, safety, and App Group artifacts
├── cli/           # Typer commands, compatibility, installer, and TraceRun CLI paths
├── composition/   # Composite worker and image composition
├── contracts/     # Capture, composite, and result contracts
├── generation/    # Scene planning, searched backgrounds, and one-shot generation
├── marketing/     # Cloudflare task contracts, bridge durability, account isolation, and loop proof
├── providers/     # Model catalog and provider request contracts
├── runtime/       # TraceRun execution, replay, store, and capture ports
├── search/        # Text/image search tools, providers, and settings
├── service/       # Service lifecycle, launchd, tunnel, and production worker wiring
├── web/           # FastAPI routes, sessions, queue, chat, and static browser behavior
└── workspace/     # Workspace identity, context, asset, and private-session stores
```

When two boundaries connect directly, name only the required files.

```bash
uv run pytest -q tests/agent/test_agent_sessions.py tests/web/test_web_sessions.py
```

```bash
uv run pytest -q tests/service/test_service.py tests/automation/test_workspace_queue.py
```

Use `-k` when one test name is sufficient.

```bash
uv run pytest -q tests/service/test_service.py -k worker
```

Do not run the following commands without an explicit user request.

```bash
uv run pytest
uv run pytest -q
```

## Static checks

Limit Ruff and BasedPyright to changed files and their directly affected files.

```bash
uv run ruff check path/to/changed.py tests/<domain>/test_changed.py
uv run ruff format --check path/to/changed.py tests/<domain>/test_changed.py
uv run basedpyright path/to/changed.py tests/<domain>/test_changed.py
```

Run repository-wide Ruff, formatting, or BasedPyright only when the user explicitly requests it or
when the tooling configuration being changed applies to the whole repository.
Ruff and BasedPyright target the package's lowest declared runtime, Python 3.13, so formatting and
type checking cannot silently introduce syntax that a supported fresh install cannot import.

## Selection by change type

| Changed area | Default automated scope | User-surface check |
| --- | --- | --- |
| `agent/`, TUI, REPL | Changed agent, session, or TUI test files | Changed input, cancellation, session, or rendering state in a real PTY |
| `auth/`, `providers/` | Changed provider and auth test files | Changed login, model request, or typed failure path |
| `tools/` | Direct tests for the tool and registry | Changed approval, path, provider, or subprocess boundary |
| `planning/`, `contracts/` | Changed contract/planner tests and direct consumers | One-shot path using the changed bundle |
| `runtime/`, TraceRun | Changed state, replay, or artifact tests | Changed capability or resume path |
| `workspace/` | Changed store tests and direct Web consumers | Changed scope, revision, rotation, or persistence path |
| `automation/` | Relevant campaign, producer, queue, scheduler, or worker tests | Changed start/stop/restart, enqueue, claim, lease, result, or review path |
| `web/` | Changed router, schema, command, and approval tests | Changed API and browser interaction against a running app, including slash commands and approval state |
| `service/`, `tunnel/` | Relevant service, worker, or tunnel tests | Changed lifecycle and health check with an isolated `TRACE_AGENT_HOME` |
| `capture/` | Changed adapter, worker, or provenance tests | Changed capture step with the current Appium, Simulator, and Trace build |
| `marketing/`, `cloudflare/` | Focused Python bridge/broker/native-capture, worker credential/LaunchAgent, Queue decoder, D1 migration/lease and hosted-workspace tests plus `cd cloudflare && npm run check`; parse the deployment workflow when it changes | Fresh-installed `trace-marketing simulate`, legacy `bridge --executor candidate-pipeline --once`, and broker `worker doctor/status/run --once`; for broker changes enroll with a one-time code, prove separate mode-`0600` credential and secret-free plist, race two workers for one task, reclaim one expired lease, prove heartbeat renewal stops at the one-hour cap, and keep heartbeat visible during capture; for hosted UI/context changes exercise root, sanitized worker status, public account create/switch/isolation, profile CRUD, immutable candidate profile snapshot, four-candidate morning/evening batch, structured feedback rule, D1 lease→Mac→callback→R2 flow, failed capture retry, submitted-candidate edit/delete, responsive UI, and one real Workers AI generation; after a qualifying `main` merge require the Actions migration/deploy plus workers.dev and `workspace.borca.ai` readbacks |
| `composition/` | Changed composer and artifact tests | Open the generated image and inspect the changed layer or output |
| Documentation | Links, paths, examples, stale references, and whitespace | Render only when layout matters |

Select only the rows that match the actual change.

## User-surface verification

When automated tests cannot prove user-visible behavior, use only the changed surface.

- For TUI changes, reproduce the changed state in a fresh PTY.
- For Web changes, run the changed API and browser interaction.
- For service changes, use an isolated state root and port for the changed lifecycle.
- For Appium changes, confirm the current Simulator and Trace installation, then run the changed
  capture path.
- For composition changes, inspect the image and provenance produced by the change.

Do not add unrelated end-to-end scenarios.

## Failure and revalidation

- When validation fails, relate the first actionable error to the current change.
- Report unrelated pre-existing failures separately; do not absorb them into the change scope.
- After a fix, rerun only the focused command that failed and the directly affected scope.
- Do not repeat a passing command at completion when its inputs have not changed.
- If a different cause is suspected, inspect source and call boundaries before widening the command.

## Completion report

Report:

- the changed behavior validated;
- each focused command executed;
- the user surface exercised directly;
- relevant validation not run and why; and
- pre-existing failures left outside the scope.

Do not report only that tests passed. State what changed and the scope that proves it.

## Document maintenance

Update this document in the same change when test locations, official commands, selection rules,
user-surface QA, or completion-evidence requirements change.
