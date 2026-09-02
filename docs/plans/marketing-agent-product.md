# Marketing Agent Product Strategy

Status: Draft — product direction and staged implementation contract. It does not authorize a new
publisher, CRM mutation, spend, customer outreach, or SaaS rollout.

Last reviewed: 2026-09-02

## Product thesis

Trace is building a marketing operating system, not a content generator or a generic autonomous
assistant. Given an approved product change and a business goal, the agent should discover grounded
evidence, propose a falsifiable experiment, prepare reviewable work through permitted tools, and learn
only from measured outcomes and explicit human promotion.

```text
trusted product / customer / market observations
  -> bounded research and evidence brief
  -> campaign hypothesis and experiment portfolio
  -> reviewable artifact and approval decision
  -> permitted channel or production adapter
  -> outcome observation and conservative evaluation
  -> learning candidate -> human promotion -> future context snapshot
```

Tools such as Appium, Threads, Figma, video generation, CRM, Slack, browser context, and a scheduler
are interchangeable hands at the edge of this loop. They do not decide strategy, grant authority, or
write long-term marketing knowledge.

The defensible wedge is **evidence-to-experiment for newly shipped product capabilities**. It begins
with Trace internally because that gives a real product-truth source, review workflow, and outcome
boundary. It must prove lower reviewer effort and a better next experiment before expanding into a
general multi-channel marketing product.

## Current truth

Implemented provider-neutral foundations:

- append-only, restart-safe v3 session trace with a hashed immutable session header and a
  fail-closed runtime-event reducer; a capability/schema-bound `BoundToolInvocation` with canonical
  non-secret request payload, receipt, budget, approval grant, idempotency, and reconciliation
  contracts;
- an observe-only evidence-research vertical across product truth, customer intelligence, and market
  evidence;
- a separate Feature Launch experiment vertical whose planner cannot create a raw tool call;
- immutable research-to-launch evidence briefs, safe planner projections, receipt-bound evaluation,
  source-session re-derivation at the handoff boundary, and explicit `inconclusive` on missing or
  counter-evidence;
- a hosted Trace campaign ledger with product evidence, strategy, experiment, approval, attribution,
  and conservative learning ownership. A human-approved learning has a prose explanation plus a
  server-derived exact applicability selector; only a matching future campaign may receive it, and
  legacy prose-only principles are not auto-applied. Existing Cloudflare, Threads, and Appium owners
  remain intact.
- an account-scoped customer-intelligence reference lane: manual normalized signals, one final human
  review decision, immutable context snapshots, safe task/receipt projection, and callback rebinding.
  It is deliberately not a transcript or CRM connector.

Not implemented or claimed:

- a real provider/model evaluation corpus, trusted remote adapter receipts, execution-time
  approval revalidation, provider idempotency/reconciliation, cross-session external approval
  authority, or a hosted runtime adapter;
- a customer-interview/CRM connector, shared multi-product tenant context, approval inbox,
  collaboration surfaces, billing, or public SaaS onboarding;
- automatic post publication, ad spend, cold outreach, generic CRM mutation, or autonomous budget
  control.

## Market position

Jasper, HubSpot, Copy.ai, Clay, Adobe, Canva, Predis, Lately, and Fastlane show that useful marketing
products need shared brand context, specialized jobs, scalable workflows, approval, and performance
feedback. Their common risk is collapsing those concerns into prompt memory, a generic workflow, or
a scheduler.

Trace differentiates by making each transition attributable and reversible:

- product claims remain tied to version-pinned evidence and a publication gate;
- raw sources, customer material, and viral references cannot mutate authority or playbook knowledge;
- an approval binds an exact proposal and effect, rather than approving a vague campaign;
- outcomes distinguish descriptive attribution, causal evidence, qualitative feedback, and missing
  coverage;
- a single result is a learning candidate, not new agent memory.

This is stronger than claiming more agent roles. It is the basis for a trustworthy "AI marketing junior
employee" rather than an uncontrolled content factory.

## Product boundaries

### Open core

The open core must be useful on its own and share contracts with the hosted product:

- typed goal/session/capability/receipt/approval/evaluation contracts;
- deterministic runtime, append-only trace, replay/reconciliation rules, local store, and fake
  adapters;
- skill and evaluator protocol, redacted trace export, and test-owned evaluation fixtures;
- local capability SDK/contract tests for a connector author.

It must never require a hosted secret, collect tenant content by default, or include a hidden hosted
runtime path.

### Hosted Marketing OS

The commercial service removes real operating burden without forking the core:

- workspace and member scope, RBAC, encrypted connector secrets, retention, audit, and managed
  worker fleet;
- approved `MarketingContextSnapshot`, customer-signal store, skill registry, policy, and evaluation
  registry;
- campaign queue, evidence trace, approval inbox, outcome/learning view, alerts, and observability;
- hosted connectors, scheduler, collaboration surfaces, usage metering, and billing.

Slack, KakaoTalk, browser sidecars, voice onboarding, and dashboards are clients of this control-plane
API. None creates an alternative authority or publishing path.

## Durable product model

The following are separate entities, not optional fields on a god campaign object.

| Entity | Role | Authority rule |
| --- | --- | --- |
| `MarketingContextSnapshot` | approved brand, product, audience, business goal, claim and channel policy frozen for one campaign | read-only planner input; later edits create a new snapshot |
| `CustomerSignal` | consent-, provenance-, freshness-, and confidence-labeled customer observation | never auto-promotes into playbook or policy |
| `Campaign` / `ExperimentPortfolio` | hypothesis, control/challengers, metric, budget, window, stop rule | binds existing experiment ledger; does not own channel effects |
| `ArtifactManifest` | asset, claim, template, policy checks, and provenance | artifact revision invalidates earlier approval |
| `ApprovalRequest` / `ApprovalGrant` | reviewer-visible proposed effect and exact authority | decision binds proposal/call/policy digests, expiry, revoke/use state |
| `OutcomeObservation` | descriptive, causal, or qualitative result with coverage | source of evaluation, never a direct playbook write |
| `LearningCandidate` / `MarketingPrinciple` | scoped possible learning and approved reusable principle | candidate requires counter-evidence and human promotion |
| `SkillCard` | task contract, permitted actions, input/output/evidence rules, budget and evaluator owner | a skill documents procedure; it cannot grant capability |

`MarketingContextSnapshot` and `CustomerSignal` have a narrow implemented reference lane: an
account-scoped manual normalization becomes pending, receives one human decision, then may be selected
into an immutable snapshot. The campaign and its receipt carry only the allowlisted planner projection;
the source provenance and consent record remain in the signal ledger. Connector ingestion, a
multi-product tenant context, and an approval-inbox UX remain planned. Existing feature packets,
artifact, approval, outcome, and learning contracts retain their current owners.

## Staged roadmap

### 0. Prove the decision loop — now

Implemented baseline: a test-owned, versioned five-case adversarial regression corpus for:

```text
Evidence Research session -> immutable Evidence Brief -> Feature Launch session
```

It grades environment outcome and trace process separately. Runner input contains only an opaque case
ID, feature packet, research scopes, and budget; test tool behavior and the grader reference are kept
outside it. The runner returns raw canonical terminal traces and an attempted brief. The scorecard
replays those traces through the runtime reducer and derives bounded tool calls, cost, claim
containment, brief lineage, terminal state, and typed reasons without accepting self-reported quality
booleans. The first implemented cases cover normal completion, insufficient customer evidence,
counter-evidence, blocked claims, and evidence-brief mismatch. A fluent final answer cannot pass this
corpus alone.

The grader pins a vertical verifier that re-runs Research and Feature Launch contracts. Failure is an
invalid trial rather than an expected `process_passed: false` result, so a canonicalized fabricated
trace cannot pass by imitating a safe blocked-claim stop.

Next, extend the regression baseline with forged receipt/evaluation, duplicate dispatch, restart after
execution-start, and stale/revoked approval; use a private corpus loader plus real provider/model
repeated trials for model validation. Do not call the source-visible baseline a model-quality or
market-effectiveness result.

Exit: a cross-session run is reproducible from fixtures; each failure has a typed reason; an independent
review can distinguish a regression in a skill, tool, evaluator, or runtime.

### Immediate implementation sequence

The first named scorecard now executes a completed Research session, an immutable brief, and a distinct
Feature Launch session through five adversarial regression scenarios. It independently replays exported
canonical event traces and re-derives brief lineage, claim containment, tool calls, and cost instead of
accepting runner summaries. The runtime now also has a `BoundToolInvocation`: a schema-bound,
canonical, non-secret request that is persisted with the pending call and received by the backend.
One UTF-8 canonical JSON policy covers event, call, and request digests; reload rejects a missing or
mismatched invocation and an execution checkpoint that contradicts its committed start event. The
serialization format is v3: its first event freezes the session ID and budget, and reload
re-derives every runtime authority/cache field from the closed event grammar. Verified pre-header
v1/v2 terminal traces are read-only, while pre-header pending or non-terminal traces fail closed
rather than being upgraded or re-executed.
The next additions stay in this order:

1. extend the named test-owned scorecard with more adversarial traces, then add a private corpus
   loader and model/provider trials before treating it as model-quality validation;
2. turn the implemented account-scoped context/signal reference lane into a reviewer-visible approval
   packet and campaign queue without widening its raw-data boundary;
3. then introduce consented connector ingestion and a multi-product tenant context only after their
   retention, access-control, and deletion contracts exist.

Deliberately not doing in this slice: a generic multi-agent graph, tool-specific logic in the
strategist, raw interview or web text in planner context, a new publisher, or an autonomous budget.

Before an effect-capable hosted adapter is admitted, add a persisted brief-verification marker and an
explicit compatibility policy for pre-marker launch sessions. Current local replay validates an already
committed brief from its canonical launch trace without reopening the research source; it must not
silently grant that legacy behavior to a new external-effect path. The same pre-effect gate must add
all of: live approval/revocation verification immediately before the effect, a versioned verifiable
receipt proof bound to the invocation, a validated capability-registry manifest digest, a closed
effect-class enum, and provider idempotency/readback/reconciliation rules. It must also derive the
authorization, budget, idempotency, and terminal/reconciliation checkpoint from a hosted monotonic
authority ledger before treating that checkpoint as external-effect authority. The local runtime now
performs the equivalent reducer check for its own ledger, but it is not a distributed authority. A
generic JSON-schema engine, secret-pattern scanner, or production connector is intentionally not part
of this foundation.

### 1. Governed marketing context and signals — reference lane implemented

The first vertical is complete: `CustomerSignal` and `MarketingContextSnapshot` are provider-neutral
contracts backed by an account-scoped D1 ledger. A manual normalized signal is immutable, pending
until one authorized review decision, and may enter a snapshot only when approval, consent, freshness,
and retention cover the full snapshot lifetime. The API rejects dedicated raw-text fields, but the
reviewer—not semantic text detection—remains responsible for normalization. The planner receives a
compact allowlisted projection without source references, source digests, or consent metadata, and
treats it as context rather than instructions. Context reads and a context-bound campaign require
control-plane authority; an expired projection is rejected before the Mac opens the model. Campaign
creation freezes the snapshot ID/digest and the research handoff plus judgment callback rebind that
projection before accepting a strategy receipt.

This is not a customer-interview, CRM, or retrieval connector. Those sources remain isolated until
their consent, retention, deletion, and access contracts are specified. `retention_until` currently
means a use/visibility deadline rather than physical deletion of immutable audit copies; that privacy
deletion contract remains explicitly deferred.

### 2. Close the reviewable campaign loop

Expose the existing experiment ledger through one orchestrator contract and create a common approval
packet: evidence, claim diff, preview/manifest, cost, blast radius, success definition, rollback, and
exact approve/revise/reject/stop decisions. Add static policy checks before, and receipt/readback after,
any adapter effect.

Exit: a reviewer can make one informed decision without reconstructing model context, and a later audit
can reproduce exactly what that decision authorized.

### 3. Make capabilities additive

Publish a capability SDK plus contract tests. Skills such as OSMU, SEO/GEO, community research,
outbound preparation, creative planning, or dynamic landing-page analysis must declare their evidence,
authority, budget, effect class, and evaluator. They add adapters and tests; they do not modify the
strategist, playbook governor, or existing channel owners.

Exit: a new connector can be introduced through registration, schema/receipt tests, and a bounded
skill, with no generic-agent rewrite.

### 4. Launch the first SaaS surface

Build a weekly campaign queue with evidence trace, experiment cards, approval inbox, and
outcome/learning view. Validate it with two or three internal Trace campaign cycles before charging.
Measure reviewer override rate, time-to-approved experiment, evidence completeness, useful experiment
cost, and eligible outcome coverage. Use the evidence—not feature breadth—to set packaging and price.

Exit: users can approve or stop a campaign faster than their current spreadsheet/chat process, and the
system produces a verifiable next action after the observation window.

### 5. Expand surfaces and autonomy deliberately

Add Slack/KakaoTalk/sidecar/voice onboarding as discovery and approval clients. Add channel adapters
only when their approval, readback, kill switch, and unknown-side-effect behavior pass the same core
contract. Pre-approved budget autonomy is a later, separately authorized capability.

## Design constraints

- Prefer a small, explicit workflow when the job is known; use an agent loop only where evidence gaps
  require genuine judgment.
- Replaying verifies committed history. Re-running creates a new attempt/session. Unknown external
  effects never auto-retry.
- Memory, working context, source observations, outcomes, and approved learning stay in distinct
  stores with explicit promotion boundaries.
- Start every new skill with a representative evaluation and tool contract, not a prompt or channel
  integration.
- Revenue, views, and engagement are product outcomes to observe; they are not sufficient proof of
  agent quality or causal success.

## References informing this plan

- [Jasper Agents](https://www.jasper.ai/agents) — shared brand context and human-orchestrated
  specialized marketing jobs.
- [Copy.ai GTM workflows](https://www.copy.ai/gtm-ai-planning) and
  [Claygent Builder](https://university.clay.com/docs/claygent-builder) — reusable workflows/skills
  and structured GTM data.
- [Anthropic: Building Effective AI Agents](https://www.anthropic.com/engineering/building-effective-agents)
  and [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
  — simple composable control flow, tool-grounded progress, and trace plus environment evaluation.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and
  [human interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) — durable state and
  reviewable pause/resume patterns.
- [Temporal Cloud architecture](https://web.temporal.io/blog/temporal-cloud-1-000-customers-1-000-thank-yous)
  and [Langfuse's open-source strategy](https://langfuse.com/handbook/chapters/open-source) — shared
  core concepts across OSS and hosted operations.
