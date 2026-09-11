# Code Architecture

Status: Active
Last reviewed: 2026-09-11

## On-premises Marketing Agent

An arrow below means “imports”: `A → B` means A imports B. `bootstrap` composes concrete
`channels`, `agent/service`, `tools`, `providers`, `knowledge`, and domain owners. `channels` may
import public service and domain surfaces plus `contracts`/`transport`; `agent/service` may import
`agent/core`, `agent/runtime`, `contracts`, providers, and established domain canonical-repository
types. `agent/core` imports only portable contracts and ports. `providers` and `knowledge` use shared
contracts or transport as appropriate. Domain owners do not own channel admission or Agent Core, but
an existing service-to-domain or canonical-repository bridge remains permitted.

| Package | Responsibility | Must not own |
| --- | --- | --- |
| `contracts/agent_run.py` | portable Run, Step, Intent, snapshot, invocation, approval, receipt, outcome, learning, and record envelopes | provider calls, storage, channel state |
| `contracts/tool_capability.py` | complete ToolDescriptor policy/readiness/idempotency/reconciliation contract | registry selection or execution |
| `contracts/reasoning.py` | replaceable structured reasoning request and decision | Codex process lifecycle |
| `agent/core/` | capability selection and provider/tool ports | SQLite, HTTP, Cloudflare, Appium |
| `agent/service/` | canonical on-prem application flow and append-only SQLite repository | channel-specific planning or effect implementation |
| `agent/runtime.py` | write-ahead invocation, exact approval, receipt validation and recovery | channel admission or effect-specific implementation |
| `channels/`, `channels/http/` | signed Slack and authenticated HTTP input/output boundaries | canonical Run state or tool effects |
| `tools/` | external-effect and read adapters, including descriptor/adapter registration inputs | Run/approval/recovery ownership |
| `bootstrap/` | composition of the configured service and concrete integrations | a second execution path |
| `knowledge/` | team knowledge, scoped retrieval, curation, learned skills and workspace learning readiness | Agent, channel, bootstrap, tool or workload dependencies |
| `creative/`, `delivery/`, `learning/`, `research/`, `workflows/`, `evaluation/` | their named domain policies and implementations | Agent Core or channel admission; their established service bridge remains explicit |
| `contracts/`, `providers/`, `transport/`, `cli/` | data-only contracts, external provider clients, shared transport and installed commands | another domain's mutable runtime state |

`bootstrap/integrations.py` is the composition boundary for installed research, Slack, Notion and
GitHub owners. It resolves secrets only inside adapters and does not move their effect logic into
Agent Core. `agent/service/skills.py` owns versioned procedures and readiness;
`agent/service/scheduler.py` owns date-stable daily admission and the narrow scheduled-delivery
approval allowlist.

Tool registration is one atomic configuration unit: a stable capability ID/version, descriptor
factory and execution adapter are validated together before catalog and adapter projections become
visible. Duplicate or incomplete registrations are configuration errors. Readiness is recomputed at
planning and dispatch time, without replacing an already admitted invocation's executor identity.

`tools/skill_tools.py` exposes the versioned `agent/service/skills.py` catalog through read-only
`skills.list` and `skills.read` adapters, registered by `bootstrap/integrations.py`. Discovery returns compact metadata;
reading returns one exact server-owned procedure, criteria and required capabilities. Neither
loads arbitrary files/URLs nor creates another Run. The canonical runtime owns invocation,
receipt and budget accounting. `providers/codex_reasoning.py` owns generic discover/act/inspect guidance;
marketing procedure bodies remain in the skill catalog rather than the initial prompt.

`knowledge/skill_discovery.py` owns deterministic metadata-only ranking used by both catalog
adapters and the scoped context assembler. It has no persistence, tool execution or authority
logic. Knowledge scope, applicability and source-currentness are resolved before ranking.
`knowledge/tool_read_operations.py` pages effective catalog results; the built-in adapter retains
its versioned read contract. `knowledge/context_selection.py` admits independent ranked skill
records within a separate bounded share of context, rather than treating the entire catalog as
one indivisible evidence group. The provider chooses discovery/authoring guidance from the
filtered snapshot; private filtering checks `KnowledgeToolName` membership instead of prefixes.

Context receipt identity hashes the complete request, receipt observation and selected block
content. Exclusions, provenance, policy or observation time may change while block labels remain
the same; this is a new observation, not an idempotency conflict. Exact observation replay remains
idempotent. Persistence still rejects two different receipts with the same ID.

`agent/service/task_input.py` projects the latest direct, host-admitted continuation into
`ReasoningRequest.current_user_message`; `agent/service/application.py` supplies it independently of evidence
compaction and uses it for retrieval. The original goal and canonical history remain unchanged.
Nested tool/source data cannot become task input, and task intent grants no effect authority.
`agent/service/knowledge.py` owns the shared read-only DM tool set, also used by the Slack composition so its
outer policy does not accidentally remove the inner knowledge owner's supported reads.

`SlackEvents._current_context` reprojects the authorized conversation through
`SlackConversationStore.transcript` and the existing scoped memory selector at every planning
boundary. This includes prior assistant replies for same-Run follow-ups. It does not write a
second transcript into continuation evidence. Existing transcript bounds, source edit/deletion
handling, conversation identity and private member/session isolation remain the authority.

`agent/runtime.py` remains the execution-safety kernel for write-ahead invocation, exact-call
approval, receipt validation, restart recovery, and reconciliation. The service composes it; it does
not fork those guarantees. The `agent/` namespace now contains this canonical engine only; the prior
connector-specific product is not restored, and no `trace-agent` or `trace-ads` entrypoint is introduced.

Codex reasoning uses a strict provider projection: arbitrary tool input is encoded as
JSON text in tool_input_json, decoded immediately back into the portable ReasoningDecision,
and validated against the selected ToolDescriptor by the service. The receipt binds the actual
provider output schema digest. This avoids sending recursive open-object schemas that the live
structured-output provider rejects; canonical invocation input and history remain structured JSON.

## Marketing analysis

`learning/funnel_analysis.py` owns bounded descriptive funnel contracts and decimal arithmetic.
`tools/marketing_analysis.py` adapts them to the canonical `marketing.analyze` descriptor and
receipts; `bootstrap/integrations.py` registers it. This observation-only tool imports no channel,
provider or mutable agent state. Known semantic input errors return failed receipts without raw
input values; successful ratios are decimal strings compatible with the portable ledger.
`FunnelCohort` permits an unknown currency only without spend; supplied spend, including zero,
requires a currency. The descriptor derives its schema/digest from this contract, so numeric-only
reports need no invented currency and known invalid monetary input follows the existing failed receipt.
`agent/service/skills.py` owns growth/customer-insight procedures and their readiness requirements.

## Web and Slack onboarding owners

- channels/http/browser_login.py owns browser-bound PKCE, short-lived server sessions and CSRF;
  channels/http/oauth.py owns token exchange/introspection transport with redirects disabled.
- channels/http/jobs.py owns durable web request admission, background dispatch and scoped status.
  It never creates a separate Run ledger or plans outside MarketingAgentService.
- channels/slack_commands.py owns real Slack form translation, command admission and response
  dispatch markers. It uses the existing ChannelApplicationAdapter for identity and exact approval.
- bootstrap/channel_setup.py composes installed environment configuration and operator-managed
  Slack member bindings; cli/marketing.py starts and stops background owners.
- tools/web_search.py owns bounded query-to-search-result observation, separately from the
  immutable installed-product evidence owner. `agent/service/skills.py` retains the original daily skill and adds
  the Slack-only versioned procedure.

## Composition

`ads_booster.cli.marketing` exports the sole CLI, `trace-marketing`. `service run` composes the
canonical service, HTTP routes, signed Slack workers, optional knowledge runtime and image-edit
queue. `cli/server.py` owns Linux installation configuration and service lifecycle commands.

```text
cli/marketing.py
  -> bootstrap/lifecycle.py
     -> agent/service/application.py -> agent/core registry/provider ports
     -> agent/service/sqlite_repository.py -> local canonical Run ledger
     -> agent/runtime.py -> write-ahead execution and reconciliation
     -> providers/codex_reasoning.py -> providers/codex_cli.py
     -> bootstrap/integrations.py -> research/creative/Slack/Notion/GitHub tools
     -> knowledge/ -> scoped source, memory and curation owners
  -> channels/http/http_api.py -> Web/Slack/API admission
  -> bootstrap/image_edit_setup.py -> asynchronous image editing
```

No Worker/D1/R2 client, Mac worker broker or Appium executor is composed. The retained Python
package distribution name `trace-appium-capture` is metadata compatibility, not an Appium runtime.
Cloudflare Tunnel remains in the Linux operator setup only.

## Knowledge ownership and dependency direction

`knowledge/batch_actor.py` rehydrates private batch identities and current catalog grants.
`knowledge/batch_failure.py` records denied queued jobs and unclaimed batches without replaying
completed receipts; the batch runtime then continues to other authorized work.
`knowledge/repository_batch_recovery.py` reconciles abandoned batch/job state under the exclusive
`KnowledgeOwner`; the installed lifecycle invokes it before starting any processing.

`knowledge/` is the server-owned domain. It owns contracts, scope and grant policy, immutable source
and Wiki/memory files, SQLite catalog migrations, ingestion, retrieval/index outbox, curation jobs,
source-bound skill revisions, workspace learning counters and admissions, backup/restore, tombstones,
the control-root erase ledger, and transfer dependency records. It does not import Slack, HTTP, Mac,
Cloudflare, or UI modules.

`bootstrap/lifecycle.py` is the composition root for the enabled runtime. It injects
the configured `KnowledgeSettings`, local actor, `SqliteKnowledgeRepository`, canonical ingress,
`KnowledgeServiceAdapter`, `KnowledgeContextAssembler`, `CodexKnowledgeProvider`, `BoundedJobRunner`,
`KnowledgeIndexWorker`, `MemoryViewDispatcher`, `CurationBatchRuntime`, `LearningReviewCoordinator`,
`TerminalExperienceAdmission`, and a read-only `LegacyMemoryGuard` backed by the existing service
`SQLiteMemoryStore`. The guard contract lives in `knowledge/legacy_memory.py`; the knowledge domain
depends on its typed reader protocol and does not own or duplicate the legacy store. The batch runtime groups
scope- and policy-compatible jobs, honors collection deadlines and urgent interruption, and drives
one shared provider call per bounded round. `knowledge/curation.py` executes each job-bound decision
through the trusted tool host, feeds its actual observation into the next shared round, and returns
one receipt per original event revision. `cli/marketing.py` starts and stops the continuous runtime
with `service run`; the existing Agent Service still owns Runs, approvals, and execution records.
`repository_batch.py` owns atomic batch state/JSON updates and persisted retry generations;
`runtime.py` delegates explicit flush to that owner. `batch_curation.py` derives new collection IDs
from those generations while preserving event deduplication and the first-event deadline. The shared
learning readiness counter is only a wake-up signal; the existing batch isolation key remains the
authority for member, session, scope, grant, and policy partitioning.

`agent/service/knowledge_ingress.py` owns the service-database outbox and trusted Run
binding. `knowledge_ingress_authority.py` maps authenticated channel actors to existing knowledge
members, sessions and grants without resetting revocations or roles. API and Slack composition share
this ingress; context preparation and outbox dispatch recheck authority through the same bridge.
`channels/knowledge_ingress_slack.py` translates authenticated Slack identity to
workspace or member/conversation scope. `agent/service/knowledge.py` owns context
preparation, read-only DM capability filtering, tool adapters, receipt freshness checks, and the
typed context boundary. `contracts/knowledge_context.py`, `contracts/knowledge_preparation.py`,
and `contracts/knowledge_selection.py` own transfer, preparation, action and receipt contracts.
Historic replica records retain provenance without an active hosted transport.

`knowledge/skills.py` projects every current `agent/service/skills.py` procedure into a protected
built-in record, resolves source-bound agent-created revisions, and falls back to the current
built-in when an override's base digest no longer matches. `repository_skills.py`,
`skill_contracts.py`, `schema_skills.py`, and `schema_learning.py` remain leaves of the existing
repository, migration, file-publication, CAS, and receipt owners. `learning_contracts.py` carries
typed terminal experience references and review requests; it never grants user authority. A
skill changes only from a current authenticated foreground request.
`knowledge/skill_authoring.py` owns the bounded explicit-directive grammar;
`skill_publication_validation.py` binds action and provenance to the trusted current user event.
`repository_commit.py` rechecks channel/workspace write authority and request currentness in the
transaction. `skill_source_currentness.py` projects only source revision/hash metadata for published
channel-authored skills, allowing workspace reuse without granting channel history access.
`context_selection.py` uses that projection for skill dependency receipts. Background `curation.py`
excludes skill mutations from its catalog; normal feedback continues through scoped memory owners.
`repository_learning_recovery.py` checks the persisted learning partition against current authority;
its `repository_learning_recovery_write.py` leaf atomically reattaches released jobs and admissions
to the same sealed round, or records terminal failure. No separate skill store, learning provider,
daemon, or verifier is composed.

`LearningReviewCoordinator.consume_target` fences each foreground-applied target by source revision
and target ID. Foreground consumption validates the canonical event and source revision without
admitting a background batch or requiring its policy version. The fence removes only the consumed
target from later learning review and preserves
the rest of the source. `TerminalExperienceAdmission` writes receipt-grounded experiences through
the existing Agent Service `append_step` after-commit seam and replays its outbox through the live
runtime dispatcher.

`agent/service/knowledge.py` registers `skill_list`, `skill_get`, and `skill_apply` through the
existing knowledge tool host. Prepared context includes metadata-only effective skill references and
`ContextReceipt.selected_skill_revisions`; each selected entry carries nested `source_refs` and
`source_revisions`, separate from generic retrieval references. `skill_get` loads the body.
Binding-free Runs hide these tools and references. The DM projection may read skill metadata and
bodies, subject to current grants, but cannot write skills.

`knowledge/tool_source_operations.py` returns verified segment evidence references and quote hashes.
`maintenance_jobs.py` builds curation inputs from extracted text and attaches authenticated user-event
metadata only after matching the canonical conversation event; `curation_contracts.py` owns that
metadata type. Evidence resolution remains in the knowledge domain, not in provider-generated IDs.

The deletion path is split by ownership: `knowledge/erase_ledger.py` owns the chained control-root
record, `schema_deletion.py` and repository deletion code own local manifest/block/purge state, and
recorded replica receipts retain external acknowledgement state. The former Cloudflare purge
transport has been removed. Preexisting replicas stay `purge_pending` until separately reconciled;
no remote deletion is executed or acknowledged by local purge. `backup.py` owns manifest/file integrity and private backup paths; `restore.py`
applies current erase authority and rebuilds search before activating a new root. Mixed-memory
redaction remains owned by repository deletion code. No module may infer actor, workspace, member,
brand, or sharing authority from model tool input or a request JSON field.

## Research and runtime contracts

`research/dynamic_evidence_research.py` composes the local observe-only research loop, official
Codex planner and product/customer/market collectors. `contracts/marketing_agent.py` owns feature
evidence and strategy/outcome contracts; these data types do not imply an active hosted campaign.
`contracts/reference_research.py` owns the quarantined market proposal schema; the local runner
retains unverified proposals without promoting them to source evidence. `providers/runtime_identity.py`
owns provider identity checks shared by research and Codex callers.

`agent/runtime.py` owns the provider-neutral, local session-and-dispatch harness. It has no
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
`replay_session(events)` is the matching public read-only reducer for an exported v3 trace; it returns
only the checkpoint re-derived from that ledger. The persisted admission and execution methods remain
the only public effect APIs; non-durable transforms are private test primitives. General planner, skill-registry, context-projection, and
outcome-evaluation owners remain separate from effect adapters; the implemented fake-backend
verticals are described below. Verified pre-header v1/v2 terminal traces are read-only; pre-header
pending/non-terminal sessions and all legacy saves fail closed.

`contracts/planning_projections.py` owns `FeaturePlanningProjection`, the shared data-only planner
projection for the Feature Launch and Evidence Research verticals. It contains packet identity/digest,
lifecycle, and claim IDs only; raw claim text, source references, evidence payloads, capability data,
and instructions remain outside planner context.

`contracts/feature_launch_evidence_brief.py` owns the immutable contract between completed Evidence
Research and a new Feature Launch session. It contains only research-trace provenance digests,
scope-complete receipt-bound observation digests, bounded semantic summaries/caveats/trust states,
and allowed supported claim IDs, plus the data-only projection used by the Feature Launch planner.
Raw sources, URLs, and locations stay out. It also owns the narrow verifier protocol and its
failure type, but imports no runtime, planner, registry, hand, or session owner.
`research/evidence_research_operator.py` alone converts an already terminal validated research trace into this
contract and supplies the local verifier that reloads and re-derives its source session. Before its
first brief commit, `workflows/feature_launch_operator.py` requires that verifier to pass; it depends on the
protocol, not the research runtime. No module combines the two sessions or transfers a research tool
authority into launch.

`workflows/feature_launch_operator.py` owns the first, narrow reasoning vertical over that harness.
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

`research/evidence_research_operator.py` owns a separate bounded research loop over the same
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
validated trace as the contract owned by `contracts/feature_launch_evidence_brief.py`; it cannot start Feature
Launch or merge the two sessions.

`evaluation/marketing_os_scorecard.py` owns a pure offline evaluation contract rather than a planner,
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

`evaluation/marketing_os_scorecard_corpus.py` owns the narrow private-grader corpus loader. A trusted
grader process supplies one mounted corpus directory; the loader resolves only its fixed
`runner_inputs.json` and `grader_expectations.json` children, validates strict envelopes and matching
case-ID sets, and preserves the input-file order before it returns the existing case contract. It does
not load tool environments, select a case, run a provider, or provide fallback/public fixture data. It
does not make an in-process runner confidential: private expectations require a separate grader
process and mount, and a future comparable grader-environment digest is a separate report contract.

### Linux update ownership

- `cli/server.py` owns the installed Linux setup and lifecycle command presentation under
  `trace-marketing server`. It validates operator input/Slack identity, writes configuration and user
  units, and delegates updates to the existing manager via systemd. It never owns Agent Runs.
- `install-server.sh` prepares missing Ubuntu dependencies and bootstraps CI-verified public main through
  `agent-manager.py bootstrap`. It preserves existing CLI/installations. Wheel force-includes export
  the canonical Slack manifests and unit templates from `docs/operations/agent-server` into
  `ads_booster/server_assets`; runtime reads these installed bytes, not a development checkout.

- `agent/service/maintenance.py` owns admission accounting shared by the HTTP dispatcher, background
  queues (including recovery and outbound notification) and scheduled skill execution.
- The updater's installed import probe verifies the `agent/service/maintenance.py` and
  `channels/http/http_api.py` pair; the manager itself remains a standalone transition boundary.
- `docs/operations/agent-server/agent-manager.py` is the standalone Python 3.10+ Linux operator CLI:
  bootstrap wheel installation, main fetch/CI gates, locked candidate install, systemd switching,
  offline state backup, transaction recovery, and installed process launch. The timer executes the
  manager from current, so updater changes follow main. It does not plan or own channel effects.
- `cli/server_knowledge.py` owns additive managed-install Knowledge configuration migration, invoked
  by the selected release's manager before exec. It reuses the Knowledge configuration and store
  initializers, preserves prior files, and publishes environment additions last. The standalone
  updater reads only persisted Knowledge path assignments for offline backup/restore.
- The supplied systemd service/timer are the Linux process composition. The actual service process
  receives the release identity and maintenance path from the launcher, not from request parameters.
- `.github/workflows/verify-agent-server.yml` owns the dedicated exact-commit CI gate and fresh-wheel
  CLI smoke on Ubuntu, including `tools` compatibility. The manager requires only this named
  GitHub Actions check, completed successfully. `tests/cli/test_agent_server_update.py` exercises
  admission, rollback and crash boundaries.

## Slack conversation ownership

`channels/slack_conversations.py` owns typed conversation/message/plan records and the
additive SQLite inbox/outbox beside the canonical Run ledger. `channels/slack_events.py` owns signed Events
admission, scope derivation, dialogue projection, action planning and original-conversation replies.
It delegates Run/input/approval mutations to `MarketingAgentService`; it does not duplicate the engine.
`channels/http/http_api.py` exposes the Events ingress and `bootstrap/channel_setup.py` drains it within the
existing maintenance-gated worker. `cli/marketing.py` composes this optional surface from env.
Private DM composition narrows CapabilityPolicy while sharing the service lock and canonical stores.
Operator manifests and merge-to-operation guidance live in `docs/operations/agent-server`.
Optional learning-question projection skips historical conversations whose read access is denied;
the resolver still enforces denial, and unrelated admitted Slack jobs continue to execute.
`knowledge/jobs.py` owns the common cancellation interface and fresh-process job boundary;
`knowledge/batch_runtime.py` applies the same spawned-process boundary to curation batches.

### Portable server onboarding and recovery

The Linux installer supports Ubuntu 22.04/24.04 x86_64/aarch64. It preserves existing tools,
uses pinned official binary checksums for missing tools, enables the service user's linger, and
supports committed source candidates separately from CI-verified public main. The anonymous
GitHub check adapter paginates and fails closed; unchanged main does not consume check API requests.
Staging failures are retryable without stopping the current agent; activation failures remain
quarantined. `last-check.json` records update decisions separately from activation receipts.

The installed server CLI owns a private `setup-pending.json` write journal. Replay is limited to
allowlisted configuration/unit paths and exact previous/desired contents, so interrupted setup can
resume without replacing intervening edits. Completed setup is idempotent. Doctor distinguishes
missing configuration and Codex login from readiness; status includes update provenance. No company
IdP or repository authentication is required for the default public Slack-only server.

## Continuing-work owners (2026-09-07 candidate)

| Owner | Responsibility |
| --- | --- |
| `agent/service/work_continuation.py` | canonical human input/pause admission, exact event replay at safe boundaries |
| `agent/service/application.py` | bounded evidence projection, signal boundary, current-memory callback, unchanged runtime dispatch owner |
| `contracts/creative_work.py`, `creative/creative_assets.py` | small scoped assets, byte provenance, parent revisions and stale descendants |
| `channels/http/creative_api.py` | authenticated PNG/JPEG upload/preview and same-Run continuation |
| `contracts/agent_memory.py`, `learning/memory.py`, `channels/http/memory_api.py` | attributed reviewed notes, scope-before-query, corrections/expiry/tombstones and selected-memory receipts |
| `creative/creative_procedures.py` | ten composable procedures and honest ready-tool/human return briefs |
| `channels/slack_image_review.py`, `tools/image_review.py` | authorized file binding/download and actual read-only image assessment, no editing |
| `channels/slack_creative_setup.py` | optional permission-probed tool catalog composition; no service lifecycle ownership |
| `channels/slack_memory.py`, `channels/slack_delivery.py` | authenticated exact review command translation |
| `contracts/marketing_delivery.py`, `delivery/delivery_review.py`, `channels/http/delivery_api.py` | prepared review packets only; no duplicate channel execution ledger |

`CodexCli.run_marketing_image_review_job` adds validated image arguments to the existing
no-tools/read-only structured runner. Ordinary judgment calls keep their existing signature.
No provider framework/vector database/custom agent entrypoint is introduced. `creative.prepare`
is a real no-effect local adapter; its output says prepared, never executed.

New delivery API/tool request schemas omit `d1_campaign_id`. The persisted `DeliveryProposal`
contract retains that optional field to preserve old signed history. Generic `PublicationTarget`
drafts and manual performance platform labels confer no Threads API capability.

`delivery/delivery_tools.py` owns reasoning-callable preparation and is wired by `bootstrap/lifecycle.py`
through `ConfiguredAgentTools`; `creative/creative_asset_verifier.py` bridges immutable assets to preparation
approval. Neither owns external effects. `AgentJobs` rechecks the API's trusted reviewer
policy immediately before queued approvals. `ToolInvocation.tenant_id` binds new local tool
mutations without rewriting legacy invocation digests.

`learning/work_observations.py` owns immutable human effort records and scoped learning snapshots;
`channels/slack_work_observations.py` translates authenticated report/summary/correction commands.
`learning/work_observation_validity.py` validates sources without importing the memory repository,
so memory selection/review can reuse its connection and preserve transactional currentness.


`channels/slack_image_files.py` owns signed file resolution, bounded download/decode and immutable cache
for both review and intake. `channels/slack_asset_intake.py` owns the inspect/import schemas, current
approval attribution and registration. `channels/slack_creative_setup.py` composes all three optional
file capabilities under the same observed Slack grant. `creative/creative_asset_links.py` owns the
Run-to-asset projection used by HTTP uploads, Slack imports and image production results.

`contracts/agent_run.py` owns the nonterminal `ToolExecutionDeferred` acknowledgement and
`awaiting_tool` Run state. `agent/runtime.py` owns the deferred event, retained pending
invocation/reservation and exact operation resolution; acknowledgement adds no terminal receipt.
The Agent Service translates adapter acknowledgements and validates eventual results against
frozen invocation/output contracts before recording canonical receipts. Transport authentication
and worker artifact validation must precede that internal completion method.

`contracts/performance_observation.py` owns attributed performance snapshots and comparison
contracts. `learning/performance_observations.py` owns immutable scoped reports, corrections and
learning-candidate construction; `learning/performance_observation_validity.py` validates current
source digests using the memory owner's transaction without a circular store dependency.
`channels/slack_performance.py` translates signed conversation commands; `channels/http/performance_api.py` exposes
the authenticated, read-only same-Run projection. Neither adapter owns approval or external
metric collection. Human reports are not promoted to independently verified external facts.

`creative/creative_image_edit_contract.py` owns bounded source/region/locale requests and deterministic
composition that restores and checks unchanged pixels. `providers/codex_image_edit.py` owns
the official app-server protocol, restricted tool inventory, immutable input files, generation
start marker and verified output readback. `agent/service/creative_image_edit.py` owns exact admission,
durable asynchronous job state, source currentness, asset provenance and canonical settlement.
`bootstrap/image_edit_setup.py` composes the explicit configuration and readiness catalog; the existing
service CLI starts its polling thread under the maintenance gate. These owners do not infer
native product support or final visual approval from generated output.

`creative/managed_image_review.py` authorizes exact Run links and current source bytes, delegates to
the existing read-only visual helper, and stores bounded no-replay inference receipts. Its
catalog is composed by `bootstrap/lifecycle.py`; `creative/creative_procedures.py` chooses it for registered inputs.
`creative/creative_assets.py` `describe` exposes stored metadata only for collection discovery; existing
`get` remains the byte-verifying owner. `channels/http/creative_api.py` bounds same-Run collection discovery,
and `channels/http/web_ui.py` renders authenticated selected images and human outcome snapshots without
creating another execution owner. Slack's review-page owner also supplies the natural assent
boundary; rendered pages and authorization must not maintain independent pagination contracts.

`channels/http/image_edit_api.py` exposes exact operation status and reviewer abandonment through the
existing authenticated API. The image queue owner records human abandonment and settles the
canonical deferred operation; HTTP neither invents worker evidence nor calls the provider.

`cli/server.py` owns the persistent `server.json.port` setting written at initial setup and read by
status. The dependency-free Linux manager independently validates and reads the same public setting
for process launch and health checks; both default to 8090. Cross-boundary regression coverage binds
launch argv, update health and status to the same configured port. The standalone manager remains
Python 3.10 compatible and does not import the Python 3.14 application to discover its port.

`tools/github_issues.py` owns fixed-repository issue input validation, service credential resolution (private file, environment, fixed-host CLI login) and GitHub
HTTP execution/readback. `tools/descriptors.py` supplies its external-effect
approval descriptor; `ConfiguredAgentTools` registers it only with a configured credential.
`cli/marketing.py` loads the token at service composition, while `cli/server.py` owns hidden operator
setup and atomic secret storage. `channels/github_results.py` projects successful receipt-bound issue
URLs for both Slack entry points. Canonical run admission, execution checkpoints and reconciliation
remain in Agent Core/service/runtime, with no separate retry or issue state store.

`execution_control.py` provides the channel/provider-neutral cooperative scope and owned subprocess
cancellation. `providers/codex_cli.py` uses it for structured jobs; `codex_reasoning.py` preserves the
cancellation signal. The canonical service owns checkpoints and append-only STOP transitions.
`channels/slack_progress.py` owns only status-message identity and durable cancellation requests.
`channels/slack_events.py` owns signed button authorization, status updates and scoped execution, using the
existing sender transport (`chat.postMessage` for new status, `chat.update` for known timestamps).
The HTTP composition exposes only the signed interaction route during maintenance, without admitting
new runs. Slack manifests own the external callback registration contract.

`tools/image_generation.py` owns the image input schema, Codex image turn and bounded PNG
artifact verification. The descriptor remains in `tools/descriptors.py`; installed lifecycle
injects the executor and private artifact root through `ConfiguredAgentTools`. Existing Agent Core
owns exact approval and uncertain execution handling. `channels/slack_images.py` owns receipt-bound
artifact projection, durable upload admission and Slack's external file-upload adapter. Slack event
composition binds the artifact directory beside the canonical service database and passes only the
authorized conversation, never model-selected channel IDs or local filenames.
`CanonicalKnowledgeIngress` owns additive `knowledge_execution_bindings`: immutable Slack source
admission remains separate from message-to-actual-Run execution binding. Its current binding and
pending-fence queries resolve aliases before knowledge preparation. `MarketingAgentService` owns
the bounded canonical follow-up query, distinct work/knowledge context record IDs, exclusion of
old prepared knowledge from generic evidence, and `knowledge_is_current` for deferred workers.
Image-edit owners use that public authority check before effects.
The composition root and API retain both knowledge ingress and current production reviewer hooks;
service doctor remains read-only and does not prepare state directories.

`SqliteChannelStore.bind_workspace_member` owns idempotent first-use identity admission; it
preserves existing approval, disable and revocation state. `SlackEvents.workspace_mentions`, enabled
by installed `events_from_env`, removes static channel/member admission limits after
app/team/signature validation. Worker execution and notification re-check current identity
authority.

`knowledge/curation_context.py` projects bounded canonical conversation evidence and matching CORE
reference entries for the existing curation worker. `CurationMemoryIntent` is the semantic provider
contract; `curation_memory.py` and its payload/admission helpers compile it into existing canonical
memory publication, source admission and indexing. `bootstrap/lifecycle.py` injects this writer into
`CurationDependencies`; no alternate conversation runtime or memory database is introduced.

`knowledge/repository_conversation_deletion.py` owns message-source linkage for canonical evidence
denial and payload erasure. Deletion traversal and evidence readers use that same linkage.

`knowledge/scope_contracts.py` owns workspace, channel and private scope identities.
`schema_channel.py` and `migrations.py` own the v5 channel transition without editing historical schema
strings or immutable provenance. MemoryDocument and Brand own their persisted scope; repository
commit checks prevent owner changes and context receipt checks reauthorize dependencies.
`channels/knowledge_ingress_slack.py` accepts the signed conversation channel; `batch_actor.py`
reloads the persisted submitting actor for channel maintenance. `contracts/agent_memory.py`
separately carries the same channel boundary for learning memory and observations.
`agent/service/knowledge_ingress_grants.py` owns durable channel grant admission markers.
`channel_grant_admissions` preserves revocation after a grant row is removed; ingress, prepared
context and batch actor reload check it. Job and batch submitter fields are omitted from legacy
JSON when absent. Batch claims select their exact batch ID and submitting session.

`LocalKnowledgePolicy.channel_id` is the explicit local-operator selector for channel administration.
`load_local_actor` namespaces channel grants and admin sessions; CLI brand and initial memory IDs
include channel identity while workspace defaults preserve their historical values.

`AccessScope.CHANNEL_MEMBER` and `channel_member_scope(actor)` identify the requester's persistent
personal scope; session-scoped MEMBER retains its private-chat meaning. `MemoryKind.USER` uses the
existing document, entry, revision and head tables. `schema_personal.py` owns the v6 transition.
`migrations.py` selects upgrades by version and checksum, preserving published skill/learning v3/v4
and supporting pre-merge channel/USER candidates without rewriting their recorded checksums.

The curation intent destination selects personal versus common memory. Generic publication and
catalog commit guards validate USER authorship and reference-only semantics. The source visibility
fence in `repository_personal_sources.py` is shared by publication and source admission; it follows
canonical event dependencies rather than parsing message text. Context selection and memory_get
resolve USER ownership from the authenticated actor. Consolidation and generated USER.md views
reuse existing workers with exact personal job scopes and the original submitting actor.

New knowledge task IDs include the authenticated actor, member, session and policy epoch so two
participants can use one shared Slack Run without colliding on task ownership. Existing active
bindings remain valid. Provider wire schemas remove default metadata beside references while
runtime contract defaults remain intact. Batch settlement persists provider/budget failures as
terminal receipts and job reasons; cancellation retains its separate resumable path.
