# System Architecture

Status: Active
Last reviewed: 2026-09-02

## Runtime boundary

Cloudflare owns hosted candidates, D1 leases/callback acceptance, R2 storage, review state, Threads
OAuth/token encryption, next-slot publication, and engagement polling. An enrolled Mac owns local
durability, admission, one official Codex CLI process, Appium side effects, and native export
validation. A fresh managed `trace-marketing` install and deployed workspace are product evidence; a
checkout is development evidence only.

```mermaid
flowchart LR
  Candidate[Hosted candidate] --> Lease[D1 lease]
  Lease --> Inbox[SQLite inbox]
  Inbox --> Prepare[Context background readiness]
  Prepare --> EventLog[Sanitized task events]
  Prepare --> Admit[Local admission]
  Admit --> Barrier[D1 execution barrier]
  Barrier --> EventLog
  Barrier --> Calendar[EventKit seed and verify]
  Calendar --> Codex[One codex exec]
  Codex --> Export[Trace PNG and manifest]
  Export --> Validate[Independent validation and Calendar cleanup]
  Validate --> Callback[Durable callback]
  Callback --> EventLog
  Callback --> Store[R2 and D1]
  EventLog --> WorkspaceLog[Workspace execution timeline]
  Store --> Review[Human review]
  Review --> Decision[Threads schedule or OFF cancellation]
  Decision --> Publish[Cloudflare publish barrier and readback]
  Publish --> Poll[Metrics and top-level replies]
```

## Request-time capture

1. The workspace may create a `hosted_workspace_generation_v1` task for automatic candidate drafts.
   A compatible Mac executes exactly one schema-constrained official Codex CLI turn and returns its
   result through the same durable callback path; it has no Agent loop or plan object.
2. The workspace creates a `hosted_workspace_capture_v1` task from an approved candidate.
3. A ready Mac claims the D1 lease and inserts the task in its local inbox before remote ack.
   Cloudflare and the worker append bounded, idempotent lifecycle events for that task. Each event
   contains only task/worker identity, task kind, a fixed event type, timestamp, and optional
   machine-readable failure code.
4. Safe capture preparation resolves a Simulator, validates country locale/time zone, searches
   allowlisted stock domains for tall wallpaper photos, deterministically selects the usable
   portrait closest to the lock-screen aspect ratio, binds its provenance and digest, creates a
   mode-0700 request root, and checks readiness. These failures have not started Appium.
5. Local SQLite records the immutable admission digest/nonce. The worker then records the D1
   barrier. If it cannot, it does not start native work.
6. After the barrier, the worker writes a digest-bound Calendar request into the Trace App Group and
   launches the DEBUG Trace EventKit helper. The helper creates or reuses only the
   `trace-<request-id>` calendar, writes the requested events, re-reads their exact titles and times,
   and returns its calendar identifier and count. Every temporary event carries the request digest
   as its ownership marker. A title collision without that marker is rejected, and a failure after
   EventKit commit rolls the new calendar back before returning failure. This helper launch adds
   `-traceMarketingCalendarAutomation`; it is not the final editor process.
7. The worker writes `trace.codex-appium-job.v2` and runs one ephemeral official `codex exec` with
   user/project configuration disabled and the `trace-appium` permission profile. Commands can use
   the request workspace and the allowlisted loopback Appium endpoint, but cannot read home secrets
   or reach external hosts. The contract supplies context, prepared background, device/UDID, Trace
   bundle, endpoint, locale/time zone, digest/nonce, and `trace-<request-id>` calendar namespace.
   The final bound Trace launch opens the wallpaper editor directly. Codex owns editor observation,
   layout, component settings, preview inspection, and Save. It does not enter Trace Orb/Quick Setup,
   open Shortcuts or Calendar, or create, edit, or delete Calendar data. The worker owns deterministic
   data preparation, Simulator preparation, collection, and cleanup.
   Before Save, Codex publishes its active wallpaper-editor state. The worker independently checks
   the Trace editor identifier, every requested title, and the live Trace process arguments in the
   same Appium session. A bundle-only terminate/activate cycle loses the immutable export binding,
   so the worker rejects that Ready marker before Save and permits one replacement Trace session.
   Codex recreates the final Trace editor with the exact original launch arguments, restores the UI
   state, and submits a new Ready marker. The worker checks the binding again at the
   saved marker, clears any earlier App Group export, and acknowledges Save only after those
   boundaries. A second rejected Ready ends the turn without Save. Collection therefore cannot wait
   on or accept an export from an unbound process.
8. When Save is accepted, Trace renders the configured background and Trace content into its bound
   `trace_wallpaper` PNG. The worker independently requires that intermediate PNG and manifest
   SHA-256, request digest, nonce, bundle, UDID, dimensions, native export binding, and
   `native_appium` provenance to agree. It then starts one separate official `codex exec` turn with
   `image_generation` enabled. The turn receives a packaged default iPhone date/time reference plus
   the localized date and time, and writes one transparent date-and-clock UI PNG. It must preserve
   the reference's neutral color, typography, hierarchy, spacing, and placement; no persona colors,
   background, phone frame, status bar, widget, notification, or editor chrome is accepted. The
   worker rescales that layer to the Trace canvas, composites it over the Trace PNG, and records the
   source PNG SHA-256, ImageGen prompt SHA-256, UI-layer SHA-256, final SHA-256, request digest,
   nonce, and UDID in
   `trace.imagen-ios-ui.v1`. The returned image is explicitly `imagen_ios_ui`: it is a
   generated copy of the default iPhone UI, not an iOS system wallpaper render. After collection or a
   terminal capture failure, the
   worker asks the helper to delete only the recorded request-owned calendar whose identifier,
   namespace, digest marker, and events all match. Cleanup has an independent bounded budget; a
   cleanup failure remains attached to the primary capture failure.
9. It commits a callback to the outbox. Callback delivery retries without rerunning Codex; Cloudflare
   stores accepted output in R2/D1, appends `callback_applied`, and opens human image review.

## Workspace execution diagnostics

The public workspace reads recent worker task events only inside the selected account scope. The
timeline is a debug projection, not an execution authority: D1 lease, local admission, execution
barrier, callback reservation, callback result, and review state remain canonical. The worker emits
preparation and execution outcome events through a bounded local daemon queue. A saturated or
unavailable monitoring endpoint can drop diagnostics but cannot block or repeat a task. Cloudflare
records its own barrier and callback transitions.

Events use a closed vocabulary and a unique task/type key, expire after fourteen days, and are
returned with a bounded newest-first limit. They never contain a worker token, control-plane token,
enrollment code, prompt, provider output, callback body, local path, or arbitrary exception message.
The local mode-0600 launchd stdout/stderr files remain the machine-level fallback for details outside
that safe contract.

## Hosted feedback loop

Caption and image review events are durable D1 evidence. A rule is promoted only from rating 1–2
rejections by three distinct candidate IDs in the same account, context-profile scope, stage, and
tag. An unprofiled event stays in the explicit `unprofiled` scope. A control-plane override can
disable a promoted rule without deleting its evidence.

Before generation or capture, Cloudflare freezes a `trace.feedback-context.v1` envelope and its
canonical SHA-256 into the task. Caption tasks carry promoted caption rules. Image tasks carry
promoted image rules plus, only for the same candidate retry, the exact preceding image rejection
event, capture task, reviewed artifact digest, tags, and private note. The note is untrusted task
data: it is never promoted or copied into another candidate's task.

Workers advertise `feedback_context_v1`; D1 does not lease new feedback-aware work to older
workers. The broker evaluates any task's versioned `required_capability` against the worker's full
advertised capability map; `outcome_reassessment_v1` uses the same rolling-upgrade gate rather than
assuming that every marketing-judgment worker understands every subtype. A successful callback must return the selected envelope digest as
`feedback_application_sha256`, otherwise Cloudflare refuses it. This receipt proves that the
worker consumed the selected context boundary, not that the model semantically obeyed it. Human
review remains the semantic and visual gate. Candidate/image schemas, native PNG/manifest
validation, R2 storage, review states, and the no-auto-publishing boundary are unchanged.

The v2 contract fixes non-secret input and completion bindings, not selectors or UI procedures.

## Marketing-agent foundation

### Local dynamic evidence research

`trace-marketing agent research` is a separate local composition root for the provider-neutral
runtime. It does not enter the hosted worker router or any existing capture/publication path. One
immutable input snapshot pins the feature packet, caller-supplied customer-context projection, market
objective, required evidence scopes, and budget. The official Codex CLI receives only a safe planning
projection and chooses one available observe action; the host derives all IDs, capability bindings,
and invocation receipts.

```text
immutable request -> safe planner projection -> official Codex decision
-> registry-bound observe hand -> immutable local receipt -> re-plan
-> completed Evidence Brief | inconclusive | awaiting reconciliation
```

Product truth reads only the frozen packet, customer intelligence reads only the caller-supplied
planning projection, and market evidence uses the existing quarantined Codex web-research contract.
The local runner does not prove that customer projection was approved and does not fetch/hash market
source bytes, so generated market proposals remain `insufficient`; their complete proposal artifact
is retained privately for later verification. Every hand
stores a mode-0600 immutable result under a mode-0700 state root. The runtime persists its canonical
decision and bound invocation before dispatch, so restart replays the ledger instead of asking the
model to reproduce an earlier choice. Only `observe` capabilities exist in this registry. Appium,
candidate materialization, Threads publication, messaging, CRM mutation, and spend remain outside
this composition root.

### Dynamic research to hosted campaign handoff

`trace-marketing agent launch` is the first canonical bridge from that local reasoning loop into the
existing hosted feedback loop. It currently accepts only a closed-gate shadow packet and requires
both product-truth and market-evidence scopes. Product truth must be bound to the exact packet;
optional customer intelligence must be sufficient. A successful but locally unverified market
proposal produces a terminal `ResearchContinuation` instead of a false Evidence Brief. Only this
specific continuation may ask the hosted market-research owner to fetch and hash source bytes.

```text
dynamic research terminal trace
-> host-derived ResearchContinuation + immutable lineage
-> persist bound control-plane invocation + execution-start
-> POST shadow campaign once
-> hosted byte verification -> strategy -> approvals -> existing execution/outcome/learning loop
                         \
                          ambiguous response -> GET-only reconciliation
```

The model proposes research decisions and observations; it cannot choose the endpoint, request body,
idempotency key, effect class, or workflow transition. The caller supplies the agent-run ID and the
host binds the same value as the campaign ID. D1 migration `0032` stores the
agent-run ID and research input/trace/continuation digests as an all-or-none immutable lineage. The
market callback rebinds that lineage before it can queue strategy, and campaign status returns the
authenticated account ID, lineage, and latest evaluation and learning-candidate IDs. The local
preflight rejects a handoff above the hosted 64 KiB request limit before model or network work, and
the create route rejects a researched account that differs from the authenticated account. Appium,
image generation, candidate
materialization, approval, Threads publication, evaluation, reassessment, and learning remain their
existing independently tested tools. A missing customer signal, failed provider call, altered packet,
altered local ledger, or mismatched hosted status produces no new POST.

New strategy work uses a separate `agent_v1` hosted campaign epoch. D1 owns immutable feature
packets, account-scoped campaign projections, ordered run events, context receipts, strategy briefs,
pre-registered experiments, approval grants, and tool-action intent. It does not dual-write these
records into the legacy `MarketingWorkflow` / `MarketingAccountAgent` memory path.

The hosted workspace now accepts a normalized, immutable source packet and creates a no-effect
shadow campaign. D1 queues a `marketing_judgment` broker task only for a Mac that advertises that
task kind. The Mac freezes feature, knowledge, capability, prompt, and output-schema digests, runs
one private schema-constrained official Codex CLI turn, and returns a control/challenger strategy.
Cloudflare independently verifies the task/output scope, digests, supported claim IDs, reference
quarantine, control activation, and attribution semantics before atomically storing the receipt,
brief, registered experiment, projection update, and ordered event.

Quarantined reference research has an additional server-owned provenance boundary. The research
worker proposes public HTTPS URLs and observations but cannot issue a trusted source receipt.
Cloudflare follows only bounded, revalidated public-HTTPS redirects, accepts a bounded textual,
JSON, or PDF response, requires two distinct requested and final hosts, and hashes the fetched bytes.
`0030` stores the verification bundle and one immutable receipt per source. The subsequent strategy
task carries both snapshot and receipts, and its callback re-reads both from D1 before accepting a
brief. These receipts prove fetched-byte lineage only; observation truth, summary faithfulness,
source credibility, and semantic freshness remain quarantined and `unknown`.

Every live `new_launch` strategy proposal also contains
`trace.marketing-decision-dossier.v1`. It makes the selected ICP (or explicit `research_needed`),
positioning proof claims, complete dispositions for frozen product, customer, and market evidence,
and one bounded next step inspectable in the same human review packet. Customer-signal freshness and
confidence are re-derived from the frozen context. Product and quarantined-market freshness remain
`unknown` until a separate trusted freshness owner exists. The hosted callback rejects unsupported
ICPs or proof claims, hidden counterevidence, rewritten source results, and unsafe new-launch next
steps. After a hosted experiment callback independently derives and stores a dossier-bearing
strategy's result, the same atomic batch queues exactly one `outcome_reassessment` task. A
pre-dossier stored strategy is still evaluated but does not gain a synthetic dossier during rollout.
The reassessment's frozen input contains the prior
strategy brief, derived evaluation, their digests, and the still-supported claim IDs. A deterministic
router labels the observed state as `experiment_result`, `performance_regression`, or `tool_failure`;
the Codex turn decides the hypothesis-by-hypothesis response rather than selecting its own situation.
The callback re-reads the evaluation and strategy from D1, rejects changed evidence metadata,
invented ICPs or claims, and incomplete hypothesis coverage, then stores a separate append-only
`trace.marketing-reassessment.v1` proposal. It never supersedes the active strategy or creates a tool
action. Market events and tool failures outside the evaluated publication path still have no live
situation source. Dossiers and reassessments are no-effect records, not publication or budget authority.

If the reassessment's evidence-bound decision admits another experiment, the same callback appends a
`trace.next-experiment-request.v1` outbox record in the reassessment transaction. It does not require
an online worker. The scheduler later queues `next_experiment_v1` only when a compatible broker
worker is available. The model must cover every reassessment evidence ID and every contradictory or
insufficient evidence ID, and can emit only candidate content plus assumptions and unresolved
questions. Python derives the no-effect draft and review admission; Cloudflare re-reads and re-hashes
the packet, strategy, registration, evaluation, reassessment, knowledge, optional marketing context,
and local research lineage before storing them. The host copies the prior control, primary outcome,
and held constants, rejects a proposal that mutates a held constant using the same fixed
NFKC/case-fold/trim contract in Python and Cloudflare, and restricts claims to the
selected parent hypotheses. Source strings enter the model prompt through an explicit untrusted,
non-authoritative boundary. Full reasoning is exposed only by the control-plane-authorized exact
draft review packet, which keeps host-verified evaluation and evidence dispositions separate from
model-proposed interpretations. Draft approval is a no-effect review receipt: it atomically appends
an immutable `trace.successor-activation.v1` intent but calls no candidate, Appium, Threads,
publication, or spend owner. A scheduler waits for `shadow_strategy_v1`, then re-reads and re-hashes
the exact request, draft, approval, packet, strategy, evaluation, reassessment, knowledge, research
lineage, unknown-effect state, and optional context expiry. Only a passing admission creates one
deterministically identified successor shadow campaign plus one strategy task. The task freezes the
approved candidate and host-derived experiment identities. If the evaluated assisted source packet
has an open publication gate, the host derives a new digest-bound packet with the same evidence and
claims but a closed gate for the successor; it never copies live publication authority into shadow.
The Python executor and Cloudflare
callback both enforce the prior control, business outcome, primary outcome, held constants,
challenger claims, reassessment dossier, and reviewer lineage. The successor re-enters the existing
strategy-review flow and still has no tool action.

Source evidence cannot open the publication gate, and a database trigger prevents every shadow
campaign from creating tool actions. This path creates no candidate, capture, publication, metric,
or learning effect. Installed-product evidence and later stages remain governed by
[`docs/plans/threads-marketing-agent.md`](../plans/threads-marketing-agent.md).

After exact human strategy approval, the same broker may run one `creative_plan` judgment. It picks
proof before medium and emits only a MediaPlan plus typed artifact requests; it cannot execute those
requests. Cloudflare freezes active capture/copy adapter descriptors into the task, re-derives their
binding digests, and rejects a callback if that catalog changed; receipt-scoped binding rows and
binding-bearing artifact requests are then written atomically. Exact creative review is recorded
separately. Candidate assignment requires an approved plan, a non-shadow assisted/live campaign,
an open installed-evidence publication gate, exact experiment/treatment lineage, and the existing
control-plane authority. Reviewer authority never enters a worker payload.

Migrations `0019`–`0034` add the execution and observation ledger: immutable artifact manifests,
candidate/post assignments, variant links, versioned product events, direct-response attribution
observations, evaluations, outcome reassessments, next-experiment outbox/drafts, successor activation
outbox, quarantined reference snapshots and source-byte receipts, learning candidates, and approval-bound
principles. Each campaign freezes its approved knowledge snapshot before its first judgment; strategy,
creative planning, and candidate materialization reuse that snapshot, so a later learning approval
improves a future campaign without contaminating an active experiment. An assisted campaign must name a same-account shadow origin, carry an installed-evidence
packet plus exact product-truth approval, and use the same broker for candidate materialization.
Candidate materialization is a bounded Codex judgment: it returns one evidence-bound candidate and
no capture, publish, or arbitrary tool action. Its image input uses the same structured weekly
schedule and todo contract as the main candidate generator; the Cloudflare delivery and marketing
callbacks share one normalizer, so a marketing path cannot silently fall back to a day-zero string
schedule. New tasks require `candidate_materialization_v2`; an older worker cannot lease them, while
the request fails before writing a task or reservation when no compatible worker is online. Callback
validation rechecks the task capability, so only an already-leased capability-less task can finish
with its frozen v1 result during rollout. Existing candidate/image review owns native capture;
the existing `threads/*` owner remains responsible for every publication effect.

Campaign creation is a control-plane operation in both shadow and assisted mode. Before any queue
mutation, the runtime requires authority and verifies an active, recently seen worker advertising
the exact versioned reasoning capability. A worker heartbeat reports capture readiness separately
from Codex reasoning readiness. When Appium is degraded, the broker may lease only a compatible
`marketing_judgment`; capture and ordinary candidate generation remain blocked exactly as before.
`0031_marketing_worker_task_events.sql` preserves existing events and adds that task kind to the
closed timeline schema.

The hosted route creates short-lived variant redirects and accepts versioned, deduplicated product
events only under event-ingest authority. The scheduler queues an evaluation only after the
pre-registered observation window or horizon closes. Direct-response rates remain explicitly
descriptive. The causal-estimation contract is restricted to a two-arm, fixed-sample registration
that chooses the server-randomized complete-block method: Cloudflare creates and immutably records
the allocation seed, materialization records the selected rank and seed digest, manual candidate
assignment cannot bypass that plan, and evaluation recomputes ranks from the server-held seed.
At experiment registration, Cloudflare freezes the account schedule revision and exact Threads
profile/user identity in an immutable exposure-plan receipt, before any allocation rank is exposed.
The paired treatment-minus-control estimator and exact two-sided randomization test become eligible
only after the existing image-review path atomically expands that receipt into the complete
fixed-sample exposure-slot schedule. The ledger binds every assignment, randomized rank,
morning/evening slot, frozen profile/user, account timezone, wall-clock policy, scheduled instant,
seed digest, and tolerance before the first publication decision.
The existing Threads publication row must match that commitment exactly, and actual publication must
fall within the fixed tolerance. Missing, late, canceled, mismatched, or unknown exposure remains
inconclusive rather than being dropped from the sample. Human image approval, the account auto-publish
toggle, publisher barrier, and readback remain the only publication path. Incomplete coverage,
malformed allocation lineage, or guardrail failure also remains fail-closed. Its
callback independently re-derives the full result from the immutable task request, verifies the
frozen registration digest, and stores only that derived result; a worker-provided state, winner, or
coverage cannot promote false learning. A learning synthesis can create only a candidate from
independent evaluated campaigns with one frozen structured applicability selector. An exact human
approval is required before a scoped principle is written, and a future campaign receives it only
when its account, feature packet digest, country, language, mode, and context-snapshot digest exactly
match. The learning callback re-derives that selector from the current D1 evaluation lineage before it
writes a candidate, and the knowledge loader narrows by those selector fields before its bounded
result limit. Legacy or prose-only scope records never auto-apply. Generic recording, composition,
Figma, and generated-media executors remain deferred, so no unsupported artifact capability is
simulated.

The control plane also exposes a bounded, read-only `trace.marketing-review-queue.v1` for pending
strategy, creative, next-experiment, and learning decisions, plus one
`trace.marketing-review-packet.v1` per campaign and one exact
`trace.next-experiment-review-packet.v1` per draft. All reads require control-plane authority and
derive their target ID, SHA-256, campaign state, and current projection revision from D1. The packet
contains feature evidence, receipt digests, current strategy/creative/outcome/learning records, and
the exact existing POST body that can approve or reject that target. It makes no write, task,
approval grant, artifact, or channel action. Customer-source records and context-snapshot contents
are never loaded into this packet; only an already-bound snapshot ID and digest may appear. Cost,
blast-radius, rollback, and external-effect approval are not yet recorded for these no-effect
decisions, so the packet names that limit rather than simulating an authority. Artifact requests and
manifests expose their capability-binding digest; capture manifests may also expose a safe source,
role, and source-artifact digest projection. Neither artifact URI, raw manifest JSON, nor catalog
descriptor is a review-packet field. An effect owner must issue a separately bounded read capability
when an actual asset needs inspection.

Existing Threads publication rows merely snapshot an optional assignment ID; publisher, OAuth,
publish-once, readback, and metrics behavior are unchanged.

`0025` adds the first governed customer-intelligence path. A caller may import only a manual,
normalized `CustomerSignal`; dedicated raw-text and connector-record fields are rejected, while the
human reviewer remains responsible for the normalization itself. The task treats the resulting
summary as bounded context, never as an instruction. The signal begins pending, an authorized human
makes one final approve/reject decision, and a human creates an immutable account-scoped
`MarketingContextSnapshot` from approved signals. Its retention and freshness must cover the
snapshot's full lifetime. Context reads and a context-bound campaign require control-plane authority.
A campaign may bind one still-current snapshot by ID and digest; the broker receives only its safe
planning projection (brand guardrails, audience context, policy IDs, and normalized signal
summaries/caveats). The Mac refuses an expired projection before it opens the provider, and the
research-to-strategy handoff re-derives the projection from D1. It does not receive source references,
source digests, or consent metadata. The strategy receipt stores the same projection, and the hosted
callback rebinds it from D1 before it writes a brief. A later signal, approval, or snapshot cannot
rewrite an already queued campaign. `retention_until` is currently a use and API-visibility deadline;
physical deletion of all immutable audit copies is a later privacy-control-plane contract. This is an
account-scoped reference lane, not a CRM/transcript connector, RAG memory system, role model, or
automatic channel action.

`0024` and `0026` add an account-scoped effect-adapter catalog and a context-receipt-scoped
capability-binding ledger. Existing accounts receive active `capture.native_png`, active
`copy.text`, and reference-only `publish.threads` descriptors; the latter cannot open a new
publishing path. A resolver validates the canonical descriptor against its typed catalog fields and
derives a binding digest server-side. A request and manifest must carry that exact immutable context
binding; later copy or capture action rechecks that the same descriptor remains active. Native/ImageGen
capture verifies that binding before R2 or candidate mutation and records `native_appium` or
`imagen_ios_ui` provenance in the manifest. ImageGen is provenance of `capture.native_png`, not a
second planner capability. Core judgment remains outside this catalog.

The provider-neutral `marketing.runtime` harness is a local, pre-adapter runtime boundary. It
persists append-only session history under a host-local lock. A capability owns its descriptor and
request-schema digests; admission creates a `BoundToolInvocation` that canonically persists the
non-secret request together with its schema-bound `ToolCall`. A backend receives that invocation,
not a digest-only call, and resolves any connector secret from its own capability identity.
`request_persisted_tool` first CASes one pending call and its exact invocation;
`execute_persisted_tool` then CASes an execution-start checkpoint before it can enter a backend. On
load, every cache field is re-derived from the immutable `session_started` header and the closed
runtime-event grammar: session ID, budget, state, spent/reserved cost, pending invocation/grant,
execution claim, idempotency keys, and consumed grants must all agree. Event sequence, canonical
payload digest, UTC/non-decreasing event time, runtime event type, and final-event ordering are
also checked, so a rewritten checkpoint cannot redeliver a claimed effect or enlarge its budget. A
restart-recovered execution checkpoint can only enter reconciliation, never redelivery. The harness
reserves budget, consumes an exact one-use external approval grant, and accepts a receipt only when
its call and grant digests bind to that pending call. Backend exceptions and rejected receipts become
`awaiting_reconciliation`. This harness has no Cloudflare, Appium, Threads, or model-provider import
and is not a hosted worker or an automatic-publication path. Its public effect surface is the
persisted admission/execution sequence; non-durable transition helpers are private unit-test
primitives, so a future hand cannot skip the checkpoints. A read-only `replay_session(events)` export
reconstructs the same checkpoint for an offline trace grader; it confers no execution authority.
Current serialization is explicitly
versioned: v3 writes the ledger header, while verified pre-header v1/v2 terminal traces are read-only
and pre-header pending or non-terminal sessions fail closed rather than being rewritten or re-executed.
General hosted orchestration and outcome optimization remain deferred. The installed local research
composition invokes the official Codex planner and only the three observe hands described below;
tests retain fake backends for deterministic contract coverage.

The first exercise is `feature_launch_operator`: a provider-neutral, observe-only Feature Launch
Experiment Operator. A new launch session first verifies an immutable research evidence brief by
reloading and re-deriving the completed source session, then commits that brief before it persists a
feature goal, strict planner decision, runtime-owned tool receipt, receipt-bound observation,
deterministic process/outcome evaluation, and a terminal result as canonical session events. The brief
must precede the goal and appear exactly once; its digest and selected research observation IDs bind the
proposal, derived call input, launch observation, and evaluation. It exposes
exactly one registry action,
`observe.feature_launch_experiment`; the registry derives a descriptor-bound call from the feature
packet, approved claim IDs, brief-supported claim set, and request-schema digest. A restart replays a
committed decision without calling the planner. Planner context receives only the shared data-only
product projection plus a data-only brief projection, and replay revalidates persisted
observation/evaluation lineage before completion; terminal sessions audit the same trace without a
hand reinvocation. Sufficient evidence still becomes inconclusive if the observation finds
counter-evidence against the proposed falsifier. This is an evaluation vertical, not a new live
research or publication path.

`evidence_research_operator` is the first bounded multi-step research vertical. It lets a strict
planner choose exactly one unobserved, observe-only hand at a time from `product_truth`,
`customer_intelligence`, and `market_evidence`. Each hand must produce a runtime receipt before its
observation can be recorded. The next planning turn receives a bounded semantic summary, caveats,
trust state, scope/status/claim IDs, and a product projection of packet ID, digest, lifecycle, and
claim IDs. The local composition deterministically removes recognizable URLs and known proposal
source/packet-claim literals; the remaining model-authored string is explicitly untrusted data and
cannot grant authority. Opposing evidence therefore changes the next planning context without
changing authority. The registry rechecks the pinned packet, provider/model/protocol, skill snapshot,
canonical
action-to-scope mapping, claim IDs, iteration, and effect class. On replay it reconstructs every
decision/receipt/observation lineage and deterministically regrades every prior evaluation before a
new hand can run; terminal sessions audit the same trace without reinvoking a hand. The loop
completes only after every required scope has sufficient receipt-bound evidence; otherwise its
at-most-three iterations end in an explicit inconclusive result. The installed composition uses an
official Codex planner and real local read hands; an unverified model proposal is forbidden from
closing a scope. This is not a claim-authoring, publication, Cloudflare, or live-market-performance
path.

A completed Evidence Research session can be converted without any new planner, hand, or session-store
side effect into `trace.feature-launch-evidence-brief.v2`. Its provenance pins the completed research
goal, planner provider/model/protocol, registry snapshot, terminal evaluation, and canonical
event-trace digest. The brief retains bounded semantic summaries, caveats, trust state,
receipt/call/request/decision/source digests, and allowed supported claim IDs for each required scope;
it excludes source locations, URLs, raw source text, and research questions. The next Feature Launch run is a
separate session with a distinct budget and registry. This is an immutable hand-off contract, not a
merged multi-skill loop or proof of a live-market outcome.

## Threads publication and observation

Threads has three configuration states. Zero bindings means disabled: health remains successful with
`threads_ready: false`, config omits Threads variables, and both Threads schedulers skip. A complete
set of four public variables and three runtime secrets means ready. Any partial or invalid set fails
closed. Threads auto-publish remains OFF until an operator connects a profile and enables it.

One account may connect multiple encrypted Threads profiles and choose one default. New morning and
evening candidates snapshot that active default; an operator may select another account-owned active
profile only before final image approval. Default changes never retarget existing candidates.

Final image approval atomically freezes the selected profile, candidate revision, caption, image key
and digest, IANA timezone, wall clock, and strictly-next slot. OFF creates a terminal cancellation and
manual slots create no publication. A bounded Cloudflare scheduler claims each due row, validates the
profile, token, scopes, and quota, signs a ten-minute account/publication/digest media capability,
creates a Meta container, then rechecks toggle/profile state at `container_ready -> publishing`.
Only that CAS is the irreversible barrier. Before it, OFF/disconnect cancels with zero publish POSTs;
after it, an ambiguous result is `unknown_side_effect` and never an automatic retry.

Successful publish stores the returned post ID before readback. Only matching authoritative readback
produces `published` and a permalink. Readback failure with a durable post ID retries readback only.
Published rows poll at +15m, +1h, +6h, +24h, then daily through day 30. Metrics and reply cursors are
independent, OFF is not a polling predicate, 401/403 pauses the same profile for reconnect, and
deleted posts become unavailable. Reply bodies and metric snapshots expire after 30 and 365 days.

## Failure, services, and compatibility

A pre-barrier failure is ordinary task failure. Calendar preparation is a post-barrier side effect.
A post-barrier crash or exception is
`unknown_side_effect`; the worker never repeats potentially completed Trace work. Restart recovery
requeues interrupted safe work and leaves post-barrier ambiguity visible. Callback retries are
delivery-only.
Monitoring-event delivery is also best-effort and never advances task state. Missing events during a
control-plane outage do not change the inbox/outbox recovery contract; a worker exits without waiting
for a blocked diagnostic delivery thread.

`trace-marketing worker run` is the foreground worker. The managed labels are
`com.corca.trace-marketing-worker` and `com.corca.trace-marketing-updater`; they run in the same
`gui/<uid>` domain as the Codex login and pin its executable. `~/.trace-agent` remains the default
state home. The updater only reads legacy `codex-runs/<id>/executing` without `result.json` to
defer activation; it preserves that compatibility state across releases.

When a release changes control-plane paths, the release workflow waits for the exact deployed health
SHA before it makes the GitHub Release public. It then writes the strict version to the Cloudflare
control-plane binding. An authenticated heartbeat returns it only to an older worker. The worker
starts the loaded updater once with `launchctl kickstart`; it does not receive release bytes or
bypass attestation, draining, atomic switching, or rollback. The 15-second heartbeat is the
immediate path and repeats the non-forced wake-up while the version is older. The hourly updater
schedule remains the fallback.

`com.corca.trace-agent` and `com.corca.trace-ads` are migration-only old plist labels, not current
service instructions. Native manifest validation does not prove visual semantics. Human review is
mandatory. Only the default-OFF hosted Threads path can publish; the Mac worker, generic `/v1`
simulation path, and every other marketing channel cannot.
