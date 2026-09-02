# Marketing Agent Platform

Status: Draft — establishes the product and architecture contract before multi-tenant platform
implementation. Trace is the first reference tenant and evidence-producing integration, not the
definition of the product. The hosted campaign ledger now carries a narrow customer-context reference
lane, while the existing Cloudflare automation and effect owners remain intact; this is not a rewrite
of that automation.

Last reviewed: 2026-09-02

## Product thesis

This product is a marketing operating agent for software teams that have product capability but not
the continuous customer-research, creative, experimentation, and learning capacity to turn that
capability into demand. It does not sell “automatic posting” or “AI video generation.” Those are
replaceable execution adapters.

The durable customer capability is: **turn a product change and customer signals into a bounded,
evidence-backed marketing experiment, execute approved work through connected tools, and improve the
next experiment from measured outcomes.**

## Product correction: agent runtime over control plane

`Appium`, persona, video, Figma, CRM, and channel publication are tools, not product modes. The
product is a marketing agent that dynamically chooses the useful research and execution tools for a
goal, learns from their receipts and outcomes, and asks a human only at meaningful authority
boundaries.

```text
Marketing Agent Runtime
  goal -> orient -> plan -> choose skills/tools -> observe -> evaluate -> revise or stop
                 |                                   |
                 +-------- human checkpoint ---------+

Marketing Control Plane
  evidence / authority / idempotency / approval / receipt / outcome / audit

Information & effect tools
  product source, customer calls, web research, analytics, Appium, creative, CRM, channels
```

This follows the useful distinction in [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents):
fixed validation and irreversible-effect gates stay deterministic workflows; the runtime uses an
agent loop only when the next research question, creative route, or tool cannot be predicted in
code. It re-plans from actual tool receipts, never assumed success.

Cloudflare/D1 is therefore neither the planner nor a product mode. It is a trusted control-plane
tool backend: it checks scope and approval, invokes the existing effect owner when authorized, and
returns typed receipts. The existing Trace capture and Threads automation remain such owners.

### Runtime contracts and memory

| Contract | Required content |
| --- | --- |
| `MarketingGoal` | outcome, product context, budget, autonomy level, stop condition |
| `DecisionTrace` | observation, alternatives, chosen action, expected evidence, cost |
| `ToolCapability` | purpose, authority, inputs, effect class, receipt schema, failure modes |
| `Observation` | source, scope, digest, freshness, uncertainty, consent where relevant |
| `Experiment` | hypothesis, control, outcome, window, guardrails |
| `LearningCandidate` | evidence coverage, applicability, counter-evidence, human approval |

Memory is deliberately separated: immutable product truth; consent-scoped customer intelligence;
cited, freshness-bounded market observations; campaign-scoped working context; and
evaluation-approved playbook skills. A viral reference, raw transcript, or single successful post
cannot silently become long-term strategy.

Skills are versioned procedures for a coherent toolset, with entry criteria, expected evidence,
cost/stop bounds, and evaluation fixtures. This borrows the valuable separation of skills and tools
from runtimes such as [OpenClaw](https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent.md),
without inheriting a general-purpose personal-assistant product shape.

### Product wedge and sequencing

The first sellable wedge is a weekly evidence-backed growth operator for small software teams: connect
one product source and one outcome source, inspect a prioritized campaign queue and decision traces,
and approve only the few actions that cross a real-world boundary. The moat hypothesis is the governed
product-evidence → customer-signal → experiment → outcome → reusable-playbook graph, not raw video
volume.

Do not reorganize existing Cloudflare effect owners to make them look generic. The provider-neutral
runtime and its fake control-plane test backend evolve independently; each hosted addition binds a
typed, account-scoped operation back to that control plane without creating a second agent runtime.
This keeps working automation release-verifiable while the high-level agent evolves independently.

Trace exercises the full loop with native capture and Threads. A future customer may use a web app,
its own analytics, a CRM, a creative tool, or another channel without changing the agent core.

## Evidence and implications

The supplied Fastlane case is useful as a demand signal, not a technical blueprint. Its reported
growth combines a narrow solo-builder problem, repeated customer discovery, rapid creative selection,
and a customer-intelligence feedback loop. [Starter Story's interview](https://www.starterstory.com/stories/the-insanely-obvious-secret-behind-this-69k-month-saas)
reports the company as an AI short-form tool for solo builders and says the founder conducted roughly
2,000 customer calls. The claim remains third-party, self-reported case evidence.

Three external patterns reinforce the architecture:

- Signal collection must write into a shared durable substrate rather than separate campaign notes.
  Clay describes account signals, enrichment, and drafted action all feeding the same GTM data
  workflow in its [Merge case study](https://www.clay.com/customers/merge).
- Product and marketing outcomes must share an experiment model. Amplitude's
  [DeFacto case study](https://amplitude.com/case-studies/defacto) describes a unified analytics and
  experimentation implementation rather than a creative-only workflow.
- Conversations are a first-class research source, but their raw text is not a product decision.
  Intercom's [Living Spaces case study](https://www.intercom.com/customers/livingspaces) describes
  product teams searching conversation evidence and acting on it alongside marketing and support.

The implication is a product with a compounding **evidence and decision ledger**, not a collection of
automation recipes. Claims above are evidence for product shape; they do not prove market size,
pricing, or a guaranteed outcome for this product.

## Canonical loop

```text
product truth + customer signals + market observations
  -> evidence normalization and provenance
  -> opportunity / insight synthesis
  -> experiment portfolio and human policy gate
  -> media / outreach / publication tool requests
  -> tool receipts + product and channel outcomes
  -> conservative evaluation
  -> human-approved reusable learning
  -> next campaign's frozen knowledge snapshot
```

The core is responsible for the arrows. An adapter is responsible only for its request and receipt.
No tool can create strategy, mark an outcome as causal, promote a learning, or publish outside its
approved authority.

## Product surfaces

| Surface | User value | Core record | Initial adapter examples |
| --- | --- | --- | --- |
| Product truth intake | turns a release into defensible claims | `FeatureEvidencePacket` | Git source/build/install receipt, manual evidence packet |
| Customer intelligence | turns calls, support, sales, and usage into attributable signals | `SignalRecord`, `InsightCandidate` | transcript import, support connector, product-event intake |
| Experiment operator | proposes and compares distinct market bets | `MarketingCampaign`, `StrategyBrief`, `ExperimentRegistration` | current Codex judgment, existing analytics |
| Creative execution | turns an approved proof treatment into reviewable artifacts | `ArtifactRequest`, `ArtifactManifest` | Trace native capture, copy, composition, video, Figma |
| Channel execution | delivers an approved artifact through a channel safely | `PublicationIntent`, `PublicationReceipt` | Threads first; other channels later |
| Learning workspace | explains what was learned and where it is valid | `ExperimentEvaluation`, `LearningCandidate`, `MarketingPrinciple` | current deterministic evaluator and reviewer approval |

`Persona`, `Appium`, `Threads`, `Figma`, and `video` do not become product modes. They are account
context, adapter capabilities, channel destinations, or artifact formats respectively. This prevents
mixed-axis APIs such as a “Figma campaign” or an “Appium strategy.”

## Architecture boundaries

### Core: Marketing Operating System

The core is tenant-scoped and provider-neutral. It owns identity, durable evidence, policy,
campaign/experiment state, approval grants, idempotency, outcome semantics, evaluation, and learning
snapshots. It never holds OAuth secrets or drives a GUI.

The current `agent_v1` D1 campaign ledger is the seed of this layer. New platform work must extend
that ledger rather than create a second agent runtime or long-lived conversational memory.

### Integration adapters

Each **effect adapter** declares a typed capability, accepts only a broker-issued request, and
returns an immutable receipt/artifact manifest. The first persistence contract retains the existing
effect classes: `none`, `local_artifact`, and `external`. Friendlier UX labels or future adapter
families must map onto that durable policy contract rather than silently invent another taxonomy.

Core judgment is not an adapter: `strategy.shadow` stays a core judgment policy, and `copy.text`
stays the current creative-output/materialization contract. The first catalog admits only adapters
that cross an execution or collection boundary. In this repository that means `capture.native_png`
and a reference-only `publish.threads` entry.

Existing ownership remains intact:

- Trace native capture owns `capture.native_png`.
- `threads/*` owns Threads OAuth, publish-once, reconciliation, and metrics.
- The Mac worker owns official Codex turns and local Appium effects.
- The marketing core owns judgment, authorization, lineage, and learning.

### Tenant product context

A tenant has one or more `ProductContext` records: product identity, supported claim sources,
approved outcome events, audience/account context, and installed adapters. A campaign references an
immutable product-context snapshot. This is the future replacement for assuming every account is a
Trace workspace.

The first registry does **not** introduce that identity model early: `account_id` is the
transitional tenant boundary. It provides account-scoped installations and immutable
context-receipt-scoped bindings while preserving the current Trace account path.

The current Trace reference lane now also has a narrower `MarketingContextSnapshot` and
`CustomerSignal` implementation under that same `account_id` boundary. It accepts a reviewed manual
normalization only, freezes an approved snapshot whose signal freshness and retention cover its full
lifetime, and sends the planner only an allowlisted projection. It is evidence for the boundary, not
completion of the future `ProductContext`, connector, or role model.

## Business model and wedge

The first sellable wedge is not “all marketing for every company.” It is an agent for small software
teams that repeatedly ship but lack a dedicated growth/creative operator.

1. Connect one product source and one outcome source.
2. Review a weekly evidence-backed campaign queue and creative proofs.
3. Approve only the actions that cross an external-effect boundary.
4. See the evidence, cost, outcome coverage, and reusable learning for every campaign.

Potential pricing aligns with the scarce, compounding units: active product contexts, approved
experiment capacity, connected signal sources, and executed artifact/publication volume. Do not price
raw model tokens as the primary customer value.

The defensible asset is a tenant's governed evidence/experiment/learning graph and the adapters that
can prove product truth and outcomes—not an interchangeable prompt library.

## Fixed decisions

- Trace remains the reference tenant; no big-bang rename or generic rewrite.
- The product is multi-tenant at the core, while channels and tools are adapters.
- A campaign operates on frozen evidence and knowledge snapshots; each context receipt carries a
  frozen capability binding appropriate to its stage.
- Customer signals are evidence with source, consent/retention policy, scope, and digest; raw
  transcripts never become automatic strategy instructions.
- Human approval remains mandatory for irreversible effects and learning promotion.
- Evaluation distinguishes descriptive attribution, causal estimates, qualitative research, and
  creative review; they cannot share a single winner field.

## Deferred control-plane implementation slice

The already-started, backwards-compatible **effect-adapter capability catalog** is a post-merge
control-plane slice, not the next agent-runtime slice:

1. Define account-scoped capability registrations with `capability_id`, immutable descriptor digest,
   effect class, request/receipt schema digests, owner, enabled state, and activation state.
2. At each context-receipt stage, bind the selected descriptors immutably. An `ArtifactRequest` and
   resulting manifest must carry the exact binding/descriptor digest; an ID-only match is rejected.
3. Register Trace native capture as an active `local_artifact` adapter. Register Threads publication
   as a `registered_reference` external adapter: visible for architecture and future activation, but
   neither selectable by creative planning nor dispatchable by this slice.
4. A frozen binding is reproducibility evidence, not standing authorization. Dispatch also requires
   the registration to be currently enabled and the existing exact, unrevoked approval/receipt gates.
   Disabling a registration blocks pending work immediately; descriptor edits affect only later
   context receipts.
5. Expose one account-authorized, read-only transition diagnostic
   `{campaign_id, current_state, next_transition, blockers[]}`. It explains existing guards without
   adding state or recommendations. Its deterministic blocker kinds are `evidence`, `signal`,
   `capability`, `approval`, `artifact_receipt`, `publication_receipt`, `observation_window`, and
   `learning_review`.

The migration seeds the two Trace reference registrations for existing accounts, and account
provisioning must make the same defaults visible to new accounts.

This slice makes adding a video, Figma, CRM, transcript, or new channel integration additive rather
than a new branch inside `marketing-agent.js`.

## Deferred decisions

- Public self-serve onboarding, billing provider, and final packaging tiers.
- A generic video or Figma executor; only an adapter contract is in scope first.
- A generic adapter dispatcher. The existing Trace owners continue executing their own effects.
- Automatic external publishing, campaign-budget autonomy, and cross-channel optimization.
- The exact customer-interview connector and transcript retention policy; these require customer
  privacy and consent decisions before ingestion.
- Multi-product tenant UX and role model; retain current workspace authority until the data model and
  access controls are specified.

## Non-goals

- Rewriting the current Trace capture or Threads publisher to make it look generic.
- Treating a URL crawl as sufficient product truth.
- Copying viral formats or external content without a product claim/proof boundary.
- Letting an adapter issue a strategy, overwrite a campaign, or self-promote learning.
- Claiming a revenue or conversion result from a case study as a product guarantee.

## Success criteria and acceptance checks

| Criterion | Verification type | Acceptance check |
| --- | --- | --- |
| Existing Trace behavior is preserved | integration | current native-capture and Threads suites remain green without moving their effect implementations |
| New tool addition is additive | unit | a capability registration and request validation test requires no change to strategist/evaluator contracts |
| Campaign capability is reproducible | integration | an artifact request and manifest are accepted only when their binding carries the exact descriptor digest, schema digest, and effect class used by the creative context receipt |
| External effects stay governed | integration | a frozen binding alone cannot dispatch; a current enabled registration plus exact, unrevoked approval and effect-owner receipt remain required |
| Product intelligence is not raw prompt memory | unit | unapproved signal/transcript records cannot enter a campaign knowledge snapshot |
| Reviewer can recover an exact current decision | integration | a protected read-only queue emits only the pending target and a packet whose action body matches the existing approval endpoint without reading customer-source payloads or writing a grant/task |
| Operator can diagnose a stalled loop | integration | seeded missing prerequisites return stable, ordered blocker objects from the read-only transition diagnostic |

## Next implementation slice

The provider-neutral runtime, scorecard baseline, account-scoped capability catalog, narrow
customer-context reference lane, and protected review surface now exist. An evaluated campaign can
now persist and later run an outcome-informed, no-effect next-experiment judgment without blocking on
worker availability. The next bounded slice is activation: convert only an approved exact draft into
one successor shadow campaign after rechecking freshness, capability availability, budget policy, and
source lineage; keep every existing effect owner unchanged. In parallel, use a private diverse corpus
and repeated pinned provider/model paired trials before making a model-quality claim. Connector
ingestion and a multi-product role model remain later policy work. The
canonical companion for current Trace behavior remains
[`threads-marketing-agent.md`](./threads-marketing-agent.md).

## Pre-implementation critique — 2026-09-01

Fresh-Eye Satisfaction: parent-delegated. Two independent reviews covered architecture ownership and
operational validation. Their change-affecting findings are incorporated above.

| Disposition | Finding | Decision |
| --- | --- | --- |
| Act Before Ship | A campaign-wide string list cannot bind a request to schema/owner/effect contract. | Use immutable context-receipt-scoped descriptor bindings and carry their digest through request and manifest. |
| Act Before Ship | The current data authority is `account_id`, not a generic tenant/ProductContext model. | Keep the first catalog account-scoped; defer identity and role-model migration. |
| Act Before Ship | Publication and artifact execution have different safety flows. | Register Threads reference-only and non-dispatchable; validate only artifact bindings in this slice. |
| Bundle Anyway | A frozen snapshot could be mistaken for continuing authority. | Require current enabled registration and existing approval/receipt gates at execution. |
| Bundle Anyway | A diagnostic could become a new UI/state machine. | Limit it to a deterministic read-only transition projection. |
| Over-Worry | A generic dispatcher, video/Figma executor, billing, or ProductContext UX is needed to validate the catalog. | Deliberately not doing them in this slice. |
| Valid but Defer | Transcript consent/retention, CRM ingestion, and causal generalization are real platform needs. | Keep them deferred until their policy and source contracts exist. |
