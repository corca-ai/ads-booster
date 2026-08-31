# Threads Marketing Agent

Status: Draft — P0, P1, and no-effect proof-first P2 planning are implemented. P3–P5 contracts,
ledgers, conservative evaluation, and safety bindings exist, but product-event ingestion,
installed-evidence campaign promotion, artifact execution, scheduled evaluation, and learning
promotion runtime remain unimplemented.

Last reviewed: 2026-08-31

## Problem

Trace needs an agent that can market a newly implemented product capability, not a scheduler that
merely fills an established post template. The agent must understand version-pinned product truth,
form differentiated marketing hypotheses, choose the proof and creative treatment, pre-register a
bounded Threads experiment, interpret incomplete evidence conservatively, and accumulate reversible
learning. Appium, native capture, composition, Figma, generation, publishing, and analytics are
tools. They do not own strategy or learning.

## Capability contract

Given a Trace feature candidate and an account-scoped business outcome, the system creates a durable
campaign whose product claims, context, strategy, experiments, approvals, tool effects,
observations, and learning lineage can be reconstructed from D1. Each judgment is one ephemeral,
schema-constrained official Codex CLI turn on an enrolled Mac. Codex proposes a transition;
Cloudflare validates and commits it before independently dispatching any approved action.

```text
feature/build/release event
  -> FeatureEvidencePacket
  -> D1 campaign projection
  -> frozen ContextReceipt
  -> one marketing_judgment_v1 task
  -> StrategyBrief proposal
  -> deterministic validation and event append
  -> approved existing capture/publication tools
  -> metric and attribution observations
  -> LearningCandidate
```

No model conversation is canonical state. A source-only feature may be used for shadow strategy but
cannot open the publication gate. A human approval cannot turn missing evidence into product truth.

## Implemented slices

P0 establishes:

- `trace.feature-evidence.v1`, including atomic claims, evidence references, and a publication gate;
- `trace.strategy-brief.v1`, with exactly one control, challengers, proof requirements, and a
  pre-registered experiment;
- an explicit separation between direct-response attribution and a causal treatment estimate;
- `trace.context-receipt.v1` for feature, knowledge, capability, prompt, and output-schema digests;
- a new `agent_v1` D1 campaign/event epoch, separate from legacy `MarketingWorkflow` state;
- shadow campaigns that cannot create any tool-action row.

P1 adds:

- account-scoped source-only packet ingestion at `/api/marketing-agent/campaigns`;
- strict cross-runtime packet normalization and digest binding;
- a broker-gated `marketing_judgment` job and distinct official Codex CLI receipt;
- one private, ephemeral, schema-constrained shadow strategy turn;
- claim/reference quarantine, exact input/output receipt validation, idempotent callback storage,
  and a registered control/challenger experiment;
- explicit failure states and task-kind-specific ambiguous-effect codes.

P1 still creates no candidate, capture, publication, or tool-action effect. The source packet may
support a strategy hypothesis, but its closed publication gate prevents product-availability claims.

P2 adds an exact strategy-review transition and a second ephemeral `creative_plan` judgment. It
selects a proof narrative and typed artifact request for every active arm from native sequence,
bound recording, explanatory composition, design render, or copy-only planning capabilities. The
callback binds every treatment to its hypothesis claims and available capability snapshot, stores
the MediaPlan and requests, and proves `tool_actions_created: 0`. Exact MediaPlan review is durable.

P3–P5 foundation adds immutable artifact manifests, candidate/experiment assignments, existing
Threads publication lineage, versioned product-event and direct-response attribution tables,
experiment evaluations, learning candidates, and approval-bound principles. The deterministic
evaluator uses complete eligible blocks, coverage, windows, and guardrails; it refuses causal
claims without an eligible estimator and requires replicated independent campaigns before creating
a learning candidate. Shadow or source-only campaigns cannot bind a candidate.

## Fixed decisions

- The hosted workspace and its account scope are the product control plane.
- D1 owns durable marketing state; the Mac owns one official Codex CLI judgment process.
- New agent runs use `agent_v1`; legacy workflow rows are not dual-written or backfilled.
- The existing native capture path remains the product-evidence producer.
- The existing default-OFF `threads/*` modules remain the only Threads OAuth, publication,
  reconciliation, and engagement owners.
- `CandidateDraft` is an execution child of a strategy and experiment, not a strategy container.
- A shadow campaign never creates candidate, capture, publication, or tool-action effects.
- Product availability claims require fresh-installed artifact evidence.
- Creative review feedback and marketing-effect learning are separate lanes.
- One post, one external reference, or three creative rejections cannot create a marketing
  principle.
- Ambiguous effects remain `unknown_side_effect` and are never blindly retried.
- Initial live publishing and durable learning promotion require exact human approval.

## Probe questions

| Probe | Thin check | Signal | Writeback and gate |
| --- | --- | --- | --- |
| Cross-runtime schema owner | Generate JSON Schema from the Python contracts and validate one JS fixture | no hand-maintained semantic drift | P0 contract section before strategist dispatch |
| Strategist portfolio size | Blind review of 10 calibration cases | reviewer utility and review time | P1 eval config before shadow launch bar |
| Trace outcome event | Fresh-installed deep-link to setup-complete roundtrip | event version, coverage, dedupe behavior | P3 attribution contract before live experiment evaluation |
| Initial experiment budget | Account cadence inventory | eligible blocks within maximum horizon | experiment defaults before any canary |
| First creative proof | Compare verified PNG composition with bound screen recording | claim coverage and reviewer preference | MediaPlan provider choice before adding attended tools |

## Deferred decisions

- Automatic GitHub webhook ingestion resumes after the manual/polling AI-lock-screen packet can be
  rebuilt from an immutable commit.
- Historical legacy-run backfill resumes only when cross-epoch learning has a concrete consumer.
- Figma becomes a provider after deterministic composition cannot satisfy an approved MediaPlan.
- Generated video remains optional and cannot become product-behavior evidence.
- Multi-channel marketing resumes only after the Threads campaign loop has a replicated learning
  lineage.
- Pre-approved campaign-budget auto-publishing resumes only after an authorized canary, attribution
  coverage, and kill-switch drill all pass.
- Advanced causal estimators resume when eligible blocks and interference observations justify
  them; they are not used to manufacture signal from scarce posts.

## Non-goals

- A second Threads publisher, OAuth owner, or metric poller.
- A long-lived model thread or custom `trace-agent` / `trace-ads` entrypoint.
- A giant `CandidateDraft` with optional campaign, experiment, media, and learning fields.
- Reference imitation or copying a viral format as strategy.
- Treating attributed conversion lineage as causal proof.
- Automatic promotion of every high-engagement result.
- A generic multi-channel marketing platform in the first implementation.

## Durable entities

- `FeatureEvidencePacket`, `FeatureClaim`, `EvidenceReference`
- `MarketingCampaign`, `MarketingRunEvent`, `ContextReceipt`
- `StrategyBrief`, `MarketingHypothesis`
- `MarketingExperiment`, `ExperimentArm`, `PostAssignment`
- `CreativeTreatment`, `MediaPlan`, `ArtifactRequest`, `ArtifactManifest`
- `ApprovalGrant`, `ToolAction`, `ToolReceipt`
- `MetricObservation`, `AttributionObservation`
- `MarketObservation`, `LearningCandidate`, `MarketingPrinciple`, `KnowledgeSnapshot`

The event envelope carries aggregate sequence, prior/resulting revision, idempotency key,
causation/correlation identity, event time, observed time, actor, schema version, and payload digest.
The eventual runtime must atomically append the event, update the projection, and enqueue any action.

## Stages

### P0 — contracts and migration boundary

Implement the records above, `agent_v1`, context receipts, transition validation, schema generation,
and the shadow no-effect database gate.

### P1 — AI lock-screen evidence and shadow strategist

Pin the Trace repository ref to an immutable commit/tree, construct a source-only packet, block
availability claims, and run `marketing_judgment_v1` through the capability-gated Mac broker. The
agent generates product-first hypotheses before references are exposed and returns a control plus a
budget-feasible challenger portfolio.

Implemented for manually supplied immutable source packets. Automatic GitHub webhook ingestion and
fresh-installed runtime evidence remain deferred as stated below.

### P2 — creative orchestration

Add MediaPlan, typed artifact requests, provenance manifests, claim/evidence maps, deterministic
composition of verified native PNGs, and the human review packet. Bound screen recording and design
renderers are later capabilities, not alternate evidence rules.

Implemented through MediaPlan proposal and exact review. Artifact providers and manifest writeback
remain unimplemented.

### P3 — attribution

Trace owns versioned first-open, feature-start, generation, scheduling, and setup-complete events.
ads-booster owns variant redirects, ingestion, deduplication, attribution windows, missingness, and
coverage. Direct-response lineage remains distinct from an eligible causal estimate.

Schema and evaluation contract implemented; link/event ingestion and app instrumentation remain
unimplemented.

### P4 — existing Threads integration

Bind experiment arms and exact approval digests to the existing candidate/publication records. Keep
default-OFF, publish-once, authoritative readback, readback-only retry, token isolation, and
`unknown_side_effect` unchanged.

Nullable assignment lineage and publication snapshot propagation are implemented without changing
the existing publisher. Activation remains impossible from a shadow/source-only campaign.

### P5 — experiment ledger and learning governor

Store pre-registration, allocation, eligible blocks, maximum horizon, contamination, incomplete
windows, guardrails, and `inconclusive`. Automatic promotion stops at `observation -> candidate`;
provisional and durable principles initially require independent lineages, replication, and human
approval.

Ledger, conservative evaluator, replication gate, and exact learning-approval database trigger are
implemented. Scheduled observation/evaluation and principle-promotion APIs remain unimplemented.

### P6 — hardening and autonomy

Exercise duplicate/out-of-order inputs, stale approvals, worker mismatch, schema failure, callback
loss, publish ambiguity, missing metrics, reauthentication, tool drift, budgets, and kill switches.
Roll out from advice to strategy, artifact production, and exact-approved publication. Campaign
budget autonomy remains deferred.

## Success criteria and acceptance checks

| Criterion | Verification type | Acceptance check |
| --- | --- | --- |
| Source evidence cannot masquerade as installed availability | unit | a source-supported claim cannot enter an open publication gate |
| Strategy is a testable portfolio | unit | exactly one control, known active arms, a falsifier, held constants, guardrails, horizon, and `inconclusive` are required |
| Attribution is not mislabeled causal evidence | unit | direct-response outcomes reject a causal estimand; treatment effects require one |
| P0 does not change existing hosted behavior | integration | the ordered D1 migration preserves an existing candidate and account |
| Shadow has zero tool effects | integration | insertion of any shadow tool action fails and the table remains unchanged |
| Event lineage is deterministic | integration | duplicate sequence/revision and invalid revision increments fail; projection rebuild matches |
| P1 is auditable and useful | eval | frozen feature corpus parses at 100%, has zero hard-gate escape, and beats the current generator on a calibrated blind rubric without fidelity regression |
| Product claims match creative evidence | integration | claim wording that exceeds the source provenance class is rejected and artifact mutation stales approval |
| Attribution reaches the real product boundary | e2e | a fresh-installed Trace variant link reaches a deduplicated setup-complete receipt |
| Existing publication safety survives | integration/e2e | default-OFF, exact approval, publish-once, readback, and ambiguous-outcome checks remain green; authorized canary proves post ID and metrics |
| Sparse evidence does not become a winner | unit/eval | incomplete blocks, low coverage, guardrail regression, and failed replication end inconclusive or block promotion |
| Installed product remains usable | e2e | managed `trace-marketing` worker and deployed workspace complete their changed user paths without checkout-only assumptions |

## Boundary ownership

| Boundary | Producer | Consumer |
| --- | --- | --- |
| Product source/build/install truth | Trace repository/build plus fresh-install demo | FeatureEvidencePacket gate and reviewer |
| Durable campaign state | D1 event/projection runtime | strategist projection, workspace, dispatcher |
| Marketing judgment | official Codex CLI on enrolled Mac | deterministic validator; never a direct tool |
| Native product evidence | current Mac capture/manifest path | MediaPlan and human review through digest references |
| Threads effects | current Cloudflare `threads/*` modules | Experiment Ledger through publication/metric receipts |
| Product outcome events | versioned Trace app instrumentation | ads-booster attribution ingest and evaluator |
| Durable learning | LearningGovernor plus human promotion authority | later knowledge snapshots |

## Critique disposition

Acted before implementation:

- fixed hosted workspace and existing Threads modules as canonical owners;
- established an explicit new epoch instead of extending legacy workflow memory;
- placed official Codex execution behind a new capability-gated broker task;
- made shadow zero-effect a database invariant;
- separated direct-response attribution from causal estimation;
- moved no-promotion rules into the P0 contract rather than postponing them to P5;
- attached an acceptance check to every phase-level success claim.

Valid but deferred: legacy history backfill, advanced inference, attended design tools, generated
video, multi-channel rollout, and campaign-budget autonomy. Immediate legacy-table deletion is
over-worry until deployment usage is audited; a complete provider-neutral tool framework is also
outside the first useful slice.

## Canonical artifact

This document is the implementation contract. Research sources remain background evidence and do
not override current source, active architecture documents, focused tests, fresh-installed product
evidence, or deployed hosted behavior.
