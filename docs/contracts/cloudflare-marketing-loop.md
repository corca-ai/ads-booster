# Dynamic Cloudflare Marketing Loop Contract

Status: Implemented for the pre-publication pipeline; live Threads publication remains disabled.

The control-plane, hosted/local simulation, legacy Queue bridge, D1 Mac-worker broker, automatic
workspace-review relay, portable worker enrollment, and deployment configuration are implemented.
The legacy bridge can opt
into the installed candidate pipeline added by PR #22: provider generation writes reviewable
candidates and the search-based image stage composes only caption-approved candidates. Real Threads
publication and live metrics readback remain unverified. Simulation output must not be represented
as a published post.

One login-free hosted review workspace is also implemented at the Worker root. It is deliberately
public, fixed to the configured public account, and separate from token-protected `/v1` operations.
Workers AI reads the selected D1 country/persona profile, matching packaged country context, and the
account instruction. D1 stores profiles, immutable candidate context snapshots, candidates, capture
tasks, worker identities, and review revisions. After the first non-revoked worker is enrolled, caption
approval leaves a task in the D1 broker and one healthy Mac claims its expiring lease without a
Cloudflare Queue token. The worker performs native Appium capture and returns a digest-backed PNG
for R2. A deployment with no broker worker retains legacy Queue dispatch. Live publication remains
outside this hosted surface. Image approval ends at `submitted` without an
external side effect. Hosted candidates remain editable and deletable in every state; an edit
invalidates prior approvals and image artifacts before returning to the first review gate.

## First milestone

The first milestone is a pipeline that can run and be changed safely. It does not optimize generated
content quality. The acceptance path is:

1. create a versioned shared instruction;
2. register a marketing account as data;
3. start a durable run for that account;
4. snapshot shared instructions plus account-private memory;
5. execute labeled simulation tasks in Cloudflare, route workspace-backed Workflow tasks through
   the legacy Queue, or route hosted native capture through a D1 worker lease;
6. persist each remote task in the worker inbox before acknowledging its transport lease;
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
| task execution | hosted simulation, or Cloudflare Queue HTTP pull when `workspace_id` exists | the baseline loop needs no local daemon; installed workspace work crosses an explicit outbound-only boundary |
| hosted native capture | D1 registry and conditional expiring lease | workers can be added, drained, revoked, or replaced independently while one atomic update chooses the task owner |
| worker task/review durability | local SQLite inbox/outboxes | Queue or broker acknowledgement follows durable insert; callbacks and review approvals survive worker restarts |
| worker enrollment and secrets | one-time code, token hash in D1, separate mode-`0600` machine credential, secret-free LaunchAgent | any prepared Mac can join without a Cloudflare account/Queue token, person login, fixed UDID, or macOS Keychain binding |
| artifacts | R2 in cloud, digest-backed local worker files | large payloads do not become workflow state and provenance remains inspectable |
| channel behavior | task-kind handler/adapter | simulation and live Threads behavior share a contract without sharing credentials |
| installed candidate journey | optional local executor selected at bridge startup | the default remains simulation; enabling the installed pipeline does not silently enable publication |
| hosted review workspace | Worker static assets, Workers AI, D1 broker, Mac worker, and R2 | the public URL needs no access ID; context/model/account selection stays data-driven while native capture crosses an explicit replaceable worker boundary |
| hosted context registry | packaged manifest plus account-scoped D1 profiles | countries extend through reviewed documents/profile data; team profiles change without Worker source edits; candidate snapshots retain provenance |

This combines ideas used by established harnesses: actor isolation from Akka/Orleans-style systems,
durable workflow steps from Temporal-style systems, inbox/outbox delivery from event-driven systems,
ports and adapters for channel replacement, and an explicit human-in-the-loop gate before an
irreversible action.

## Dynamic extension rules

The following changes are data-only:

- add or disable an account;
- add, edit, or hide a profile for the configured public account;
- add a packaged country by registering its documents and starter profile JSON in the context manifest;
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
cannot inject executable code into a Worker or local bridge process.

A D1 profile alone cannot enable an unreviewed country. Hosted generation resolves global plus
country documents from the packaged manifest and fails with `409` when that country is absent. Each
candidate persists the complete selected profile snapshot; editing or hiding the profile later does
not rewrite already-generated evidence.

## Isolation and instructions

- D1 contains account configuration and an opaque credential reference, never the credential value.
- One named Durable Object owns each account's private learned memory.
- An account may carry an opaque local `workspace_id`. The control plane forwards it to the selected worker but
  never uses it to read another workspace; the installed candidate executor requires the referenced
  workspace to already exist in the local Trace store.
- Shared instructions are immutable revisions. Every run records the selected revision and a digest
  of the resulting context snapshot in R2.
- Legacy Queue and callback credentials are injected by a supervisor or external secret command.
  Broker workers instead receive one independently revocable credential; only its hash is stored in
  D1, and its plaintext value lives in a separate local mode-`0600` file. Neither path includes a
  credential in task payloads, callbacks, artifacts, ordinary logs, or LaunchAgent plist.
- A callback is accepted only when task, run, account, and task kind all match the stored task.
- A hosted capture callback additionally requires its assigned worker when brokered, candidate
  revision, callback ID, PNG type, native provenance, byte limit, and SHA-256 to match. Changed,
  revoked, or stale callbacks cannot advance the candidate.

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
the operator reviews all captions in the existing Trace workspace; the bridge then sends one durable
approval containing the accepted IDs, or rejects the run if none were accepted. The image stage
moves those candidates to image review, and the second approval is sent automatically only after the
operator approves every selected image in that workspace. Rejecting every caption terminates the
run at `rejected`. Rejecting an image returns that candidate to the composition stage and emits no
publication decision; an explicit control-plane rejection at either Workflow gate terminates the run.
Verified failures terminate at `failed`. If a publication request times out after it may have reached
a channel, the run terminates at `unknown_side_effect`; it is not retried until an operator reads
back channel state.

## Task delivery contract

Every task has `schema_version`, `task_id`, `run_id`, `account_id`, `kind`, `idempotency_key`, `payload`,
`created_at`, and optional opaque `credential_ref`. For a simulation account without
`workspace_id`, the Workflow executes the task in Cloudflare, writes a labeled digest-backed result
artifact to R2, and records completion in D1. For a workspace-backed account, the worker bridge:

1. pulls a batch with a visibility lease;
2. validates the versioned task;
3. inserts it into the local inbox using `task_id` plus a payload digest;
4. acknowledges only successfully persisted or identical duplicate tasks;
5. executes one claimed task;
6. writes a terminal result and callback to the local outbox in the same transaction; and
7. retries callback delivery independently until the control plane accepts its callback ID;
8. records run/workspace/candidate linkage after generation and capture; and
9. writes completed human-review decisions to a separate approval outbox before delivery.

The Worker publishes the task envelope with Queue `contentType: "text"`. HTTP pull therefore returns
plain JSON text; the worker accepts that canonical shape and legacy base64 JSON during rollout. Every
Workflow callback event type includes its `task_id`. Repeating an identical callback replays the
same buffered event, while a reused callback ID with a changed result is rejected. Workspace review
events use the Worker-token-only endpoint, an ID of `<run-id>:<phase>`, and a D1 receipt. An
identical delivered retry is acknowledged without sending a second Workflow event; changed content
under the same ID is rejected.

Hosted broker delivery reuses the same local durability contract with a different transport:

1. a worker-scoped bearer token authenticates heartbeat, claim, acknowledgement, and callback;
2. doctor status is refreshed on claim and degraded workers receive no task;
3. a conditional D1 update gives exactly one worker an initial two-minute lease;
4. successful local inbox insertion extends the accepted lease to fifteen minutes;
5. a dedicated heartbeat continues while Appium executes synchronously and renews the lease for at
   most one hour from its original claim;
6. retry or revocation clears ownership, and expiry lets a different healthy worker reclaim it; and
7. callback acceptance checks the current D1 owner before the existing candidate/digest boundary.

The public `/api/workers/status` projection contains only team-visible aliases, pools, aggregate
counts, and ready/busy/degraded/offline states. One-time code creation, full inventory, state changes,
and revocation remain under the `CONTROL_PLANE_TOKEN` `/v1` boundary. The workspace exposes those
protected actions through a separate Mac manager: an operator types the token into an unseeded
password field, the browser sends it only as a bearer header to `/v1`, and closing or locking the
manager clears the token and displayed enrollment code from memory. No browser persistence or public
admin projection is introduced.

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
This restriction is about already-published external content. The hosted D1 candidate and its R2
image can still be edited or deleted before any separate human publishing action.

Only one non-terminal run may exist for an account. Candidate approval, publication approval, and
task callback timeouts transition the run to `failed`. Observation settings are absolute minutes
since publication; the Workflow converts them to relative sleeps so `5,10,15` samples at minutes 5,
10, and 15 rather than 5, 15, and 30.

## Installed candidate executor

`trace-marketing bridge --executor candidate-pipeline`, or the supervisor-friendly `trace-marketing
bridge-configure` plus `trace-marketing bridge-service`, replaces only candidate generation and image
capture task handlers. Research, publication, and metrics remain explicitly simulated. The executor:

1. requires `workspace_id` in the account configuration;
2. adds the versioned shared instruction, account-private memory, and research output to the provider
   generation request;
3. writes generated candidates into the existing workspace candidate store;
4. accepts only candidate IDs selected at the first approval gate;
5. uses PR #22's provenance-checked search background and deterministic image composer; and
6. refuses publication unless every selected candidate has reached `submitted` through the existing
   image review gate.

Tasks carrying `pipeline=hosted_workspace_capture_v1` are routed ahead of the legacy local-candidate
handler. `trace-marketing worker run` composes only this hosted executor behind the D1 broker; it
does not poll or deliver the unrelated Workflow review contract. The executor discovers a booted or
available iPhone Simulator on each compatible Mac,
builds a typed marketing context from the immutable hosted snapshot, runs the production Appium
capture/composition path, and places the final PNG plus digest in the durable callback outbox. A
fixed UDID is optional, not part of enrollment. The legacy local candidate image handler remains an
offline fixture path for its existing workspace journey and is never represented as native.

## First operating target

The first operating target after credentials and Cloudflare resources exist is:

- migrate D1 and deploy the Worker;
- open `Mac 연결 관리`, enter the control-plane token, issue a ten-minute enrollment code, then on
  one prepared Mac run the displayed enroll and service commands for the generated
  `com.corca.trace-marketing-worker` LaunchAgent;
- open the login-free `workspace.borca.ai` workbench and generate four context-grounded candidates;
- approve a caption, observe D1 lease → native Mac/Appium → verified R2 PNG, and approve the image;
- reach `submitted` while confirming no Threads or other publication call occurs; and
- inspect the D1 capture correlation row and R2 digest metadata.

The D1 broker worker is the primary hosted native-capture path after enrollment. Legacy HTTP Queue
pull remains required for a control-plane account that opts into a local `workspace_id` and as a
pre-enrollment rollback path. A broker worker may run on any prepared Mac; its generated LaunchAgent
owns restart and its separate machine credential does not grant Cloudflare account or Queue access.

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
6. read back `{"ok":true}` from the configured health URL; and
7. verify the root workspace has no access-ID form, `/api/auth/session` identifies the public account,
   `/api/context-profiles` returns a default profile, and `/api/workers/status` reads the migrated
   registry without exposing a credential; and
8. read back `https://workspace.borca.ai/health` through the custom domain.

The job is serialized and does not cancel an in-flight deployment. Any failed check, migration,
deploy, or health readback leaves the GitHub job red and prevents a success claim. Human candidate
and publication approvals remain product gates rather than deployment chores. Enrolling, preparing,
or replacing a physical Mac remains an explicit infrastructure action because GitHub cannot install
Xcode, the Trace Debug build, or a revocable local credential on an arbitrary team computer.
