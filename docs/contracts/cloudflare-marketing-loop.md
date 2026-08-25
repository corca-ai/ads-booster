# Dynamic Cloudflare Marketing Loop Contract

Status: Draft

The control-plane, local simulation, queue bridge, and deployment configuration are implemented in
this branch. A production Cloudflare deployment, real Threads publication, and live metrics readback
remain unverified. Simulation output must not be represented as a published post.

## First milestone

The first milestone is a pipeline that can run and be changed safely. It does not optimize generated
content quality. The acceptance path is:

1. create a versioned shared instruction;
2. register a marketing account as data;
3. start a durable run for that account;
4. snapshot shared instructions plus account-private memory;
5. dispatch research, generation, capture, publication, and metrics tasks through Cloudflare Queue;
6. persist each task in the Mac inbox before acknowledging its Cloudflare lease;
7. pause for human approval before publication;
8. observe, evaluate, commit account-private memory, and complete; and
9. prove the same state contract end-to-end in simulation mode.

## Architecture decisions

| Concept | Owner | Why it stays replaceable |
| --- | --- | --- |
| account registry, schedules, instruction revisions, run/task index | D1 | new accounts, countries, schedules, and instruction versions are rows, not deployments |
| one account agent | named Durable Object using `account_id` | actor-style isolation prevents one account from reading another account's learned memory |
| long-running loop and approval wait | Cloudflare Workflow | durable steps and buffered events survive process restarts and long human waits |
| Mac task transport | Cloudflare Queue HTTP pull | the Mac initiates outbound traffic; no permanent inbound tunnel is required |
| task durability | local SQLite inbox/outbox | queue acknowledgement follows durable insert; callbacks survive Mac restarts |
| artifacts | R2 in cloud, digest-backed local files on Mac | large payloads do not become workflow state and provenance remains inspectable |
| channel behavior | task-kind handler/adapter | simulation and live Threads behavior share a contract without sharing credentials |

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
- Shared instructions are immutable revisions. Every run records the selected revision and a digest
  of the resulting context snapshot in R2.
- Mac credential values are resolved outside this contract, preferably from Keychain. They are not
  included in task payloads, callbacks, artifacts, or logs.
- A callback is accepted only when task, run, account, and task kind all match the stored task.

## Run and failure states

The happy path is:

```text
scheduled -> context_snapshot -> research -> planning -> candidate_generation
-> capture_requested -> capture_completed -> automatic_quality_check
-> awaiting_human_approval -> approved -> scheduled_for_publish -> publishing
-> published -> observing -> evaluated -> memory_committed -> completed
```

Rejection terminates at `rejected`. Verified failures terminate at `failed`. If a publication request
times out after it may have reached a channel, the run terminates at `unknown_side_effect`; it is not
retried until an operator reads back channel state.

## Task delivery contract

Every task has `schema_version`, `task_id`, `run_id`, `account_id`, `kind`, `idempotency_key`, `payload`,
`created_at`, and optional opaque `credential_ref`. The Mac bridge:

1. pulls a batch with a visibility lease;
2. validates the versioned task;
3. inserts it into the local inbox using `task_id` plus a payload digest;
4. acknowledges only successfully persisted or identical duplicate tasks;
5. executes one claimed task;
6. writes a terminal result and callback to the local outbox in the same transaction; and
7. retries callback delivery independently until the control plane accepts its callback ID.

Cloudflare task execution is at-least-once. Business side effects become effectively-once only when
the selected channel adapter supports an idempotency key or a conclusive readback.

## Human approval and live publication

Simulation mode may close the whole state loop but cannot impersonate a live post. Live mode remains
disabled until a capability probe confirms, against the current official Threads API and the actual
account permissions:

- creation and publication calls;
- stable publication identifiers;
- idempotency or conclusive readback behavior;
- metrics available at the selected sampling intervals; and
- operator-visible verification after publication.

The current product does not automatically delete, edit, or retry an ambiguous live publication.

## Two-hour operating target

The honest two-hour target after credentials and Cloudflare resources exist is:

- migrate D1 and deploy the Worker;
- enable HTTP pull on the task queue;
- create secrets and one instruction/account row;
- start the Mac bridge with its simulation executor;
- start a run, approve it, and observe `completed`; and
- inspect the R2 context snapshot, D1 events, local inbox/outbox, and account-private memory.

Enabling real Threads publication is a separate target because platform capability and permission
verification are external facts, not an implementation toggle.
