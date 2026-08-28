# Dynamic Cloudflare Marketing Loop Contract

Status: Implemented for the pre-publication pipeline; live Threads publication remains disabled.

Candidate rollout status (2026-08-27): the feedback provenance/learning rules, generated-batch
validator, immediate image-retry guidance, and zero-worker fail-fast in this document are implemented
in the current branch but remain pending D1 migration, Worker deployment, hosted readback, and the
first real Mac canary.

The control-plane, hosted/local simulation, simulation-only legacy Queue bridge, D1 Mac-worker
broker, automatic workspace-review relay, portable worker enrollment, and deployment configuration
are implemented. Hosted native capture runs only through `trace-marketing worker run` or its
LaunchAgent service. Real Threads publication and live metrics readback remain unverified.
Simulation output must not be represented as a published post.

One login-free hosted review workspace is also implemented at the Worker root. It is deliberately
public, fixed to the configured public account, and separate from token-protected `/v1` operations.
Workers AI reads the selected D1 country/persona profile, matching packaged country context, and the
account instruction. D1 stores profiles, immutable candidate context snapshots, candidates, capture
tasks, worker identities, and review revisions. After the first non-revoked worker is enrolled, caption
approval leaves a task in the D1 broker and one healthy Mac claims its expiring lease without a
Cloudflare Queue token. The worker performs native Appium capture and returns a digest-backed PNG
for R2. When no non-revoked broker worker is registered, the image request fails before creating a
capture task; new hosted captures never fall back to a shared legacy Queue token. Live publication
remains outside this hosted surface. Image approval ends at `submitted` without an
external side effect. Hosted candidates remain editable and deletable in every state; an edit
invalidates prior approvals and image artifacts before returning to the first review gate.

## First milestone

The first milestone is a pipeline that can run and be changed safely. Generated content also passes
a bounded structural quality gate and uses only controlled, provenance-backed feedback rules; it
does not yet optimize against publication performance. The acceptance path is:

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
| hosted review workspace | Worker static assets, Workers AI, D1 broker, Mac worker, and R2 | the public URL needs no access ID; context/model/account selection stays data-driven while native capture crosses an explicit replaceable worker boundary |
| hosted context registry | packaged manifest plus account-scoped D1 profiles | countries extend through reviewed documents/profile data; team profiles change without Worker source edits; candidate snapshots retain provenance |
| hosted feedback learning | immutable reviewed-candidate snapshot plus controlled stage/tag rules | free-form reviewer text stays evidence for people, while only reviewed instructions enter later prompts or Mac creative direction |

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

## Hosted feedback learning and generation quality

Every automatically generated batch records a prompt version and SHA-256, model identifier, and the
exact active feedback-rule objects used for that batch. Each caption or image decision separately
records the reviewed candidate ID and revision, a bounded candidate snapshot and digest, the
generation provenance, rating, stage, tags, and optional reviewer note in the same D1 batch as its
candidate-state transition. Either both persist or neither does. Editing a candidate clears its
generation provenance because the edited revision no longer represents the recorded prompt.

The automatic rule boundary is intentionally narrow:

- the server owns a reviewed mapping from `(stage, rejection tag)` to a stable rule ID, quality
  dimension, and instruction;
- only rating 1–2 rejections count, and a rule activates only after the same stage and tag appear on
  three distinct candidate revisions for the same account/profile scope;
- every active controlled instruction reaches later candidate prompts; design and policy
  instructions are also snapshotted on the candidate and reach the Mac `creative_direction`;
- an image rejection immediately adds its stage-valid controlled tag instructions to that same
  candidate's next capture attempt, without waiting for the three-review learning threshold;
- free-form notes and the `기타` tag never become model instructions automatically;
- the public feedback summary returns aggregate tags and controlled rules, not reviewer notes; and
- approvals are retained as evidence but do not yet produce a positive-learning rule or mutate a
  persona/profile without a separate reviewed promotion step.

Generated batches must contain exactly four distinct topics and captions, two morning and two
evening slots, selected-profile reference IDs only, at least one non-duplicated principle per
candidate, and five to seven `HH:MM 제목` Trace items with a distinct schedule per candidate.
Schema-conforming AI output that violates these cross-candidate invariants is rejected and retried
once; it is never stored as an accepted candidate batch. Candidate persistence happens after the
format retry boundary, so a D1 failure never triggers another model call.

The hosted workspace is currently login-free, so reviewer identity and signal integrity are not
strong enough for automatic profile mutation. Adding authenticated reviewer attribution, rule
activation/deactivation controls, positive-signal learning, and experiment/metric attribution is a
separate policy slice.

## Isolation and instructions

- D1 contains account configuration and an opaque credential reference, never the credential value.
- One named Durable Object owns each account's private learned memory.
- An account may carry an opaque local `workspace_id`. The control plane forwards it to the selected worker but
  never uses it to read another workspace. It is compatibility metadata and does not select a hidden production executor.
- Shared instructions are immutable revisions. Every run records the selected revision and a digest
  of the resulting context snapshot in R2.
- Legacy Queue and callback credentials are injected by a supervisor or external secret command.
  Broker workers instead receive one independently revocable credential; only its hash is stored in
  D1, and its plaintext value lives in a separate local mode-`0600` file. Neither path includes a
  credential in task payloads, callbacks, artifacts, ordinary logs, or LaunchAgent plist.
- A callback is accepted only when task, run, account, and task kind all match the stored task.
- A brokered hosted callback validates its payload and then atomically reserves its callback ID and normalized result digest against the current worker
  and lease before any R2 or candidate mutation. Revocation/reassignment wins if it commits first;
  once reserved, changed content is rejected and lease expiry, acknowledgement retry, late execution
  start, and revocation wait for an identical retry to complete.
  Candidate revision, callback ID, PNG type, native provenance, byte limit, and SHA-256 must also
  match, so changed or stale callbacks cannot advance the candidate.

## Run and failure states

The happy path is:

```text
scheduled -> context_snapshot -> research -> planning -> candidate_generation
-> awaiting_candidate_approval -> candidates_approved -> capture_requested
-> capture_completed -> automatic_quality_check
-> awaiting_human_approval -> approved -> scheduled_for_publish -> publishing
-> published -> observing -> evaluated -> memory_committed -> completed
```

The Workflow contract keeps two human gates around capture and publication, while the current
login-free hosted workspace implements caption approval followed by native image review. Hosted
image approval ends at `submitted`; no installed bridge converts it into a publication decision. An
explicit control-plane rejection at either Workflow gate terminates that separate run.
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
5. planning and other pre-side-effect work keeps that lease renewable for at most one hour;
6. immediately before Appium, the worker records `execution_started_at` in D1, removes lease expiry,
   and only then writes its local execution marker;
7. an expired pre-execution lease may move to a healthy worker, but post-barrier work stays with its
   original owner until a callback or explicit operator revocation releases it; and
8. callback acceptance checks the current D1 owner before the existing candidate/digest boundary;
   `succeeded` and `unknown_side_effect` require the Appium execution barrier, while `failed` may
   terminate against the still-current worker/lease before the barrier; and
9. side-effect-free Codex planning retries twice, then returns `codex_plan_failed` without invoking
   Appium when all three attempts fail.

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

## Installed Mac executor

`trace-marketing worker run` and the LaunchAgent-only `trace-marketing worker service` are the only
installed production composition for hosted native capture. They claim D1 leases, invoke the official
Codex CLI for a schema-constrained wallpaper plan, cross the deterministic Appium boundary, and
deliver a provenance-checked callback. `trace-marketing bridge` and `bridge-service` remain
simulation-only compatibility commands; they never invoke the former custom candidate agent, Codex
OAuth/Responses stack, or native capture.

Tasks carrying `pipeline=hosted_workspace_capture_v1` preserve the approved caption, hypothesis,
validated textual reference IDs, creative direction, background intent, the full topic, complete
profile strings and Trace items in the typed Codex
input. They discover a booted or available iPhone
Simulator on each compatible Mac, build a typed marketing context from the immutable hosted
snapshot, run the production Codex-to-Appium capture path, and place the final PNG plus digest in
the durable callback outbox. A fixed UDID is optional, not part of enrollment.

## First operating target

The first operating target after credentials and Cloudflare resources exist is:

- migrate D1 and deploy the Worker;
- open `Mac 연결 관리`, enter the control-plane token, issue a ten-minute enrollment code, then on
  one prepared Mac run the displayed enroll and service commands for the generated
  `com.corca.trace-marketing-worker` LaunchAgent;
- open the login-free `workspace.borca.ai` workbench and generate four context-grounded candidates;
- confirm the batch has prompt/model/rule provenance and passes the cross-candidate quality gate;
- approve a caption, observe D1 lease → execution barrier → native Mac/Appium → verified R2 PNG, and approve the image;
- reach `submitted` while confirming no Threads or other publication call occurs; and
- inspect the D1 capture correlation row and R2 digest metadata.

The D1 broker worker is the only installed hosted native-capture path. A conditional D1 batch checks
for a non-revoked worker while it creates the task and advances the candidate revision; either both
records commit or neither does. With no registered worker the hosted image request returns `503`
without queueing. Legacy HTTP Queue pull remains a
simulation-only compatibility path and does not invoke Codex or Appium. A broker worker may run on any prepared Mac; its generated LaunchAgent
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
