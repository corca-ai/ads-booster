# Dynamic Cloudflare Marketing Loop Contract

Status: Draft

The control-plane, local simulation, queue bridge, and deployment configuration are implemented in
this branch. The bridge can also opt into the installed candidate pipeline added by PR #22: provider
generation writes reviewable candidates and the search-based image stage composes only
caption-approved candidates. A live external Queue pull, real Threads publication, and live metrics
readback remain unverified. Simulation output must not be represented as a published post.

## First milestone

The first milestone is a pipeline that can run and be changed safely. It does not optimize generated
content quality. The acceptance path is:

1. create a versioned shared instruction;
2. register a marketing account as data;
3. start a durable run for that account;
4. snapshot shared instructions plus account-private memory;
5. dispatch research, generation, capture, publication, and metrics tasks through Cloudflare Queue;
6. persist each task in the Mac inbox before acknowledging its Cloudflare lease;
7. pause for caption/candidate selection before image capture;
8. pause again for image/publication approval before publication;
9. observe, evaluate, commit account-private memory, and complete; and
10. prove the same state contract end-to-end in simulation mode.

## Architecture decisions

| Concept | Owner | Why it stays replaceable |
| --- | --- | --- |
| account registry, schedules, instruction revisions, run/task index | D1 | new accounts, countries, schedules, and instruction versions are rows, not deployments |
| one account agent | named Durable Object using `account_id` | actor-style isolation prevents one account from reading another account's learned memory |
| long-running loop and approval wait | Cloudflare Workflow | durable steps and buffered events survive process restarts and long human waits |
| task execution | hosted simulation, or Cloudflare Queue HTTP pull when `workspace_id` exists | the baseline loop needs no Mac daemon; installed workspace work crosses an explicit outbound-only boundary |
| Mac task durability | local SQLite inbox/outbox | queue acknowledgement follows durable insert; callbacks survive Mac restarts |
| artifacts | R2 in cloud, digest-backed local files on Mac | large payloads do not become workflow state and provenance remains inspectable |
| channel behavior | task-kind handler/adapter | simulation and live Threads behavior share a contract without sharing credentials |
| installed candidate journey | optional Mac executor selected at bridge startup | the default remains simulation; enabling the installed pipeline does not silently enable publication |

This combines ideas used by established harnesses: actor isolation from Akka/Orleans-style systems,
durable workflow steps from Temporal-style systems, inbox/outbox delivery from event-driven systems,
ports and adapters for channel replacement, and an explicit human-in-the-loop gate before an
irreversible action.

## Dynamic extension rules

The following changes are data-only:

- add or disable an account;
- change country, timezone, cadence, or instruction revision;
- rotate an opaque `credential_ref`;
- switch an account from live back to simulation; and
- publish a new shared instruction revision.

The following changes intentionally require code review and deployment:

- add a task kind or alter workflow ordering;
- add a channel adapter;
- change state-transition or retry semantics;
- change credential resolution; and
- loosen the human-approval boundary.

Dynamic does not mean arbitrary code loaded from D1. Account data can select a reviewed adapter, but
cannot inject executable code into a Worker or Mac process.

## Isolation and instructions

- D1 contains account configuration and an opaque credential reference, never the credential value.
- One named Durable Object owns each account's private learned memory.
- An account may carry an opaque local `workspace_id`. The control plane forwards it to the Mac but
  never uses it to read another workspace; the installed candidate executor requires the referenced
  workspace to already exist in the local Trace store.
- Shared instructions are immutable revisions. Every run records the selected revision and a digest
  of the resulting context snapshot in R2.
- Mac credential values are resolved outside this contract, preferably from Keychain. They are not
  included in task payloads, callbacks, artifacts, or logs.
- A callback is accepted only when task, run, account, and task kind all match the stored task.

## Run and failure states

The happy path is:

```text
scheduled -> context_snapshot -> research -> planning -> candidate_generation
-> awaiting_candidate_approval -> candidates_approved -> capture_requested
-> capture_completed -> automatic_quality_check
-> awaiting_human_approval -> approved -> scheduled_for_publish -> publishing
-> published -> observing -> evaluated -> memory_committed -> completed
```

The first approval selects caption-approved candidate IDs. With the installed candidate executor,
the operator approves captions in the existing Trace workspace before sending this event. The image
stage then moves those candidates to image review, and the second approval is sent only after the
operator approves the images in that workspace. Rejection at either gate terminates at `rejected`.
Verified failures terminate at `failed`. If a publication request times out after it may have reached
a channel, the run terminates at `unknown_side_effect`; it is not retried until an operator reads
back channel state.

## Task delivery contract

Every task has `schema_version`, `task_id`, `run_id`, `account_id`, `kind`, `idempotency_key`, `payload`,
`created_at`, and optional opaque `credential_ref`. For a simulation account without
`workspace_id`, the Workflow executes the task in Cloudflare, writes a labeled digest-backed result
artifact to R2, and records completion in D1. For a workspace-backed account, the Mac bridge:

1. pulls a batch with a visibility lease;
2. validates the versioned task;
3. inserts it into the local inbox using `task_id` plus a payload digest;
4. acknowledges only successfully persisted or identical duplicate tasks;
5. executes one claimed task;
6. writes a terminal result and callback to the local outbox in the same transaction; and
7. retries callback delivery independently until the control plane accepts its callback ID.

The Worker publishes the task envelope with Queue `contentType: "text"`. HTTP pull therefore returns
plain JSON text; the Mac accepts that canonical shape and legacy base64 JSON during rollout. Every
Workflow callback event type includes its `task_id`. Repeating an identical callback replays the
same buffered event, while a reused callback ID with a changed result is rejected.

Cloudflare task execution is at-least-once. Business side effects become effectively-once only when
the selected channel adapter supports an idempotency key or a conclusive readback.

## Human approval and live publication

Simulation mode may close the whole state loop but cannot impersonate a live post. Account writes
with `adapter_mode: "live"` are currently rejected, and the local simulation executor independently
refuses a non-simulation publication task. Live mode remains disabled until a capability probe
confirms, against the current official Threads API and the actual account permissions:

- creation and publication calls;
- stable publication identifiers;
- idempotency or conclusive readback behavior;
- metrics available at the selected sampling intervals; and
- operator-visible verification after publication.

The current product does not automatically delete, edit, or retry an ambiguous live publication.

Only one non-terminal run may exist for an account. Candidate approval, publication approval, and
task callback timeouts transition the run to `failed`. Observation settings are absolute minutes
since publication; the Workflow converts them to relative sleeps so `5,10,15` samples at minutes 5,
10, and 15 rather than 5, 15, and 30.

## Installed candidate executor

`trace-marketing bridge --executor candidate-pipeline` replaces only the candidate generation and
image capture task handlers. Research, publication, and metrics remain explicitly simulated. The
executor:

1. requires `workspace_id` in the account configuration;
2. adds the versioned shared instruction, account-private memory, and research output to the provider
   generation request;
3. writes generated candidates into the existing workspace candidate store;
4. accepts only candidate IDs selected at the first approval gate;
5. uses PR #22's provenance-checked search background and deterministic image composer; and
6. refuses publication unless every selected candidate has reached `submitted` through the existing
   image review gate.

The offline image stage still uses the packaged Trace component fixture. Candidate schedule items and
device time are recorded but are not rendered until the native Appium capture path replaces that
fixture.

## Two-hour operating target

The honest two-hour target after credentials and Cloudflare resources exist is:

- migrate D1 and deploy the Worker;
- create secrets and one instruction/account row without a `workspace_id`;
- start a run, approve it, and observe `completed`; and
- inspect the R2 context/task snapshots, D1 events, and account-private memory.

HTTP pull and the Mac bridge are required only when the account opts into a local `workspace_id`.

Enabling real Threads publication is a separate target because platform capability and permission
verification are external facts, not an implementation toggle.

## Merge-to-deploy contract

For the existing Cloudflare environment, a merge to `main` that changes `cloudflare/**` is the
production delivery trigger. A Pull Request changing the same paths runs the check job without
Cloudflare credentials. After merge, the repository workflow must, in order:

1. install dependencies from `package-lock.json`;
2. run the Worker syntax and state-machine checks;
3. render the ignored Wrangler config from repository variables;
4. apply all pending D1 migrations;
5. deploy the merged Worker revision; and
6. read back `{"ok":true}` from the configured health URL.

The job is serialized and does not cancel an in-flight deployment. Any failed check, migration,
deploy, or health readback leaves the GitHub job red and prevents a success claim. Human candidate
and publication approvals remain product gates rather than deployment chores. Starting the Mac
bridge and enabling a marketing account are runtime lifecycle choices and are not silently changed
by a code merge.
