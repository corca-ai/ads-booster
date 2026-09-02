# Code Architecture

Status: Active
Last reviewed: 2026-09-02

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
| `native_capture.py` | hosted payload/context, request paths, native preview PNG validation | UI selectors, image editing |
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
`ads_booster.contracts.marketing_context` separately owns the allowlisted customer-signal and
campaign-context shapes: its full signal retains provenance and consent, while its planning
projection deliberately excludes both. D1 migration
`0017_marketing_agent_foundation.sql` owns the new `agent_v1` persistence epoch and its shadow
no-tool-action guard. `marketing/hosted_judgment.py` owns validation, private workspace admission,
the schema-constrained strategy turn, claim/reference quarantine, approved-context prompt boundary,
and the bound result. Cloudflare
`marketing-agent.js` owns campaign ingestion and task creation;
`hosted-marketing-judgment-callback.js` independently validates and atomically persists successful
strategy state. The generic worker broker owns leasing and callback transport only.

`marketing/hosted_creative_judgment.py` owns the proof-first MediaPlan proposal and creates no tool
action. `marketing/hosted_candidate_judgment.py` materializes one approved, evidence-bound
candidate; `marketing/hosted_reference_research.py` returns an immutable quarantined observation
snapshot; and `marketing/hosted_learning_judgment.py` creates only a reversible learning candidate.
`marketing/hosted_experiment_evaluation.py` is deterministic and has no model or external effect.
`experiment-evaluation.js` is its pure control-plane re-deriver: it accepts only the frozen request
and reproduces its conclusion, coverage, lineage, and guardrail result. The experiment-evaluation
callback canonical-compares the worker output to that derived value and persists the derived value
only after the frozen registration digest also matches D1. Their Cloudflare callbacks independently
validate every receipt, claim, plan, assignment, and approval binding before writing a projection.
Learning synthesis receives a server-derived `MarketingLearningApplicability`; its model may explain
scope in prose but cannot broaden the selector. The callback binds that selector into the candidate,
re-derives it from D1's evaluation/campaign/packet/account lineage, and rejects drift before a write.
Learning approval copies it into the immutable principle, and `marketing-agent.js` SQL-narrows on every
selector member before the bounded lookup, then applies a defensive exact canonical match before a
new campaign's knowledge snapshot receives a principle. Legacy principles without the selector are
read but never auto-applied. `marketing-agent.js` also owns assisted campaign gating, variant links,
product-event intake, scheduled evaluation dispatch, and learning approval.

`cloudflare/src/marketing-review.js` owns only read models over that immutable/append-only ledger.
It selects pending strategy, media-plan, or learning-candidate decisions from their exact state and
unreviewed target digest, and builds the versioned queue and review packet. Its action template is a
projection of the existing approval endpoint—not a new mutation API or authority source. The module
does not query customer-signal or context-snapshot payload tables, and `marketing-agent.js` guards
both review routes with control-plane authority before delegating to it. Artifact-manifest read
models deliberately retain only IDs and digests: URI and manifest payload stay behind their effect
owner rather than becoming an incidental review-token transport.

Migrations `0018`–`0023` own the execution/observation lineage, assisted-shadow origin binding,
quarantined reference snapshots, and assignment-specific artifact proof. Existing candidate review,
native capture, and `threads/*` modules remain the only effect owners; marketing-agent code refers to them by immutable IDs rather than reimplementing them.

`0024_marketing_context_signals.sql` owns account-scoped, immutable `CustomerSignal` payloads,
their one-time human review decision, and immutable `MarketingContextSnapshot` records. The hosted
route accepts only a manually normalized signal in this first version and rejects dedicated raw-text
or connector-record fields; the human reviewer is still responsible for the normalization itself.
`marketing-agent.js` builds the snapshot only from approved, consented, fresh signals whose retention
and freshness both cover the snapshot expiry, then projects only the allowlisted summary to a
campaign task. Context reads and a context-bound shadow campaign require control-plane authority. A
campaign binds the snapshot ID and digest immutably; its callback re-derives the same projection from
D1 before accepting the receipt. The optional `marketing_context` member of
`trace.context-receipt.v1` is therefore an additive receipt binding, not a replacement for the source
signal ledger.

`0023_marketing_adapter_capabilities.sql` owns account-scoped adapter registrations and
context-receipt-scoped immutable capability bindings. It records descriptor/schema digests, owner,
effect class, enabled state, and whether a capability is active or reference-only; it does not add a
generic dispatcher or move capture/Threads effect ownership.

`marketing/runtime.py` owns the provider-neutral, local session-and-dispatch harness. It has no
Cloudflare, Appium, Threads, or model-provider import. `ToolCapability` owns both descriptor and
request-schema digests. `bind_tool_invocation` is the single construction boundary for a
`BoundToolInvocation`: canonical non-secret request JSON, schema version, and a `ToolCall` whose
digest binds capability, schema, payload digest, idempotency, and effect class. `ToolBackend`
receives this envelope rather than a digest-only call; connector-secret resolution remains with the
adapter owner. `MarketingAgentRuntime` admits one invocation at a time, reserves budget, requires
and consumes an exact one-use grant for external effects, and validates the returned receipt against
the pending call and approval digest. `request_persisted_tool` CASes the call and invocation;
`execute_persisted_tool` CASes an execution-start event before it calls a `ToolBackend`, and a
restart-recovered execution can only be closed by `reconcile_interrupted_execution`. On reload,
`JsonSessionStore` replays the closed runtime-event grammar from a hashed v3 `session_started`
header. It rejects a missing/mismatched pending invocation, rewritten budget or authority checkpoint,
invalid event digest/time, unknown reserved event, or an event after finalization. It supplies
host-local append-only CAS persistence, file locking, atomic replacement, and serialization
integrity checking for replay tests; it is not a distributed lease or production control-plane store.
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
scope-complete receipt-bound observation digests, and allowed supported claim IDs, plus the data-only
projection used by the Feature Launch planner. It also owns the narrow verifier protocol and its
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
receives only the shared data-only product and evidence-brief projections rather than raw evidence text. It
revalidates a persisted decision, observation, and evaluation against the registry, runtime receipt,
and event-time prefix before finalizing; terminal sessions audit that trace without calling a hand.
This module accepts only an observe effect class and has no Cloudflare or live-channel backend.

`marketing/evidence_research_operator.py` owns a separate bounded research loop over the same
runtime. Its registry maps the three distinct research scopes—product truth, customer intelligence,
and market evidence—to canonical versioned observe-only actions; it derives each call from the pinned
goal, feature packet, decision, and action schema. The planner can emit a typed decision but never a
raw call. It receives `ResearchObservationSummary` plus `FeaturePlanningProjection`: both deliberately
exclude raw sources, claim text, and instructions. The evaluator closes a scope only from a
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
Research and Feature Launch trace contracts; its failure makes a trial invalid regardless of whether a
safe expected outcome is `inconclusive`. The report pins the corpus digest and
runner/model/prompt/registry metadata. This versioned regression corpus proves local vertical behavior
only; it is neither private held-out model evidence, hosted authority, nor a live marketing result.

The legacy `MarketingWorkflow` / `MarketingAccountAgent` tables and Durable Object storage are not
the owner of new strategy state. Existing `hosted-workspace.js`, native capture modules, and
`threads/*` modules keep their present responsibilities and will be referenced through immutable
IDs and receipts rather than generalized or reimplemented.
