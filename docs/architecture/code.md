# Code Architecture

Status: Active
Last reviewed: 2026-09-08

## On-premises Marketing Agent

The new dependency direction is portable contracts → agent core → agent service → provider/tool/
channel adapters. Research, creative and delivery adapters remain leaves; they must not own the Agent core.

| Package | Responsibility | Must not own |
| --- | --- | --- |
| `contracts/agent_run.py` | portable Run, Step, Intent, snapshot, invocation, approval, receipt, outcome, learning, and record envelopes | provider calls, storage, channel state |
| `contracts/tool_capability.py` | complete ToolDescriptor policy/readiness/idempotency/reconciliation contract | registry selection or execution |
| `contracts/reasoning.py` | replaceable structured reasoning request and decision | Codex process lifecycle |
| `marketing/agent_core/` | capability selection and provider/tool ports | SQLite, HTTP, Cloudflare, Appium |
| `marketing/agent_service/` | canonical on-prem application flow and append-only SQLite repository | channel-specific planning or effect implementation |

`marketing/agent_service/integrations.py` is the composition boundary for installed research,
Slack, Notion and GitHub owners. It resolves secrets only inside adapters and does
not move their effect logic into Agent Core. `skills.py` owns versioned procedures and readiness;
`scheduler.py` owns date-stable daily admission and the narrow scheduled-delivery approval allowlist.
`ToolRegistry` accepts a live catalog provider so readiness is refreshed at plan and dispatch time
rather than frozen at process startup.

`marketing/runtime.py` remains the execution-safety kernel for write-ahead invocation, exact-call
approval, receipt validation, restart recovery, and reconciliation. The service composes it; it does
not fork those guarantees. The previous deleted `agent/` connector-specific product is not restored,
and no `trace-agent` or `trace-ads` entrypoint is introduced.

Codex reasoning uses a strict provider projection: arbitrary tool input is encoded as
JSON text in tool_input_json, decoded immediately back into the portable ReasoningDecision,
and validated against the selected ToolDescriptor by the service. The receipt binds the actual
provider output schema digest. This avoids sending recursive open-object schemas that the live
structured-output provider rejects; canonical invocation input and history remain structured JSON.

## Web and Slack onboarding owners

- agent_service/browser_login.py owns browser-bound PKCE, short-lived server sessions and CSRF;
  oauth.py owns token exchange/introspection transport with redirects disabled.
- agent_service/jobs.py owns durable web request admission, background dispatch and scoped status.
  It never creates a separate Run ledger or plans outside MarketingAgentService.
- channels/slack_commands.py owns real Slack form translation, command admission and response
  dispatch markers. It uses the existing ChannelApplicationAdapter for identity and exact approval.
- agent_service/channel_setup.py composes installed environment configuration and operator-managed
  Slack member bindings; cli/marketing.py starts and stops background owners.
- agent_service/web_search.py owns bounded query-to-search-result observation, separately from the
  immutable installed-product evidence owner. skills.py retains the original daily skill and adds
  the Slack-only versioned procedure.

## Composition

`ads_booster.cli.marketing` exports the sole CLI, `trace-marketing`. `service run` composes the
canonical service, HTTP routes, signed Slack workers, optional knowledge runtime and image-edit
queue. `cli/server.py` owns Linux installation configuration and service lifecycle commands.

```text
cli/marketing.py
  -> agent_service/lifecycle.py
     -> agent_service/application.py -> agent_core registry/provider ports
     -> agent_service/sqlite_repository.py -> local canonical Run ledger
     -> marketing/runtime.py -> write-ahead execution and reconciliation
     -> providers/codex_reasoning.py -> providers/codex_cli.py
     -> agent_service/integrations.py -> research/creative/Slack/Notion/GitHub tools
     -> knowledge/ -> scoped source, memory and curation owners
  -> agent_service/http_api.py -> Web/Slack/API admission
  -> agent_service/image_edit_setup.py -> asynchronous image editing
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
backup/restore, tombstones, the control-root erase ledger, and transfer dependency records. It does
not import Slack, HTTP, Mac, Cloudflare, or UI modules.

`marketing/agent_service/lifecycle.py` is the composition root for the enabled runtime. It injects
the configured `KnowledgeSettings`, local actor, `SqliteKnowledgeRepository`, canonical ingress,
`KnowledgeServiceAdapter`, `KnowledgeContextAssembler`, `CodexKnowledgeProvider`, `BoundedJobRunner`,
`KnowledgeIndexWorker`, `MemoryViewDispatcher`, and `CurationBatchRuntime`. The batch runtime groups
scope- and policy-compatible jobs, honors collection deadlines and urgent interruption, and drives
one shared provider call per bounded round. `knowledge/curation.py` executes each job-bound decision
through the trusted tool host, feeds its actual observation into the next shared round, and returns
one receipt per original event revision. `cli/marketing.py` starts and stops the continuous runtime
with `service run`; the existing Agent Service still owns Runs, approvals, and execution records.
`repository_batch.py` owns atomic batch state/JSON updates and persisted retry generations;
`runtime.py` delegates explicit flush to that owner. `batch_curation.py` derives new collection IDs
from those generations while preserving event deduplication and the first-event deadline.

`marketing/agent_service/knowledge_ingress.py` owns the service-database outbox and trusted Run
binding. `knowledge_ingress_authority.py` maps authenticated channel actors to existing knowledge
members, sessions and grants without resetting revocations or roles. API and Slack composition share
this ingress; context preparation and outbox dispatch recheck authority through the same bridge.
`marketing/channels/knowledge_ingress_slack.py` translates authenticated Slack identity to
workspace or member/conversation scope. `marketing/agent_service/knowledge.py` owns context
preparation, read-only DM capability filtering, tool adapters, receipt freshness checks, and the
typed context boundary. `contracts/knowledge_context.py`, `contracts/knowledge_preparation.py`,
and `contracts/knowledge_selection.py` own transfer, preparation, action and receipt contracts.
Historic replica records retain provenance without an active hosted transport.

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

`marketing/dynamic_evidence_research.py` composes the local observe-only research loop, official
Codex planner and product/customer/market collectors. `contracts/marketing_agent.py` owns feature
evidence and strategy/outcome contracts; these data types do not imply an active hosted campaign.
`contracts/reference_research.py` owns the quarantined market proposal schema; the local runner
retains unverified proposals without promoting them to source evidence. `providers/runtime_identity.py`
owns provider identity checks shared by research and Codex callers.

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

### Linux update ownership

- `cli/server.py` owns the installed Linux setup and lifecycle command presentation under
  `trace-marketing server`. It validates operator input/Slack identity, writes configuration and user
  units, and delegates updates to the existing manager via systemd. It never owns Agent Runs.
- `install-server.sh` prepares missing Ubuntu dependencies and bootstraps CI-verified public main through
  `agent-manager.py bootstrap`. It preserves existing CLI/installations. Wheel force-includes export
  the canonical Slack manifests and unit templates from `docs/operations/agent-server` into
  `ads_booster/server_assets`; runtime reads these installed bytes, not a development checkout.

- `agent_service/maintenance.py` owns admission accounting shared by the HTTP dispatcher, background
  queues (including recovery and outbound notification) and scheduled skill execution.
- `docs/operations/agent-server/agent-manager.py` is the standalone Python 3.10+ Linux operator CLI:
  bootstrap wheel installation, main fetch/CI gates, locked candidate install, systemd switching,
  offline state backup, transaction recovery, and installed process launch. The timer executes the
  manager from current, so updater changes follow main. It does not plan or own channel effects.
- The supplied systemd service/timer are the Linux process composition. The actual service process
  receives the release identity and maintenance path from the launcher, not from request parameters.
- `.github/workflows/verify-agent-server.yml` owns the dedicated exact-commit CI gate and fresh-wheel
  CLI smoke on Ubuntu, including `tool_adapters` compatibility. The manager requires only this named
  GitHub Actions check, completed successfully. `tests/cli/test_agent_server_update.py` exercises
  admission, rollback and crash boundaries.

## Slack conversation ownership

`marketing/channels/slack_conversations.py` owns typed conversation/message/plan records and the
additive SQLite inbox/outbox beside the canonical Run ledger. `slack_events.py` owns signed Events
admission, scope derivation, dialogue projection, action planning and original-conversation replies.
It delegates Run/input/approval mutations to `MarketingAgentService`; it does not duplicate the engine.
`agent_service/http_api.py` exposes the Events ingress and `channel_setup.py` drains it within the
existing maintenance-gated worker. `cli/marketing.py` composes this optional surface from env.
Private DM composition narrows CapabilityPolicy while sharing the service lock and canonical stores.
Operator manifests and merge-to-operation guidance live in `docs/operations/agent-server`.

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
| `agent_service/work_continuation.py` | canonical human input/pause admission, exact event replay at safe boundaries |
| `application.py` | bounded evidence projection, signal boundary, current-memory callback, unchanged runtime dispatch owner |
| `contracts/creative_work.py`, `agent_service/creative_assets.py` | small scoped assets, byte provenance, parent revisions and stale descendants |
| `agent_service/creative_api.py` | authenticated PNG/JPEG upload/preview and same-Run continuation |
| `contracts/agent_memory.py`, `agent_service/memory.py`, `memory_api.py` | attributed reviewed notes, scope-before-query, corrections/expiry/tombstones and selected-memory receipts |
| `agent_service/creative_procedures.py` | ten composable procedures and honest ready-tool/human return briefs |
| `agent_service/slack_image_review.py`, `image_review.py` | authorized file binding/download and actual read-only image assessment, no editing |
| `channels/slack_creative_setup.py` | optional permission-probed tool catalog composition; no service lifecycle ownership |
| `channels/slack_memory.py`, `slack_delivery.py` | authenticated exact review command translation |
| `contracts/marketing_delivery.py`, `agent_service/delivery_review.py`, `delivery_api.py` | prepared review packets only; no duplicate channel execution ledger |

`CodexCli.run_marketing_image_review_job` adds validated image arguments to the existing
no-tools/read-only structured runner. Ordinary judgment calls keep their existing signature.
No provider framework/vector database/custom agent entrypoint is introduced. `creative.prepare`
is a real no-effect local adapter; its output says prepared, never executed.

New delivery API/tool request schemas omit `d1_campaign_id`. The persisted `DeliveryProposal`
contract retains that optional field to preserve old signed history. Generic `PublicationTarget`
drafts and manual performance platform labels confer no Threads API capability.

`delivery_tools.py` owns reasoning-callable preparation and is wired by `lifecycle.py` through
`ConfiguredAgentTools`; `creative_asset_verifier.py` bridges immutable assets to preparation
approval. Neither owns external effects. `AgentJobs` rechecks the API's trusted reviewer
policy immediately before queued approvals. `ToolInvocation.tenant_id` binds new local tool
mutations without rewriting legacy invocation digests.

`work_observations.py` owns immutable human effort records and scoped learning snapshots;
`slack_work_observations.py` translates authenticated report/summary/correction commands.
`work_observation_validity.py` validates sources without importing the memory repository,
so memory selection/review can reuse its connection and preserve transactional currentness.


`slack_image_files.py` owns signed file resolution, bounded download/decode and immutable cache
for both review and intake. `slack_asset_intake.py` owns the inspect/import schemas, current
approval attribution and registration. `slack_creative_setup.py` composes all three optional
file capabilities under the same observed Slack grant. `creative_asset_links.py` owns the
Run-to-asset projection used by HTTP uploads, Slack imports and image production results.

`contracts/agent_run.py` owns the nonterminal `ToolExecutionDeferred` acknowledgement and
`awaiting_tool` Run state. `marketing/runtime.py` owns the deferred event, retained pending
invocation/reservation and exact operation resolution; acknowledgement adds no terminal receipt.
The Agent Service translates adapter acknowledgements and validates eventual results against
frozen invocation/output contracts before recording canonical receipts. Transport authentication
and worker artifact validation must precede that internal completion method.

`contracts/performance_observation.py` owns attributed performance snapshots and comparison
contracts. `performance_observations.py` owns immutable scoped reports, corrections and
learning-candidate construction; `performance_observation_validity.py` validates current
source digests using the memory owner's transaction without a circular store dependency.
`slack_performance.py` translates signed conversation commands; `performance_api.py` exposes
the authenticated, read-only same-Run projection. Neither adapter owns approval or external
metric collection. Human reports are not promoted to independently verified external facts.

`creative_image_edit_contract.py` owns bounded source/region/locale requests and deterministic
composition that restores and checks unchanged pixels. `providers/codex_image_edit.py` owns
the official app-server protocol, restricted tool inventory, immutable input files, generation
start marker and verified output readback. `creative_image_edit.py` owns exact admission,
durable asynchronous job state, source currentness, asset provenance and canonical settlement.
`image_edit_setup.py` composes the explicit configuration and readiness catalog; the existing
service CLI starts its polling thread under the maintenance gate. These owners do not infer
native product support or final visual approval from generated output.

`managed_image_review.py` authorizes exact Run links and current source bytes, delegates to
the existing read-only visual helper, and stores bounded no-replay inference receipts. Its
catalog is composed by `lifecycle.py`; `creative_procedures.py` chooses it for registered inputs.
`creative_assets.describe` exposes stored metadata only for collection discovery; existing
`get` remains the byte-verifying owner. `creative_api.py` bounds same-Run collection discovery,
and `web_ui.py` renders authenticated selected images and human outcome snapshots without
creating another execution owner. Slack's review-page owner also supplies the natural assent
boundary; rendered pages and authorization must not maintain independent pagination contracts.

`image_edit_api.py` exposes exact operation status and reviewer abandonment through the
existing authenticated API. The image queue owner records human abandonment and settles the
canonical deferred operation; HTTP neither invents worker evidence nor calls the provider.

`cli/server.py` owns the persistent `server.json.port` setting written at initial setup and read by
status. The dependency-free Linux manager independently validates and reads the same public setting
for process launch and health checks; both default to 8090. Cross-boundary regression coverage binds
launch argv, update health and status to the same configured port. The standalone manager remains
Python 3.10 compatible and does not import the Python 3.14 application to discover its port.

`marketing/agent_service/github_issues.py` owns fixed-repository issue input validation, private token
loading and GitHub HTTP execution/readback. `tool_adapters/descriptors.py` supplies its external-effect
approval descriptor; `ConfiguredAgentTools` registers it only with a configured credential.
`cli/marketing.py` loads the token at service composition, while `cli/server.py` owns hidden operator
setup and atomic secret storage. `channels/github_results.py` projects successful receipt-bound issue
URLs for both Slack entry points. Canonical run admission, execution checkpoints and reconciliation
remain in Agent Core/service/runtime, with no separate retry or issue state store.

`execution_control.py` provides the channel/provider-neutral cooperative scope and owned subprocess
cancellation. `providers/codex_cli.py` uses it for structured jobs; `codex_reasoning.py` preserves the
cancellation signal. The canonical service owns checkpoints and append-only STOP transitions.
`channels/slack_progress.py` owns only status-message identity and durable cancellation requests.
`slack_events.py` owns signed button authorization, status updates and scoped execution, using the
existing sender transport (`chat.postMessage` for new status, `chat.update` for known timestamps).
The HTTP composition exposes only the signed interaction route during maintenance, without admitting
new runs. Slack manifests own the external callback registration contract.

`agent_service/image_generation.py` owns the image input schema, Codex image turn and bounded PNG
artifact verification. The descriptor remains in `tool_adapters/descriptors.py`; installed lifecycle
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
