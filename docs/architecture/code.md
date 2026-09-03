# Code Architecture

Status: Active
Last reviewed: 2026-09-03

## On-premises Marketing Agent transition

The new dependency direction is portable contracts → agent core → agent service → provider/tool/
channel adapters. Existing Appium, candidate, Threads, and Cloudflare effect owners remain leaves
behind wrappers; they must not import or become the Agent core.

| Package | Responsibility | Must not own |
| --- | --- | --- |
| `contracts/agent_run.py` | portable Run, Step, Intent, snapshot, invocation, approval, receipt, outcome, learning, and record envelopes | provider calls, storage, channel state |
| `contracts/tool_capability.py` | complete ToolDescriptor policy/readiness/idempotency/reconciliation contract | registry selection or execution |
| `contracts/reasoning.py` | replaceable structured reasoning request and decision | Codex process lifecycle |
| `marketing/agent_core/` | capability selection and provider/tool ports | SQLite, HTTP, Cloudflare, Appium |
| `marketing/agent_service/` | canonical on-prem application flow and append-only SQLite repository | channel-specific planning or effect implementation |

`marketing/runtime.py` remains the execution-safety kernel for write-ahead invocation, exact-call
approval, receipt validation, restart recovery, and reconciliation. The service composes it; it does
not fork those guarantees. The previous deleted `agent/` connector-specific product is not restored,
and no `trace-agent` or `trace-ads` entrypoint is introduced.

Current Cloudflare/D1 hosted Run code remains a compatibility owner until the projection cutover is
implemented and verified. New portable records must not be dual-written as independently mutable
authorities. See [`on-prem-marketing-agent-service.md`](../contracts/on-prem-marketing-agent-service.md)
for current, transition, and target boundaries.

## Composition

`ads_booster.cli.marketing` exports the sole CLI, `trace-marketing`. `worker run` composes a
`MarketingWorkerLoop` from D1 `WorkerBrokerClient`, SQLite `MarketingInbox`, and a planless hosted
executor that routes immutable tasks to `HostedWorkspaceCaptureExecutor`, hosted caption
generation, or a discriminated marketing judgment executor.

```text
cli/marketing
  -> marketing/worker_loop
     -> marketing/worker_broker       D1 lease, barrier, callback
     -> marketing/inbox               SQLite inbox and outbox
     -> marketing/native_capture      prepare/execute and final validation
        -> marketing/background       background intent/provenance
        -> capture/codex_appium_job   immutable v2 contract
        -> capture/calendar_automation_contract typed EventKit file contract
        -> capture/calendar_preparation post-barrier seed/verify/cleanup
        -> capture/calendar_lifecycle cleanup on every post-prepare exit
        -> capture/appium_codex       one job and native collector
           -> capture/appium_codex_validation paths and completion proof
           -> capture/appium_editor_verifier live UI and process-binding proof
        -> providers/codex_cli        official CLI subprocess
     -> marketing/hosted_judgment     source- and reference-bound strategy proposal
     -> marketing/hosted_creative_judgment
     -> marketing/hosted_candidate_judgment
     -> marketing/hosted_learning_judgment
        -> providers/codex_cli        distinct official structured-judgment subprocess receipts
     -> marketing/hosted_experiment_evaluation
                                      deterministic, no-model outcome evaluation
```

## Responsibility boundaries

| Area | Owns | Does not own |
| --- | --- | --- |
| `worker_broker.py` | D1 claim, acknowledgement, barrier, callback HTTP | SQLite/Appium |
| `inbox.py` | ingress, claim, admission, terminal result, callback retry | Cloudflare/Codex |
| `worker_loop.py` | prepare-barrier-execute ordering and ambiguity handling | Trace UI method |
| `background.py` | `background_intent`, allowlisted candidate ranking, digest, provenance | native export |
| `native_capture.py` | hosted payload/context, Trace PNG validation, ImageGen output provenance | UI selectors, image editing |
| `codex_imagegen_ui.py` | one official Codex ImageGen turn against the packaged iPhone UI reference, transparent UI-layer normalization, final UI manifest | external model auth, Trace export |
| `contracts/feedback.py` | strict `trace.feedback-context.v1` shape and canonical digest | rule promotion, review storage |
| `codex_appium_job.py` | v2 context/device/digest/nonce/time/calendar contract | process execution |
| `calendar_automation_contract.py` | typed prepare/cleanup request, result, and event time projection | EventKit execution, layout |
| `calendar_preparation.py` | post-barrier Trace helper launch, Calendar proof and request-owned cleanup | Trace layout, Calendar UI navigation |
| `calendar_lifecycle.py` | independent cleanup budget and primary/cleanup error preservation | EventKit execution |
| `appium_codex.py` | device lock, Calendar lifetime, contract file, one Codex result, native collection | UI reasoning |
| `appium_codex_validation.py` | request paths, result/Ready/Saved consistency, expected titles | process execution |
| `appium_editor_verifier.py` | live editor titles and Trace process launch-binding verification | UI navigation, export rendering |
| `codex_cli.py` | schema-constrained official CLI subprocess, bounded Ready-session recovery, and localhost-only permission profile | custom agent/auth/thread state, image generation |

`CodexAppiumJobContract` uses `trace.codex-appium-job.v2`. Its canonical digest covers identity,
marketing context, prepared background, device, locale/time zone, nonce, and calendar namespace.
The contract is mode 0600 in a mode-0700 request root. Codex runs without user/project configuration;
its commands can access the workspace and loopback Appium, but not home secrets or external hosts.
The worker starts the DEBUG Trace EventKit helper only after execution admission. Its helper launch
adds `-traceMarketingCalendarAutomation`; the final Codex editor keeps the original immutable launch
arguments. Codex reports UI completion and session close only. Worker-owned Calendar proof and
cleanup never enter Ready/Saved/result schemas. Only the worker collector proves the App Group export
and validates the manifest.

Cloudflare owns review-event binding, distinct-candidate rule promotion, override state, task
selection, and callback receipt comparison. Python validates the same feedback envelope, inserts
caption rules into the candidate instruction or carries image correction context in the Appium job,
and returns only the selected digest as its consumption receipt. Neither side changes generated
candidate fields or native artifact fields to transport feedback.

The package deliberately keeps no alternate execution runtime or legacy command compatibility.
`trace-agent` and `trace-ads` are migration-only names.

## Hosted Threads modules

| Module | Owns |
| --- | --- |
| `cloudflare/src/threads/config.js` | disabled/ready classification, all-or-none bindings, public config rendering contract |
| `cloudflare/src/threads/client.js` | pinned Graph operations and strict public client surface |
| `transport.js` / `responses.js` | bounded GET retry, no-retry POST, typed Graph parsing |
| `crypto.js` | versioned AES-GCM token encryption |
| `profiles-api.js` / `profiles-store.js` | account-scoped OAuth state, profile lifecycle, default and toggle CAS |
| `media-capability.js` | short-lived HMAC/digest/account/publication-bound private PNG fetch |
| `scheduling.js` | bounded due publication selection and per-row isolation |
| `publication.js` | quota, container, irreversible barrier, publish-once, post-ID readback |
| `engagement.js` | independent metric/reply polling, cursors, reauth/deletion, retention |
| `status-api.js` | public-safe status/metrics plus privileged replies and unknown resolution |

`render-config.mjs`, deployment health, and `index.js` share the same configuration owner. Disabled
deployments omit public Threads variables and skip publication and engagement tasks; partial
configuration fails before deployment or scheduling.

`hosted-workspace.js` owns candidate creation and dual human approval. It snapshots the default
profile at candidate creation and inserts the immutable publication decision in the same D1 batch as
accepted image review. `index.js` composes separate candidate-generation, publication, and engagement
schedulers. Threads modules do not import or extend Mac worker task kinds or the generic `/v1`
adapter; the sibling marketing-agent route delegates no-effect judgment tasks to that broker.

## Marketing-agent contracts

`ads_booster.contracts.marketing_agent` owns the Python source contract for feature evidence,
strategy portfolios, registered outcomes, and frozen context receipts.
`marketing/dynamic_evidence_research.py` is the installed CLI composition root for the first dynamic
observe-only loop. It adapts the official Codex CLI to `evidence_research_operator`, builds a
source-derived three-hand capability registry, owns the product/customer/market collectors and their
private immutable local receipts, and emits a receipt-grounded Evidence Brief. The generic runtime and
operator remain provider-neutral; this module contains no Appium, Threads, Cloudflare, outreach, CRM,
or spend adapter. `cli/marketing.py` only validates the immutable request and selects the state root.
`marketing/feature_launch_run.py` is the thin composition boundary that admits a terminal
`ResearchContinuation`, derives one `control_plane.create_shadow_campaign` invocation, and binds it to
the existing hosted campaign API. It owns neither strategy nor channel effects. Its local runtime
ledger is written before POST; ambiguous dispatches can be resolved only by exact-lineage campaign
readback. `marketing/runtime.py` owns the generic persisted reconciliation-receipt transition used by
that adapter. D1 migration `0032_marketing_agent_run_lineage.sql` owns the all-or-none, immutable,
account-unique local-to-hosted lineage. `marketing-agent.js` validates and stores it, while the
reference-research and strategy callbacks independently rebind it before advancing state.
`ads_booster.contracts.marketing_context` separately owns the allowlisted customer-signal and
campaign-context shapes: its full signal retains provenance and consent, while its planning
projection deliberately excludes both. D1 migration
`0018_marketing_agent_foundation.sql` owns the new `agent_v1` persistence epoch and its shadow
no-tool-action guard. `marketing/hosted_judgment.py` owns validation, private workspace admission,
the schema-constrained strategy turn, claim/reference quarantine, approved-context prompt boundary,
and the bound result. Cloudflare
`marketing-agent.js` owns campaign ingestion and task creation; every campaign creation requires
control-plane authority before its request body can reach the shared broker;
`hosted-marketing-judgment-callback.js` independently validates and atomically persists successful
strategy state. The generic worker broker owns leasing and callback transport only. Marketing's
`marketing-worker-capabilities.js` owns the subtype-to-version registry and the active/recent/exact
capability preflight. Its task rows freeze that capability, and callbacks reject a different
non-legacy binding. The broker may admit only `marketing_judgment` while Appium is degraded but
Codex reasoning is ready; capture and candidate-generation readiness are unchanged.
`marketing/hosted_task_router.py` is the worker composition root mapping subtypes to leaf executors.
`hosted_generation.py`, native capture, and each judgment module remain tool owners.

`cloudflare/src/marketing-agent-runs.js` owns the channel-independent hosted run intake, immutable
request/task binding, account-scoped lifecycle projection, and idempotent replay. Migration
`0035_hosted_marketing_agent_runs.sql` owns that run ledger separately from the campaign ledger.
`cloudflare/src/marketing-run-capabilities.js` owns the host projection of the first common,
observe-only research action plane. Migration
`0037_marketing_agent_run_capability_receipts.sql` freezes its per-run snapshot and owns the
append-only invocation/receipt/observation ledger. The matching strict Python shapes live in
`contracts/marketing_capability.py`; parity tests bind the host and installed worker constants and
canonical subset digest.
`marketing/hosted_feature_launch_run.py` is its no-effect Mac leaf: it pins the task-selected official
Codex model, validates the host snapshot before provider execution, constructs the runtime registry
from that snapshot, and invokes only `DynamicEvidenceResearchRunner`. Its result exports a
worker-reported ordered canonical envelope derived from persisted runtime events. The callback independently
re-derives the host snapshot and recomputes the redacted descriptor, invocation, call, decision,
hand-result, receipt, and observation digests before validating exact scope/action/source/cost/lineage
coverage and appending the full proof envelope. It never constructs a
`FeatureLaunchRunner`, control-plane client, campaign, candidate, Appium action, or publication.
`cloudflare/src/marketing-run-intents.js` and the matching Python contract own the next no-effect
intent descriptor projection. They derive only currently eligible stop, bounded needs-input, and
shadow-proposal options from the validated research result. Migration
`0038_marketing_agent_run_intent_steps.sql` freezes the chosen snapshot/decision and owns the
append-only run-step record. `0039_marketing_agent_resume_loop.sql` adds the immutable run-to-task
mapping, child receipt ledger, and compare-and-swap run head. `marketing-agent-runs.js` owns the
single account-scoped customer-context resume admission and creates a new sequence-two task; it never
resets a completed broker task. The callback reconstructs the planner bytes and may delegate only
the shadow proposal to the existing campaign owner; stop creates no task, and request-more may
create only that bounded research child.
`cloudflare/src/hosted-feature-launch-run-callback.js` independently binds the worker result back to
the stored request, validates the full quarantined market proposal against its finding digest, and
derives the only admissible lineage. For a proposal it commits the proof, decision step, completed
worker task, and a `0040` immutable delegation outbox record in one batch.
`cloudflare/src/marketing-agent-delegations.js` owns scheduled and immediate reconciliation: it
revalidates the stored outbox/step/run binding, delegates campaign creation to the existing
idempotent `createShadowCampaign` owner, CAS-finalizes the run, and durably defers failed rows with
capped exponential backoff and a non-sensitive failure code so later rows remain eligible. That owner places the exact proposal and digest in the existing
`market_research` task. `marketing/hosted_reference_research.py` consumes a frozen seed without a
second provider search, and its Cloudflare callback requires the returned snapshot to equal that seed
before the existing URL byte verifier can create receipts and strategy input. `index.js` performs judgment-subtype callback
dispatch and schedules delegation reconciliation. Broker task IDs and product run/campaign IDs are distinct to preserve the existing task
run uniqueness contract.
`cloudflare/src/marketing-agent-run-journey.js` is a read-only projection owner. It derives a bounded
run journey from the existing campaign, origin, activation, evaluation, reassessment,
next-experiment, and learning records. It performs parent-scoped breadth-first expansion through
constant-parameter, index-forced queries and hydrates in D1-safe 99-ID chunks under fixed node/depth
bounds; migration `0041` owns the matching origin/activation/evaluation/learning indexes. It owns no state
transition and writes no duplicate graph.
`hosted-experiment-evaluation-callback.js` commits a valid evaluation and the downstream
capability-gated reassessment task atomically without requiring a compatible worker to be online at
callback time.

`marketing/hosted_creative_judgment.py` owns the proof-first MediaPlan proposal and creates no tool
action. It validates frozen capability descriptor bindings and derives the prompt's capability IDs
and executable format allowlist from them; it never constructs a binding. Cloudflare derives the same
allowlist when creating a `creative_plan_v2` task and revalidates each format-to-capability
requirement on callback. Currently only `native_sequence` is executable; recording, carousel,
designed-static, and text-only formats remain unavailable until their adapters exist. The current
worker heartbeat additionally advertises `creative_plan_v1` only as a drain capability for frozen
queued tasks. Only the creative callback opts into that legacy validator; current task creation and
every other judgment subtype continue to require their exact current version.
`marketing/hosted_candidate_judgment.py` materializes one approved, evidence-bound
candidate and reuses `workspace.CandidateImageInputs` rather than defining a marketing-only image
shape. `cloudflare/src/candidate-image-inputs.js` is the matching control-plane normalizer shared by
ordinary candidate delivery and marketing materialization. New marketing materialization requires
the canonical structured weekly schedule and todo column; legacy string rows remain readable only
at the ordinary delivery boundary or an in-flight v1 callback. The marketing worker capability gate
fails before reservation when no active, recently seen, reasoning-ready compatible worker is online, keeps
`candidate_materialization_v2` tasks away from older workers, and binds the callback schema back to
the leased task capability. `marketing/hosted_reference_research.py` returns an immutable quarantined observation
snapshot and validates the server-issued receipt contract carried into strategy;
`cloudflare/src/reference-source-verification.js` alone fetches declared public sources and derives
byte-level receipts, while both hosted callbacks bind those receipts into and back out of D1; and
`marketing/hosted_learning_judgment.py` creates only a reversible learning candidate.
`marketing/hosted_experiment_evaluation.py` is deterministic and has no model or external effect.
`experiment-evaluation.js` is its pure control-plane re-deriver: it accepts only the frozen request
and reproduces its conclusion, coverage, lineage, and guardrail result. `ExperimentRegistration`
owns the distinction between descriptive balanced blocks and the two-arm
`server_randomized_complete_blocks_v1` estimator; `CausalEffectEstimate` owns the paired risk
difference, server seed digest, and exact two-sided decision evidence. `marketing-agent.js` owns
server seed generation, deterministic rank allocation and re-verification, and the rejection of
manual assignment for that estimator. The strategy callback freezes the account schedule and exact
Threads identity in an immutable experiment exposure plan before materialization. `hosted-workspace.js`
expands that plan into the immutable complete exposure schedule at the existing image-approval
boundary, while `marketing-agent.js` independently re-derives plan and slot hashes and compares
profile, user, schedule, and publication readback before it sets causal exposure verification.
Neither owner publishes; the existing Threads owner retains the external effect.
The experiment-evaluation
callback canonical-compares the worker output to that derived value and persists the derived value
only after the frozen registration digest also matches D1. In the same batch it uses
`cloudflare/src/marketing-outcome-reassessment.js` to derive a situation and queue one immutable
no-effect follow-up. `marketing/hosted_reassessment_judgment.py` owns the schema-constrained Codex
reassessment, while `cloudflare/src/hosted-outcome-reassessment-callback.js` independently rebinds
the stored evaluation, strategy, evidence metadata, claims, and hypothesis set before writing its
append-only ledger row. Neither owner changes strategy or executes the recommendation. Their Cloudflare callbacks independently
validate every receipt, claim, plan, assignment, and approval binding before writing a projection.
`marketing/hosted_next_experiment_judgment.py` is the next no-effect reasoning leaf. It owns the
strict model schema for evidence interpretations, counterevidence, assumptions, questions, and
challenger content; it treats source strings as untrusted data, binds candidate claims to selected
parent hypotheses, and prevents held-constant mutation. The model cannot emit state, tool, budget,
schedule, or new identity authority.
`cloudflare/src/marketing-next-experiment.js` owns the immutable request/outbox, compatible-worker
dispatch, cross-runtime validation, and host-copied experimental controls.
`cloudflare/src/hosted-next-experiment-callback.js` independently re-derives source lineage before
storing a draft/admission in migration `0033_marketing_next_experiments.sql`.
`cloudflare/src/marketing-next-experiment-review.js` owns the protected exact review packet, presents
host-verified evaluation/disposition facts separately from model interpretation, and records the
no-effect approval receipt plus activation intent.
`cloudflare/src/marketing-successor-activation.js` owns the activation-time source revalidation and
the atomic, deterministic creation of one successor shadow campaign and its ordinary strategy task;
`marketing/hosted_judgment.py` and `hosted-marketing-judgment-callback.js` independently enforce the
approved successor constraints before storing its strategy. Reviewer authority remains in the
Cloudflare activation ledger and is not part of a worker/model projection. None of these modules
imports or replaces candidate, native-capture, or Threads effect owners.
`marketing-agent.js` projects the latest activation's safe operational status on the source campaign;
it does not expose the approval grant or reviewer record.
Learning synthesis receives a server-derived `MarketingLearningApplicability`; its model may explain
scope in prose but cannot broaden the selector. The callback binds that selector into the candidate,
re-derives it from D1's evaluation/campaign/packet/account lineage, and rejects drift before a write.
Learning approval copies it into the immutable principle, and `marketing-agent.js` SQL-narrows on every
selector member before the bounded lookup, then applies a defensive exact canonical match before a
new campaign's knowledge snapshot receives a principle. Legacy principles without the selector are
read but never auto-applied. `marketing-agent.js` also owns assisted campaign gating, variant links,
product-event intake, scheduled evaluation dispatch, and learning approval.

`marketing/decision_quality.py` owns a pure offline synthetic-scenario Decision Dossier grader. It
compares a typed dossier with a frozen scenario for ICP support, positioning claims, complete
evidence disposition, explicit counterevidence, freshness, and bounded next action. It is neither a
provider runner nor production authority. `marketing/hosted_judgment.py` produces and validates only
the live `new_launch` dossier; `marketing/hosted_reassessment_judgment.py` consumes only an immutable
live experiment evaluation and prior brief for `experiment_result`, `performance_regression`, or the
evaluation's publication `tool_failure`. Their Cloudflare callbacks independently revalidate frozen
input before storage. Neither path runs the offline evaluator, and live market-event reasoning is
still absent. The grader, dossier, and reassessment have no tool or publication authority.

`marketing/marketing_judgment_canary.py` owns an observe-only repeated-provider canary for that
reassessment leaf. It gives the evaluated runner only a frozen request, creates a fresh no-tool
workspace per trial, records the observed executable/package and requested model plus prompt/schema
provenance, grades the existing typed decision contract, predeclared evidence/hypothesis direction,
and human-authored semantic anchors, and requires a same-situation outcome-evidence pair to differ on
predeclared decision fields. `marketing_judgment_canary_corpus.py` separately loads the runner inputs
and grader expectations. File separation prevents accidental prompt exposure but does not make a
same-process runner confidential; a trusted caller must provide process/mount isolation. Nor does the
requested model field attest the model actually served by the provider. This canary measures one
outcome-judgment vertical only after real trials are run; it does not validate research-tool choice,
source truth, creative quality, or market lift.

`cloudflare/src/marketing-review.js` owns only read models over that immutable/append-only ledger.
It selects pending strategy, media-plan, next-experiment-draft, or learning-candidate decisions from their exact state and
unreviewed target digest, and builds the versioned queue and review packet. Its action template is a
projection of the existing approval endpoint—not a new mutation API or authority source. The module
does not query customer-signal or context-snapshot payload tables, and `marketing-agent.js` guards
both review routes with control-plane authority before delegating to it. Artifact-manifest read
models deliberately retain only IDs, content/input/binding digests, and safe capture provenance:
URI, raw manifest payload, and adapter descriptor stay behind their effect owner rather than becoming
an incidental review-token transport.

`cloudflare/static/workspace-agent.js` is the browser presentation client for the agent loop. It owns
only the Codex product-evidence preparation prompt, a memory-only agent control token,
hosted-account-scoped run/campaign/review reads, hosted run submission, safe text rendering, and
submission of the server-projected exact approval action. It does not run Codex in the browser or
hold a worker credential. Future Slack and Kakao clients must call the same run API rather than
reimplementing orchestration.
`workspace-live.js` continues to own candidate, Mac worker/Appium, and Threads UI behavior; the agent
client does not import, wrap, or duplicate those effect paths. `build-workspace.mjs` copies both
clients as separate static assets.

Migrations `0019`–`0041` own the execution/observation/reassessment/next-experiment, successor-activation, forward-only activation admission hardening, hosted agent-run lineage, capability-receipt ledger, intent snapshot, run-step and bounded-resume lineage, the durable campaign-delegation outbox, and bounded journey traversal indexes, assisted-shadow origin binding,
quarantined reference snapshots, immutable source-byte receipts, and assignment-specific artifact proof. Existing candidate review,
native capture, and `threads/*` modules remain the only effect owners; marketing-agent code refers to them by immutable IDs rather than reimplementing them.

`0031_marketing_worker_task_events.sql` preserves the existing capture/generation execution
timeline while extending its closed task-kind contract to marketing judgments. Migration prefixes
are unique, so fresh installation and an upgrade from main's `0017_worker_task_events.sql` apply the
same order.

`0025_marketing_context_signals.sql` owns account-scoped, immutable `CustomerSignal` payloads,
their one-time human review decision, and immutable `MarketingContextSnapshot` records. The hosted
route accepts only a manually normalized signal in this first version and rejects dedicated raw-text
or connector-record fields; the human reviewer is still responsible for the normalization itself.
`marketing-agent.js` builds the snapshot only from approved, consented, fresh signals whose retention
and freshness both cover the snapshot expiry, then projects only the allowlisted summary to a
campaign task. Context reads and every shadow or assisted campaign creation require control-plane authority. A
campaign binds the snapshot ID and digest immutably; its callback re-derives the same projection from
D1 before accepting the receipt. The optional `marketing_context` member of
`trace.context-receipt.v1` is therefore an additive receipt binding, not a replacement for the source
signal ledger.

`marketing-adapter-capabilities.js` owns canonical catalog validation, server-derived binding
digests, frozen-task comparison, and current-action admission. Creative planning queries the known
creative capability IDs as optional account installations, freezes only the active subset, and
derives executable formats from that subset. It stops when the active combination cannot complete
any format. Dispatch rechecks only the bindings frozen by that plan, so enabling an additional tool
does not invalidate in-flight work while disabling or changing a selected tool still fails closed.
`0024_marketing_adapter_capabilities.sql`
and `0026_marketing_copy_capability.sql` own account-scoped registrations and receipt-scoped immutable
bindings. `0026` provisions active `copy.text`, rejects blank/mismatched request or manifest bindings,
and prevents binding updates. Neither adds a generic dispatcher or moves capture/Threads effect
ownership: `hosted-workspace.js` gates capture queueing, `index.js` verifies binding/provenance before
R2, and existing effect owners execute. `hosted-capture-manifests.js` owns the deterministic
task-time capture manifest, approved/succeeded retry admission, provenance validation, and immutable
manifest recording; it has no capture or publication effect of its own.

`marketing/runtime.py` owns the provider-neutral, local session-and-dispatch harness. It has no
Cloudflare, Appium, Threads, or model-provider import. `ToolCapability` owns both descriptor and
request-schema digests. `bind_tool_invocation` is the single construction boundary for a
`BoundToolInvocation`: canonical non-secret request JSON, schema version, and a `ToolCall` whose
digest binds capability, schema, payload digest, idempotency, and effect class. `ToolBackend`
receives this envelope rather than a digest-only call; connector-secret resolution remains with the
adapter owner. `MarketingAgentRuntime` admits one invocation at a time, reserves budget, requires
and consumes an exact one-use grant for external effects, and validates the returned receipt against
the pending call and approval digest. Effect classes are a closed runtime policy set (`observe`,
`local_artifact`, `control_plane_write`, and `external`), so an unknown or misspelled class cannot
silently bypass the external-effect approval rule. `request_persisted_tool` CASes the call and invocation;
`execute_persisted_tool` CASes an execution-start event before it calls a `ToolBackend`, and a
restart-recovered execution can only be closed by `reconcile_interrupted_execution`. On reload,
`JsonSessionStore` replays the closed runtime-event grammar from a hashed v3 `session_started`
header. It rejects a missing/mismatched pending invocation, rewritten budget or authority checkpoint,
invalid event digest/time, unknown reserved event, or an event after finalization. It supplies
host-local append-only CAS persistence, file locking, atomic replacement, and serialization
integrity checking for replay tests; it is not a distributed lease or production control-plane store.
`feature_launch_run.py` additionally binds the local research account to the hosted handoff, checks
the exact compact UTF-8 request size against the control plane's 64 KiB limit before research, and
requires the authenticated account to round-trip in every successful create or status response.
`replay_session(events)` is the matching public read-only reducer for an exported v3 trace; it returns
only the checkpoint re-derived from that ledger. The persisted admission and execution methods remain
the only public effect APIs; non-durable transforms are private test primitives. General planner, skill-registry, context-projection, and
outcome-evaluation owners remain separate from effect adapters; the implemented fake-backend
verticals are described below. Verified pre-header v1/v2 terminal traces are read-only; pre-header
pending/non-terminal sessions and all legacy saves fail closed.

`marketing/planning_projections.py` owns `FeaturePlanningProjection`, the shared data-only planner
projection for the Feature Launch and Evidence Research verticals. It contains packet identity/digest,
lifecycle, and claim IDs only; raw claim text, source references, evidence payloads, capability data,
and instructions remain outside planner context.

`marketing/feature_launch_evidence_brief.py` owns the immutable contract between completed Evidence
Research and a new Feature Launch session. It contains only research-trace provenance digests,
scope-complete receipt-bound observation digests, bounded semantic summaries/caveats/trust states,
and allowed supported claim IDs, plus the data-only projection used by the Feature Launch planner.
Raw sources, URLs, and locations stay out. It also owns the narrow verifier protocol and its
failure type, but imports no runtime, planner, registry, hand, or session owner.
`evidence_research_operator.py` alone converts an already terminal validated research trace into this
contract and supplies the local verifier that reloads and re-derives its source session. Before its
first brief commit, `feature_launch_operator.py` requires that verifier to pass; it depends on the
protocol, not the research runtime. No module combines the two sessions or transfers a research tool
authority into launch.

`marketing/feature_launch_operator.py` owns the first, narrow reasoning vertical over that harness.
It defines `MarketingGoal`, a strict `DecisionProposal`, one versioned skill registry action, receipt-
bound observation, and deterministic process/outcome graders. The planner can return a proposal but
never a `ToolCall`; `FeatureLaunchSkillRegistry` derives the call from the pinned feature packet,
approved claim set, evidence-brief-supported claim set, action schema, and descriptor. It commits
exactly one source-verified evidence brief before its goal, and propagates the brief digest and selected
research observation IDs through proposal, derived call, observation, and evaluation. The planner
receives only the shared product projection and a brief projection containing bounded evidence
summaries, caveats, trust states, and lineage—not raw source text or URLs. It
revalidates a persisted decision, observation, and evaluation against the registry, runtime receipt,
and event-time prefix before finalizing; terminal sessions audit that trace without calling a hand.
This module accepts only an observe effect class and has no Cloudflare or live-channel backend.

`marketing/evidence_research_operator.py` owns a separate bounded research loop over the same
runtime. Its registry maps the three distinct research scopes—product truth, customer intelligence,
and market evidence—to canonical versioned observe-only actions; it derives each call from the pinned
goal, feature packet, decision, and action schema. The planner can emit a typed decision but never a
raw call. It receives `ResearchObservationSummary` plus `FeaturePlanningProjection`: bounded semantic
signals/caveats/trust state are included; the local hand removes recognizable URLs and known proposal
source/packet-claim literals, while the remaining model string stays untrusted. Provider, model, and
planner protocol are pinned in the goal and checked on every decision. The protocol digest includes
the actual stable prompt-prefix bytes rather than relying only on a manually bumped version.
The evaluator closes a scope only from a
receipt-bound sufficient observation and revalidates each persisted decision/receipt/observation and
historical evaluation against its trace prefix before another hand can run. The module owns replay of a
committed decision, terminal trace audit without hand reinvocation, the at-most-three-step stop
condition, and deterministic completed/inconclusive evaluation; it does not own a live research
provider, Cloudflare adapter, campaign mutation, or publication. It can only freeze a completed
validated trace as the contract owned by `feature_launch_evidence_brief.py`; it cannot start Feature
Launch or merge the two sessions.

`marketing/marketing_os_scorecard.py` owns a pure offline evaluation contract rather than a planner,
runtime, tool, or provider adapter. Each named corpus case separates the runner-visible packet/scope
input from its grader-only expectation and test-only tool environment. The runner returns canonical
terminal event traces plus an attempted brief—not self-reported quality booleans—and the scorecard
replays the traces through the runtime reducer before it derives budget, brief lineage, claim
containment, process, and environment grades. A pinned grader-side vertical verifier re-runs the
Research and Feature Launch trace contracts, checks each terminal fixture receipt against the
test-owned authority that issued it, and compares each Research or Feature Launch observation with the
full authority record stored before trace append. Its failure makes a trial invalid and prevents a
launch or research outcome from passing, regardless of whether a safe expected outcome is
`inconclusive`.
The report pins the corpus digest and runner/model/prompt/registry metadata. This versioned regression
corpus proves local vertical behavior only; it is neither private held-out model evidence, hosted
authority, nor a live marketing result.

`marketing/marketing_os_scorecard_corpus.py` owns the narrow private-grader corpus loader. A trusted
grader process supplies one mounted corpus directory; the loader resolves only its fixed
`runner_inputs.json` and `grader_expectations.json` children, validates strict envelopes and matching
case-ID sets, and preserves the input-file order before it returns the existing case contract. It does
not load tool environments, select a case, run a provider, or provide fallback/public fixture data. It
does not make an in-process runner confidential: private expectations require a separate grader
process and mount, and a future comparable grader-environment digest is a separate report contract.

The legacy `MarketingWorkflow` / `MarketingAccountAgent` tables and Durable Object storage are not
the owner of new strategy state. Existing `hosted-workspace.js`, native capture modules, and
`threads/*` modules keep their present responsibilities and will be referenced through immutable
IDs and receipts rather than generalized or reimplemented.
