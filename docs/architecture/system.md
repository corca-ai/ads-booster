# System Architecture

Status: Active
Last reviewed: 2026-09-14

## Runtime ownership

One on-premises `MarketingAgentService` owns canonical Runs, decisions, exact approvals and receipts
in local SQLite. Ubuntu 22.04/24.04 runs the installed Python service directly through systemd.
The service user's official Codex CLI login supplies reasoning and direct image generation. Local
`service run` is also available for development; the managed server lifecycle is Linux-specific.

```text
Slack / authenticated Web
  -> HTTPS ingress (optional Cloudflare Tunnel)
  -> MarketingAgentService
     -> SQLite: Runs, jobs, approvals, receipts, work memory and asset metadata
     -> official Codex CLI: reasoning, research curation, image generation/review/edit
     -> local knowledge root + catalog: scoped team context
     -> local artifact root: digest-bound images
     -> configured Slack / Notion / GitHub adapters: approved external delivery
```

Cloudflare Workers, D1/R2 campaign storage, the hosted review workspace, Mac/Appium workers and
Simulator execution remain removed. The optional Threads adapter uses only the official API and is
composed when its complete configuration is present. Cloudflare Tunnel remains an optional HTTPS
transport to the server. Source removal does not delete deployed Workers,
remote databases, objects, installed services or existing operator state.

`GET /v1/tools` projects the current tool catalog; `GET /v1/skills` projects versioned procedures.
The registry refreshes readiness at planning and dispatch boundaries. An unavailable image tool
leaves research and human handoff available. An operator configures each daily schedule and its service principal. The scheduler creates one
stable Run per tenant, skill and local date; that principal may preauthorize only the schedule's
declared exact Slack/Notion delivery invocations.

The generic scheduler is separate from the legacy daily research scheduler. `agent_schedules`,
immutable revisions and `agent_schedule_occurrences` own calendar intent and occurrence identity;
each occurrence creates one canonical `AgentRun` through the same drive queue. Every tick, dispatch
and notification rechecks the authenticated Slack source and current membership. Missed content
work is skipped by policy, read-only collection can coalesce to the latest due occurrence, and an
unknown external effect is not replaced with a new Run. Review-mode occurrences notify the source
thread with the exact Run and approval hash. Auto mode can approve only capabilities frozen in the
schedule capability and resource allowlists and remains bounded by its Run budget, end and occurrence limits.

Threads OAuth state is short-lived and single-use. Account ownership and non-secret metadata live in
SQLite while access tokens remain in mode-0600 files. Approved draft revisions issue opaque expiring
URLs for only their exact creative assets. Publication writes persist each pending provider step,
returned container ID, published ID and final permalink. A restart during an unresolved POST marks
the operation uncertain instead of issuing another POST. The bounded Threads reconciliation worker
uses only a known published ID for provider readback, then settles the original canonical Run with a
zero-cost terminal receipt. Provider metrics use append-only snapshots;
human-reported performance remains a separate evidence type.

Meta deauthorization and data deletion POSTs enter through dedicated unauthenticated provider
routes, including while the service drains for an update. The boundary accepts one form-encoded
`signed_request` and verifies its HMAC-SHA256 signature before exposing the provider user ID.
Deauthorization revokes all matching local connections and token files. Data deletion removes the
matching account data and dependent Threads records in one exclusive SQLite transaction after token
removal, then exposes an opaque public receipt. OAuth completion, publication and deletion share a
process fence, so deletion waits for an active provider write. The deletion transaction persists a
connection tombstone that rejects stale publication-ledger writes. Fresh OAuth consent clears that
tombstone after it stores the new account. The durable receipt stores a keyed request digest, not the
provider user ID: an exact replay reuses its result while a later request owns a new receipt.

Threads publish and reply approvals also pass a service-level resource-owner admission check in
every channel. A workspace approver who does not own the draft batch cannot authorize the external
write.

The service is a single process with one execution lock. Durable asynchronous jobs and image-edit
operations release that lock while waiting. No distributed active-active Run ownership is claimed.
Its composition is assembled in `bootstrap/lifecycle.py`: `agent/service/` owns canonical Run
application flow, `agent/runtime.py` owns execution safety, `channels/http/` owns authenticated Web
ingress, and `tools/` supplies configured adapters. This package placement does not change the
public CLI, HTTP, SQLite, approval or recovery contracts.
A source checkout, fake adapter or candidate wheel does not establish live provider, Slack, OAuth,
Tunnel or Linux deployment acceptance. See the [server guide](../operations/agent-server/README.md).

## Evidence-based task completion (2026-09-14 candidate)

`AgentRun` remains the authority for execution, approval, receipts and cumulative budget. A versioned
`TaskSpec` names the current result, response/artifact/effect obligations and their admitted sources.
Its revision is independent of the Run ledger revision. A `TaskCheckpoint` binds that spec digest to
a segment, next action, counters, candidate and unresolved criteria. Task records and runnable-queue
changes commit with the canonical Run transition in SQLite, not a second execution ledger.

Authenticated corrections append their exact instruction text outside transcript compaction.
The original request remains provenance; later instructions can supersede incompatible requirements
while preserving compatible constraints. The actor may add obligations but cannot weaken existing
ones. Only the independent assessor can mark one `superseded`, citing a later admitted event that the
host validates against its sources. Prior accepted results are context for a new task, not its proof.
An active correction invalidates the old candidate. A terminal follow-up starts a new task/segment
on the same Run without refilling its tool/cost budget.

The service advances one boundary at a time and drives bounded slices: default four provider calls
or 20 seconds, checked between calls. These bounds do not interrupt an in-flight call or effect.
Planning and assessment reserve persisted decision calls before invocation; the default segment cap
is 64, including at most three assessments. The final call slot is assessment-only, so an actor cannot
produce an unchecked candidate at the cap. The third assessment may succeed; rejection cannot trigger
a fourth. Waiting does not consume active work time. Canonical invocations and actual receipt costs
determine remaining Run budget across restarts and continuations.

Progress means new evidence or newly satisfied obligations, not a new receipt ID or repeated prose.
Repeated equivalent outcomes, including short alternating failure cycles, trigger one bounded
strategy reconsideration; continued repetition blocks with `no_progress`. Obtainable missing proof
returns unmet criteria for another action. Missing verification infrastructure fails closed as
`verification_unavailable`; input and exact approval dependencies retain their wait states.
Cancellation stops at a safe boundary and does not undo an already-started effect.

A pure renderer receives `CompletionRenderContext(run, records)` and materializes final text, links
and attachment references before assessment. Attachment references come only from candidate-selected,
successful canonical image outputs; actor-supplied references are replaced, and private output strips
them. An empty accepted attachment list means no attachment delivery, not all available images.
`TaskCompletionService` binds
the assessment to the current spec, full candidate and canonical evidence digests. The configured
proof registry selects a host-installed verifier by one composite identity: canonical capability,
owner, executor and effect class. A registration may additionally require an exact installation
identity, as the configured GitHub verifier does; local image verifiers rely on the composite identity.
It checks artifact
roots, digest and image decoding, or supported effect-owner receipts, readback and invocation/approval
identity. The configured `deliver.slack` and `store.notion.daily` registrations bind the
`slack.chat_post_message` and `notion.pages_create` owners to their configured channel/page and
require a successful receipt with the exact invocation and approval binding. Unsupported or
ambiguous identities fail closed. It rechecks proofs after the semantic call
or cache lookup to reject
artifacts changed during assessment. Preparation/no-effect receipts, unsupported effects and human
reports are not effect proof. Long briefs are bounded for model context without invalidating verified
bytes. Visual quality requires actual image-review evidence and remains separate from human approval.

The no-tools Codex assessor receives trusted instruction lineage, the exact candidate and bounded
owner evidence, not the actor's justification. It independently reports omitted deliverables and
required artifact/effect kinds. A host-admitted exact response, response line count, required JSON
fields or required evidence digest can be decided without a model; actor proposals cannot select
these checks. On a later authenticated revision, a failed retained check still reaches the assessor
only to decide source-valid supersession; semantic approval cannot override its host comparison.
Exact semantic results are reused only from a canonical assessment record whose task,
candidate, evidence, assessor identity and result schema all match. Owner proof is re-read on reuse.
Other response tasks use this assessor; unavailable verification has no success bypass.
Subjective semantic judgment remains fallible, even when every deterministic binding passes.

### Durable continuation and result delivery

`DriveWorkQueue` stores channel/principal/event origin and Run revision beside the ledger. Existing
Slack, slash-command and HTTP workers claim work; no new daemon is introduced. A `drive` claim
advances runnable work. A `notify` claim persists its terminal result without planning or executing
tools again, closing the commit-to-notification crash gap. Recovery returns interrupted claims to
their respective phases only after their lease expires. Each process lifetime has one owner identity;
an explicit operation scope keeps that owner and lease across every Run transition in the bounded
slice, updates the revision fence, and publishes the latest `pending` or `notify` target only on scope
exit. A newly authenticated Slack or HTTP input that arrives while another live lease owns the
same Run is returned to its inbox as `pending`; it is not converted into a terminal blocked job.
The owner may renew only its own still-live claim. Atomic claim, owner, lease and Run-revision
fencing prevent a live rolling-restart peer or stale worker from
transitioning another claim. The 20-second slice target is checked only between provider calls and is
not an in-flight timeout. The default 30-minute lease covers four calls at their 300-second timeout
plus headroom;
expired or null legacy claims are reclaimed at startup and before the next claim. Workers recheck
current authority and safe Run state; a durable claim
cannot replace revoked or unavailable authorization.
The slash-command worker releases a notify claim only after its final result/outbox transaction
commits. Recovery before or after that commit can finish notification without re-planning the task.

Pending authenticated input fences completion commit. Notification identity binds tenant, Run,
task/revision, candidate digest, assessment ID and rendered answer digest. Superseded pending
completions are suppressed. Sending/unknown outcomes retain existing reconciliation rules and are
not blindly resent. Accepted task disposition and delivery state are separate HTTP projections:
`task` contains `disposition`, `result`, `accepted_identity`; `delivery` contains notification state.
The same Run detail includes a bounded `execution` view: current phase and next action, derived last
progress reason/time, decision/assessment/tool usage, queue state/owner/lease and a sanitized wait code.
It is not a transcript, provider-token stream or completion-percentage estimate.
Attachment delivery remains with the image owner. An input wait shows the exact latest canonical
`request_input` question only when its checkpoint binds the current task/spec revision. Incomplete
text otherwise reports confirmed obligations and remaining work rather than repeating old questions
or unsupported candidates. Installed fixture regressions cover these boundaries with synthetic
Slack transport. A separate process restart resumed ten durable tool receipts and one final reply;
an OS exit after assessment reservation preserved counters when another process completed the task.

### Completion state compatibility and rollback

Ordinary Slack messages during `awaiting_reconciliation` receive a response-only model turn
with the current authenticated message, bounded conversation transcript and persisted operation
status. This turn has no selectable tools or execution budget, and a tool proposal is rejected.
It does not revise or drive the uncertain Run, replace its knowledge execution source, or take
its drive/notification claim. The response and provider receipt are saved in the Slack message
plan before notification, so recovery can deliver a saved answer without another model call.
The original Run retains its late-result binding. An explicit `새 작업 <request>` can start a
separate Run while the original waits; the conversation retains the original Run ID. Notifications
use that Run's result, original message actor and progress-message identity, not the newer Run's
completion assessment. Later-task dialogue does not steer the older Run's completion. A blocked
completion with no pending invocation also permits a fresh question in a new Run. Pending effects
remain response-only until explicitly separated as new work; none are automatically retried.
These dialogue answers are not task-completion assessments or fresh external-result checks.

Waiting dialogue receives a current, scoped registry inventory as reference context. Its own empty
dispatch snapshot is a turn restriction, not a claim that the service lacks tools or writable
worker workspaces. The provider prompt identifies the configured model literally without inferring
its underlying model family. These reference projections do not grant dispatch authority.

Initial Slack handling and resumed drive slices share the same requested-work authorization loop.
Each invocation rechecks the original authenticated message, unchanged source, current membership
and descriptor before calling normal runtime approval. The whole request, including a `새 작업`
prefix, remains intact for exact intent binding. A slice or process restart cannot introduce a new
human approval checkpoint or grant an unrequested second operation.

The completion assessor receives the same scoped conversation facts and the last planning tool
inventory as reference data, plus the configured reasoning provider receipt. Only admitted task
instructions can revise obligations; reference context cannot prove an artifact or external effect.
Its cache binds that context. Structured provider schemas constrain actor evidence IDs and assessor
obligation IDs to host-supplied values; canonical receipt and artifact validation still run.

Slack progress adds `started_at` to its existing table and retains the initial timestamp across
drive slices and restart. Legacy rows initialize it on first resumed observation. Stages identify
skill lookup, image execution and automatic result checking; elapsed time is not a completion
estimate. Conversation JSON adds `retained_run_ids` only when needed (empty values are omitted).
Older binaries cannot read populated retained-run JSON. Drain pending work and use the established
pre-upgrade backup procedure for rollback; do not start an old binary on newly retained conversations.

The candidate reads legacy Runs by deriving a task from the immutable goal when task records are
absent. Existing records and receipt/approval digests are not rewritten. Reasoning v1 remains readable,
and its public constructor supplies a response-only compatibility assessor so ordinary v1 stops keep
their historical completion behavior while artifact/effect obligations remain owner-gated. Historical
`completed` Runs are not retroactively certified.

Old binaries do not understand the new checkpoint and runnable/notify semantics; backward execution
compatibility is unsupported. Before rollback, use existing managed-service maintenance controls to
stop admission, drain or explicitly stop runnable
harness work, resolve pending/unknown delivery through its owner, stop the service, and preserve a
consistent backup of canonical state and artifacts. Preserve candidate-created work; never silently
replace it with an older snapshot. Start old code only with a separately preserved compatible state
after those conditions are verified. Never run both versions against one live database.

The September 12 disposable installed rehearsal refused old-reader use while runnable work remained,
then drained work and notifications, backed up SQLite and verified integrity and unchanged canonical
digests through the old installed read-only repository. This proves those controlled operator
preconditions, not an automatic downgrade guard or backward execution compatibility. Fresh installed
CLI, loopback health, completion and process restart were exercised; production deployment/rollback
still requires separate authorization. See [verification scope](../development/testing.md#task-completion-harness).

## Browser and Slack admission

The installed service exposes read-only `skills.list` / `skills.read` tools alongside action
tools. `marketing.analyze` is a zero-cost observation of caller-supplied, bounded funnel counts,
registered even without external credentials and allowed in admitted private Slack conversations.
It returns arithmetic and limitations through canonical invocation/evidence/receipt records, without
network calls or separate state. Decimal strings preserve exact portable receipt serialization;
semantic input rejection is a known failed receipt, not an uncertain external effect.
Numeric-only reports may omit currency with spend absent; output preserves `currency: null`.
Reported spend, including zero, still requires currency. Existing currency-bearing inputs remain valid;
the current descriptor advertises the relaxed schema while historical receipts stay immutable.
Growth/customer-insight procedures guide outcome selection, customer evidence and finished copy.
Strategy v2 adds campaign measurement and execution dependencies; performance-report v1 applies
data-quality checks and selects existing funnel arithmetic only for compatible counts. Copy v3
adds reader usefulness and claim review. These remain on-demand versioned procedures in the
existing catalog: no new tools, context injection, state store or effect authority is introduced.
Persisted goals retain their recorded procedure; new reads expose the current built-in version.
Knowledge context receipts bind the complete selection observation, including exclusions and
observation time. Stable block labels alone cannot identify a selection after a skill is learned.
Existing receipts remain immutable; this identity change needs no data migration.
The planner discovers purpose/version metadata, reads a selected procedure, then chooses
each subsequent action against its current capability snapshot and observed receipts. Procedure
loading neither executes a skill Run nor grants production/publication authority. Reads cost zero
operation units but consume the existing tool-call budget. Skills cover opportunity research,
strategy, copy and experiments as well as creative procedures. Creative v2 guidance follows
available execution tools after preparation; human assistance is conditional on an actual blocker.
Existing persisted goals keep their recorded procedure; new creative skill Runs use version 2.
Later planning boundaries may repeat a run-scoped observation to refresh read-only state. Each such
invocation has its own claim identity; effectful and tenant-scoped idempotency identities remain
stable, and restart replay continues from the original persisted invocation.

The Codex reasoning provider's conversation contract treats Trace as a teammate with marketing
expertise. It maintains the current request through language/format changes, uses reasonable
creative defaults for delegated drafts, and presents results rather than internal planning text.
V2 tool decisions classify direct delegation with a `current_user_message` authorization source
marker bound to the exact reasoning request receipt and pending intent. Slack still rechecks the
authenticated current event, actor and exact invocation; pre-marker v1 and v2 decisions retain the
complete-message echo rule. Slack-client attribution text does not grant broader effect authority.
Skill inventory questions query the scoped catalog instead of treating selected context as the
whole catalog. Procedure reads must lead to applicable work; compatible numerical reports use
the available calculation tool. Shared skill writes still require the current explicit user request.
This changes model guidance, not tool registration, approval, budgets or canonical history.

Ordinary Slack replies and asynchronous updates omit Run diagnostics. Nonterminal updates use
the persisted execution state rather than presenting the last tool-selection rationale as an
answer. Both thread status and slash-command status project the current Run and last committed
step under the execution lock. Interrupted reasoning, interrupted dispatch and asynchronous tool
waits receive distinct descriptions; an earlier reasoning answer is never a nonterminal status.
The slash command retains its explicit Run ID. Approval review continues to expose the exact
invocation and digest; notification durability is unchanged. Failure logs preserve bounded exception
categories and product-code locations across wrapped causes without messages, source text or locals.

Each Slack planning boundary also receives a fresh, bounded projection of that conversation's
completed message/reply pairs. Same-Run follow-ups therefore retain prior alternatives after
restart. This projection is reference data, not verified product knowledge or approval. It is
re-read from the authorized inbox rather than persisted as another reusable knowledge record.
Private DMs may read the server-owned skill catalog; their action allowlist remains read-only.
The host additionally projects the latest admitted continuation as `current_user_message`, outside
bounded reference evidence. Only direct continuation records qualify; nested source/tool text does
not set the task. This separates immediate user intent from the immutable original goal without
conferring approval. Knowledge retrieval uses the same current input. `marketing.context` supplies
the on-demand procedure for scoped wiki, memory and source reads. Missing tools, no search hits
and an actually empty corpus are different observations.

The installed service accepts one configured tenant; OAuth identities for other tenants cannot use
that instance's shared integration credentials. Browser /auth/login uses authorization code with
PKCE and a one-use, browser-bound state; /auth/callback exchanges the code at pinned HTTPS
endpoints without redirects and introspects the access token. Provider tokens remain in bounded,
expiring server memory. Secure HttpOnly cookies plus same-origin/CSRF checks authorize browser
mutations. Restart invalidates browser sessions, not Run history.

The browser submits durable /v1/jobs and polls status, so long reasoning turns do not hold the
Tunnel HTTP request open. A single service execution lock serializes web, scheduler, and channel
mutations. This is a single-process service, not active-active execution.

The /channels/slack/commands route verifies real Slack form bytes/signatures and timestamp, app,
team, configured shared channel and operator-bound user before writing durable command admission.
It acknowledges without reasoning, and a background worker invokes the same canonical service.
The operator file binds Slack users to stable member identities, with separate reviewer grants.
Exact invocation hashes and input revisions reject stale mutations. Removing a configured user
revokes new and queued command access after restart.

Slack response notifications have a persisted dispatch marker; unknown delivery is not repeated.
Interrupted create jobs can reconcile through canonical Run replay; uncertain input/approval jobs
remain blocked for status readback. Notifications are channel responses to user commands, not
planner-granted publishing authority. The daily Slack-only skill grants only its declared Slack
delivery invocations and never unrelated external effects.

The [server packet](../operations/agent-server/README.md) defines deployment and live acceptance.
No live OAuth/Slack/Tunnel or Linux systemd success is implied by local tests.

## Server-owned knowledge context

The on-premises `MarketingAgentService` is the integration owner for team knowledge. The knowledge
domain owns its own absolute `knowledge_root` and SQLite catalog, while the existing Agent Service
database remains the owner of Runs, Slack admission, and Run bindings. `KnowledgeSettings.from_env`
requires all three values together: `TRACE_MARKETING_KNOWLEDGE_ROOT`,
`TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT`, and `TRACE_MARKETING_KNOWLEDGE_POLICY`. All paths must be
absolute. With all three absent the feature is disabled; a partial set raises a configuration error.
The service user owns root/control directories at mode `0700`; policy and `control_root/identity.json`
are private mode `0600` files.

Managed startup additionally runs the installed `cli/server_knowledge.py` migration before service
exec. If the persistent service environment has no Knowledge settings, it initializes tenant-bound
defaults and publishes only the three path assignments after initialization. Existing settings and
knowledge are preserved; partial settings fail closed. The same startup receives the returned paths
without waiting for another systemd restart. This candidate-startup boundary also runs when an older
updater performs the first switch. Initialization is additive and retained on rollback; it does not
rewrite prior identity or policy. The updater reads persistent Knowledge paths without importing
Slack credentials into the update unit, so configured Knowledge participates in backup/restore.

When enabled, `cli/marketing.py` builds `InstalledKnowledgeRuntime` beside the canonical service.
It registers the local actor, creates the SQLite knowledge repository, canonical ingress, ingestion,
retrieval and tool host, Codex curation provider, owner lock, bounded job runner, index worker, and
memory-view dispatcher. `CurationBatchRuntime` defaults to collecting compatible routine jobs until
60 seconds after their first event, without extending the deadline for later arrivals. It claims
urgent work immediately and runs one shared bounded model round at a time for every active job.
Shared feedback learning uses a separate durable workspace readiness counter: the tenth admitted
shared conversation turn or terminal tool receipt seals one logical review round. This wake-up rule
does not combine member, session, scope, grant, or policy partitions; the existing curation owner
still processes each compatible partition. Immediate corrections bypass that routine threshold at
the next safe foreground boundary. The existing 60-second collection window remains in force for
other routine curation jobs.
The live `KnowledgeRuntime` dispatcher drains ingress, terminal experience, curation, index, and
memory-view work under the existing owner and maintenance activity gates. Terminal experience
outbox recovery runs before normal processing, so a committed terminal receipt can resume delivery
without replaying the foreground tool.
Bounded job and curation workers start with a fresh spawned interpreter. They do not inherit the
service's active thread locks, SQLite handles or other process-local state. Processor dependencies
must therefore be serializable configuration and paths rather than open resources.
Unavailable or superseded sources fail their individual queued curation jobs with a durable reason;
they do not terminate collection of other jobs. Maintenance evidence reads preserve the source's
original speaker while applying the current worker's read authority.
The HTTP health route receives the configured Knowledge thread's actual liveness probe. An exited
thread produces HTTP 503/degraded while release and maintenance fields remain available. A running
thread is not a guarantee that every job has finished. The updater reads a degraded 503 body so the
current service can drain and install a repair, while candidate activation still requires healthy
status and fails closed on a stopped worker.
Each later round receives the actual guarded tool observations from earlier rounds; terminal
job decisions become receipts bound to their original event and revision. Explicit flush updates
the indexed and serialized batch state in one transaction. Cancelled unfinished jobs re-enter
collection with a deterministic generation derived from persisted terminal batch history; earlier
receipts remain intact. Startup recovers abandoned running batches only after acquiring the exclusive
owner lock; completed per-event receipts remain terminal and unfinished jobs return to collection.
For a sealed learning round, the batch runtime reattaches released queued jobs to a new ready attempt
in the original round and actor/grant partition. This also covers work-construction exceptions;
it does not increment the learning counter or admit an unsealed round. The old attempt remains
terminal. If current authority or the persisted binding cannot be restored, the jobs fail with a
specific recovery reason and the round is reconciled instead of remaining queued indefinitely.
Private batches reload their registered actor and current private grants from the catalog rather
than inheriting the local administrator's identity or workspace grants. Revoked private jobs and
unclaimed batches fail durably without preventing later authorized work. The runtime starts as a daemon loop with the service and stops before the
service releases its owner lock. Standalone
knowledge CLI ownership is separate from the service owner and must not share a live root. The
registered local-admin surface covers `init`, `doctor`,
`ingest`, `run`, `search`, `get`, `context`, `schedule`, `backup`, `restore`, `retract`, `purge`,
`questions`, plus memory, brand, and task subgroups. Every operation takes the absolute `--root`,
`--control-root`, and `--policy` paths; `run` and `memory consolidate` take optional `--model` and
bounded `--once`/`--until-idle` controls. Installed commands and fresh-install behavior require their
own verification.

Ingress remains authenticated at the existing service/channel boundary. The installed API and Slack
adapters share the canonical ingress and its authority bridge. Before admission, the bridge maps the
authenticated actor to stored member, session and read/write grants at the current policy epoch,
preserving revoked memberships, closed sessions and reader roles. Outbox replay and context
preparation recheck the stored authority. Active task lookup includes member and session identity.
A shared Slack conversation maps to `CHANNEL(workspace_id, channel_id)`, independently of its
thread and speaker. The signed event or stored conversation supplies the channel ID. Each Run
receives grants for only that channel. A Slack DM maps to member plus conversation scope and
receives only private grants through a read-only capability set. The DM projection permits knowledge search/get, memory get/explain, and
source read; it excludes knowledge or memory writes, scheduling, purge, and external delivery. The
canonical ingress stores the binding, conversation event, and durable outbox before dispatch. Edited,
deleted, or correcting events create a pending fence; context preparation blocks the affected Run
until the new source state is admitted. A dispatch that durably records a classified item failure
returns progress without acknowledging or automatically retrying that item, so later ingress and
maintenance continue. Unknown receipt outcomes and persistence errors still propagate.
Source fetching admits only 2xx or 304 after redirect handling. HTTP 408/429 and server errors are
retryable; other unsuccessful statuses fail before their body can enter extraction or curation.

Only authenticated members admitted to shared Slack threads contribute turns or terminal experience
receipts to workspace learning. Private DMs keep member and conversation scope, remain read-only for
shared knowledge, and do not increment the shared counter. A terminal tool result enters learning
through its stored Run, invocation, receipt, and source binding; tool text never becomes a synthetic
user event. All eligible terminal outcomes count toward readiness. Reviewed complete successful or
observed evidence can support a reusable procedure; failed, unknown-side-effect, and invalidated
outcomes stay evidence without promotion. A completed `no_effect` receipt is terminal work for
readiness, not effect success. Actual
experience evidence retains typed invocation input and typed output, their digests, the receipt, and
the source/Run binding. Existing reviewed SQLite work and performance notes may provide read-only
evidence, but the knowledge owner does not dual-write or auto-approve them. The service selects
those notes through the existing SQLite owner for the exact knowledge workspace, source Run, and
currently admitted actor. A stable selection fingerprint binds note IDs and digests without binding
incidental selection time. SQLite selection receipts retain canonical JSON alongside indexed run,
full scope, actor, and UTC selection-time columns. An additive, transactional migration backfills
valid existing receipts once; malformed or actorless legacy receipts cannot satisfy a current actor
binding. Latest selection reads one exact-binding row ordered by UTC time and selection ID, then
revalidates the receipt through the existing current-selection guard. Every global learned-memory or
skill mutation must assess every selected
note as compatible, unrelated, or conflicting. Missing, mismatched, or stale assessments reject the
write; a declared conflict preserves the proposed head and creates a source-thread question whose
external note references are rechecked before display or answer. Task-only overlays do not publish a
global change and stay outside this guard.

The knowledge owner derives the full built-in skill catalog as protected records and stores only
source-bound learned revisions or explicit foreground overrides. Every `skill_apply` operation,
including a complete strict record, must cite only evidence admitted to its trusted invocation:
the current authenticated foreground user event. Every creation, update and retraction requires an
explicit skill directive in that event, a matching action, one target per call, and current source
provenance. Background curation excludes `skill_apply` and host validation rejects background writes.
Slack channel actors may publish workspace-owned procedures through this narrow authoring boundary;
the commit transaction rechecks channel write authority and current human request provenance.
No workspace data grant is added. Cross-channel skill reads use a server-only source revision/hash
projection; original channel text remains protected by its existing source-read boundary. Edits,
deletions and blocked sources invalidate the published procedure and selected context dependencies.
Private/member sources cannot publish workspace skills. The explicit-directive recognizer accepts
bounded first-line command forms and common direct Korean/English requests; ambiguous text requires
a clarified user request. Built-in overrides additionally require the exact skill ID. If a built-in
release digest changes, the current built-in is the effective
fallback and the override remains pending review. Normal learning emits no Slack notification. An
unresolved same-applicability conflict creates one durable question in the original thread; the
answer follows the knowledge-question path and is separate from ToolApproval. This extension reuses
the existing repository, curation provider, ingress, and lifecycle; it adds no provider, daemon,
store, or verifier.

Foreground learning records a consumed target by source revision and target ID after an applied or
replayed `memory_correct` or `skill_apply` result. This validates the current source independently
of background-job policy and does not enqueue curation. Later review skips that exact target while
keeping
other evidence from the source. This prevents a foreground correction and a background round from
applying the same target twice.

The enabled knowledge tool host exposes `skill_list`, `skill_get`, and `skill_apply`. Prepared
context carries metadata for the effective skill index and selected revision receipt; `skill_get`
loads the procedure body. Each `selected_skill_revisions` entry carries nested `source_refs` and
`source_revisions`; these are separate from generic retrieval references. Binding-free Runs receive
neither the learning tools nor this context.
Private DMs may read shared skill metadata and bodies under their current grants, but cannot write
skills.

The scoped skill catalog is preferred over built-in-only discovery when both read tools are
available. Both catalogs support bounded keyword queries and offset pages; each read binds the
returned version/revision. Prepared skill metadata is ranked by the current task query and admitted
individually within at most 2,400 budget units and half the post-required-context capacity. Budget
exclusions remain in the receipt. Required capabilities missing from the filtered snapshot are
listed as unavailable, not inferred to be uninstalled or newly authorized. Unrelated evidence
groups retain their atomic budgeting rules.

`marketing.skill_learning` guides source-bound procedure creation/update, current-revision CAS
and readback through the existing knowledge owner. The reasoning provider advertises authoring
only when `skill_apply` is present; private filtering excludes every non-read knowledge tool,
including skill mutations. Stored procedures do not install code, register tools, expand the
capability snapshot or grant external delivery authority.

`source_read` returns verified segment `evidence_ref` and `quote_sha256` values for reuse in guarded
memory and Wiki writes; an arbitrary text range carries its quote hash without inventing a segment
identity. Curation uses the extracted text and character offsets. Its optional
`authenticated_user_event` contains evidence and authority references only for a canonical matching
user message with no quoted spans; imported documents and other conversation roles do not gain
user-instruction authority.

Side-effect-free question input errors are returned to the curation model as observations within
the existing decision budget. A successfully stored question still waits for an answer;
authorization, proposal-binding and conflict failures remain terminal. Question evidence IDs and
new-page revision expectations are described in the tool schemas, rather than left for the model
or operator to infer.

Before reasoning, the service adapter assembles a bounded context receipt from current revisions,
constraints, grants, and task/brand binding. It stores the selected immutable revision references and
rejects unavailable or inactive brands before changing the current task. It
rechecks that receipt immediately before tool dispatch. Image production rechecks current knowledge authority before effects. Generic transfer contracts
retain digest and revision provenance; removing the former hosted transport does not clear historic
replica records or supply an external purge receipt.

Deletion records a manifest and chained erase-ledger entry in the control root before blocking live
reads. Tombstones and reverse dependencies cover source, Wiki, memory, claims, context receipts, and
transfer replicas. Retracting a message source also blocks its canonical conversation evidence and
derived memories; purge scrubs those canonical message payloads, including earlier revisions.
Independent sources and other workspaces retain their evidence. Preexisting remote replicas remain `purge_pending` until separately reconciled;
the service has no remote purge transport and does not execute or acknowledge external deletion.
CLI purge resolves its target from the applied retraction receipt, retaining request
identity on replay. Restore validates the manifest and file digests, applies the current erase ledger
including mixed-memory redactions, and rebuilds search in a private staging root before activation.
No external deletion or deployment success is implied by this source wiring.

## Research and execution contracts

### Local dynamic evidence research

`trace-marketing agent research` is a separate local composition root for the provider-neutral
runtime. It invokes only observe capabilities. One
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
Automatic channel outcome collection is not configured. The installed local research
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

## Linux main tracking and Slack-only operation

The Linux operator entrypoint is `install-server.sh` plus `trace-marketing server`. Bootstrap fetches
public main over HTTPS, applies the same exact-SHA CI/protocol admission as updates, installs locked
dependencies and selects current before exposing the CLI. A completed install is preserved on rerun;
a conflicting CLI is rejected. The bootstrap prepares missing system packages and pinned tools.
GitHub checks use anonymous HTTPS, without gh or an account. Root bootstrap drops to a named
unprivileged user; user services use linger. Explicit source installs retain source provenance.
`server setup` validates Slack auth.test and member/approver bindings, writes private configuration,
and generates systemd user units using discovered executable paths. It exports domain-specific Slack
manifests from wheel-packaged assets. Existing configuration and unowned units are never overwritten.
An optional `trace-marketing-tunnel.service` runs an already-created Cloudflare tunnel with a private
token file. DNS and hostname routes remain existing Cloudflare configuration; no account changes are
made. `server start` enables the agent/tunnel/update timer and reports a missing linger setting.
`server status` reads local/public health and unit status; `doctor` reports prerequisites. `update`
requests the existing update service asynchronously. `stop` stops owned units without disabling them.
The former ZIP/wheel packet remains a recovery/development path, not the standard onboarding flow.

`TRACE_MARKETING_SLACK_ONLY=1` is an explicit public-ingress mode for operators without an IdP.
Only health and signed Slack commands/events are exposed, on loopback behind the Tunnel; web UI,
OAuth/session routes and bearer API access return 404. App/team/channel/member binding still applies.
Slack review pages project the full pending invocation and its exact approval hash. No browser
login or company OAuth is required in this mode; Cloudflare email-login integration remains unimplemented.

The systemd user agent and five-minute updater are separate processes. The updater has GitHub read
access and no agent secret EnvironmentFile. It fetches main and verifies the exact SHA's dedicated
on-prem check (`Verify on-prem agent`, GitHub Actions, completed/success). That check includes tool
adapter contracts; unrelated checks do not govern server installation.
It installs a non-editable locked candidate and verifies
its update protocol and installed doctor before requesting maintenance. One MaintenanceGate accounts
for HTTP admission, queue recovery/dispatch/delivery and scheduler work. No new work enters while its
file exists; active work drains without interruption. If it cannot drain in five minutes, the update
is deferred and admission reopens. On quiescence, stop -> offline state backup -> atomic current
symlink switch -> passive startup with SHA health readback -> activation. A failed passive startup
restores code and canonical state before reopening admission. A persisted transaction recovers an
interrupted switch; after activation is committed it never rewinds records. Failed SHAs are quarantined.
State and configuration live outside releases. Backups and failed state are retained for operator
reconciliation. Existing cloudflared services are independent and must not be replaced by this updater.

## Slack conversation Events boundary

HTTP `/channels/slack/events` verifies raw-body signature, timestamp, app/team, configured members
and allowed channels before durably admitting text. Signed URL verification does not call reasoning.
app_mention starts a thread; ordinary message events only join an admitted thread. Bot/subtype,
unrelated channel chatter and external shared-channel envelopes are ignored. Message identity binds
team/channel/timestamp, preventing duplicate retries from creating new work.

Four maintenance-gated Slack worker lanes drain commands and conversation jobs. Startup recovery
finishes before these lanes claim work. Durable claims exclude another running job in the same
conversation (or slash-command Run); unrelated conversations may call the model concurrently.
Plans and canonical mutations hold a reentrant `(tenant_id, run_id)` lock, shared by private and
shared service projections. Follow-ups bind to the latest thread Run only when dequeued.
Awaiting-input replies and ordinary completed-work follow-ups continue that Run at its saved revision.
The original goal and full ledger remain stored; explicit new-work requests start a new Run.
Private DM tenant IDs derive from workspace, member, channel and thread; unthreaded messages use the
member's ongoing DM session. Private services share the canonical Run locks/ledger but expose only
public search, skill discovery and registered scoped knowledge/memory/source reads, with no
shared-context mutation or delivery tool authority. Knowledge must be configured for those reads
to appear; current actor/session grants are still checked by the knowledge owner.
Shared-thread corrections remain bound to the admitted source and current Run, including while a Run
awaits input. Clear CORE corrections do not wait for the 10/10 review threshold. Routine learning
stays silent; only an unresolved same-scope conflict sends a durable question to the original
thread. Private messages never contribute to the shared counter or shared writes.
Completed shared create, input, resume, and revise plans admit their turn through the stored source
receipt. Edited or deleted source events invalidate superseded learning admissions after canonical
acknowledgement; an edited current source may then enter the urgent correction path.
Ordinary completed/input-wait answers omit diagnostic footers. Explicit status requests and
exceptional runtime states retain diagnostics. Enabled shared work links use a separate Slack
context block; private conversations never receive that shared Web projection.

Conversation acknowledgements/results target only the admitted original conversation. Each send has
a durable marker; ambiguous sending state becomes unknown on restart and is never blindly retried.
Authorization is checked again before execution and notification. Saved create plans use canonical
Run idempotency/reconciliation; interrupted input/approval/resume plans are blocked for inspection.
Approval is an explicit reviewer action bound to the exact current invocation hash, never inferred
from free text or inherited dialogue. Closing a thread stops later responses, not in-flight effects.
Slack settings and Ubuntu live acceptance are documented in the server launch guide.

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

## Continuing small work (candidate, 2026-09-07)

Agent Service remains the sole new Run/decision owner. Slack's signed durable inbox admits
text and bounded file references. Ordinary thread follow-ups resume the same safe Run with
cumulative budget; `새 작업 ...` starts independent work. Pending input signals are read
without waiting on the execution lock, then committed to an input wait before the next
plan/fresh dispatch. Started effects retain receipt/reconciliation ownership. Closing
conversation replies and pausing work remain distinct.

Same-event human continuation is idempotent across the admission/input/provider boundaries.
Every replan selects bounded canonical evidence with selected hashes/omission accounting;
raw events remain intact. Current approved memory is separately retrieved by trusted
workspace/product/member/session scope before relevance, with a persisted selection receipt.
Prior user/model text is not a current approval or reusable rule. HTTP memory drafts derive
workspace/author from OAuth identity; adoption currently uses authenticated Slack reviewers.

Creative uploads store real PNG/JPEG bytes below a tenant/digest artifact root, immutable
source/revision/locale/preserve/change metadata and same-Run human input. Pixel decoding is
not visual QA. Source revisions mark only dependent assets stale. Optional Slack image review
binds signed file IDs to tenant/Run before fixed-origin file lookup; actual official Codex
image input yields model assessment and human-review-required status. It never edits images
or verifies native app capabilities. DM tool scope remains read-only; image review is unavailable.

The optional image tool requires `TRACE_MARKETING_SLACK_IMAGE_REVIEW=1`, a `files:read`-capable
Slack token and a confirmed identity/scope probe. Existing manifests, tokens, login, tunnels,
installers and service units are unchanged. Missing permission never makes startup fail.

Delivery review is a local preparation projection, separate from actual D1 effect facts.
Production/publication/Paid/format/code/public-amendment targets have independent digest/CAS
reviews. `scheduled_prepared` is not a provider reservation or scheduled public post. Every
packet declares external execution disabled; user reports and injected owner readback are
distinct. Existing effect owners and their approvals still govern any future activation.

The installed composition registers `delivery.prepare` as a local no-effect tool. New
ToolInvocations bind the trusted tenant; legacy invocations omit that optional field from
serialization so existing approval digests remain valid. The preparation adapter requires
the tenant and exact Run lookup and rejects caller-supplied scope/approval fields.
HTTP direct and queued approvals require a trusted reviewer policy in addition to OAuth;
queued execution rechecks it. Legacy/unmapped queued approvals become blocked records.
Asset-bearing approval and scheduling use the current creative asset owner to recheck bytes,
revision and ancestors; prepared Paid reservations share one SQLite transaction.
Slack memory defaults to the current work; explicit `기억 공용` in shared channels creates
reviewed product-scoped learning. Private chat cannot widen that scope.

Human effort reports use the same Run identity and workspace/member/session scope. Slack
records phase, locale, reported minutes/revisions and the report time, without deriving an
execution interval. Append-only corrections replace earlier reports in totals. Learning
candidates bind exact report snapshots; memory review and selection validate those sources
on the same SQLite connection before writing approval or selection receipts. Tool receipt
cost units are shown separately and are not currency or human time.

Optional Slack file intake reuses the signed tenant/Run/file binding and bounded downloader.
`creative.file.inspect` observes bytes without model inference; `creative.asset.import` binds
an exact digest and proposed source/use metadata to the existing canonical approval. A current
approval and matching bytes are required before registration. The receipt records human
confirmation, not independent rights/visual/product verification. Registration feeds the
current Run through its tool receipt, avoiding nested continuation/planning. Only registered
assets enter the shared Run-to-asset projection; a crash before linkage can replay local
registration without exposing another asset. DM intake remains disabled.

Deferred tools return an operation/executor binding, not a successful output. Canonical
acknowledgement and runtime events preserve the accepted work across restarts without invoking
the adapter again. The Run waits in `awaiting_tool`, releasing the service execution lock for
other Runs. Cost remains reserved until an exact terminal receipt is validated and persisted.
The completion boundary is internal; no unauthenticated or operator HTTP callback is introduced.
Synchronous adapters retain their existing behavior.
The owner records explicit worker uncertainty separately in `awaiting_reconciliation`, retaining
the reservation until validated readback. Waiting alone does not imply uncertainty or retry.
Human follow-ups remain canonical task input; a pending pause stops subsequent planning after
the admitted result is recorded. A later revision can supersede that pause. Completion retains
the original admitted approval even when its admission window has since expired.

Canonical acknowledgement precedes the runtime acknowledgement; canonical completion precedes
runtime settlement and receipt projection. Recovery repairs those local boundaries without
re-entering the adapter. Legacy serialized sessions are unchanged, but older binaries reject
the newly reserved deferred events. Rollback must preserve the database and use a compatible
reader for Runs containing those events; do not delete pending state to downgrade.

### Human-reported marketing outcomes

The canonical service stores attributed performance snapshots alongside a Run, with attributed sources and uncertainty. Signed Slack commands derive workspace/member/session/work
scope from authenticated membership and the conversation; payloads cannot supply authority or
declare metrics verified. Each snapshot retains account, country, publication reference, UTC
window, source digest and author. Missing clicks/installs remain unknown. Corrections are
immutable successors restricted to the author or an authenticated reviewer. Listing returns
the latest bounded set; comparison preserves separate snapshots and mismatched conditions
without aggregation or causal attribution.

Learning candidates freeze their source digests, observation, counterexample and applicability.
Memory review and context selection recheck current sources in the same database transaction;
a correction prevents adoption or selection of the stale candidate. The existing memory
review/approval lifecycle remains mandatory. `GET /v1/runs/{run_id}/performance` is a bounded,
authenticated read projection, with no private-chat promotion or write authority. Collection
from live marketing accounts, attribution to installs and measurement of actual lift remain
separate integrations; reported numbers do not establish them.

### Optional bounded raster production

`TRACE_MARKETING_IMAGE_EDIT_CONFIG` enrolls an explicit official Codex executable/model in
the shared service. No login, server unit, publishing setting or private-chat production
authority is changed. Planning readiness retains its observed timestamp for at most 60
seconds; execution checks readiness again. Missing readiness leaves preparation and human
continuation usable.

An admitted invocation freezes source revision/digest, requested regions or top extension,
locale/text, configuration and exact production approval. The SQLite queue waits for canonical
acknowledgement before recording start. Provider execution runs outside the service lock, so
other work can continue. A durable start without a terminal receipt is uncertain and cannot
trigger regeneration. Known preflight failures settle without effect; verified output failures
retain the generation cost and fail without presenting an asset as successful.

The compositor restores unchanged original pixels and verifies their equality. Asset provenance
retains original/generated/composed digests and whether generated pixels were resized. This
deterministic check does not validate translation, fonts, seams or visual taste; model visual
review and human review remain pending. Same-Run asset linking and the existing Slack outbox
receive the validated result. Pending pause/revision and changed source state retain their
canonical meaning. Edited promotions do not prove actual Trace language or font support.

Managed visual review authorizes exact same-Run asset links before reading source bytes or
cached inference. It rechecks lineage before and after the existing Codex visual helper and
retains an unresolved start record after response loss. Source changes invalidate cached review;
model findings cannot update human approval or native-product facts. Preparation selects this
capability when managed inputs and readiness exist, independently of Slack file permissions.

The Web Run projection lists only linked asset metadata, bounded separately from byte readback.
It loads a selected PNG/JPEG through the authenticated single-asset route and displays origin,
locale, QA and stale state. Metadata listing does not read or verify every image. Delayed asset
and performance responses cannot overwrite another selected Run. Shared Slack summaries link
to the configured Web origin only when public links are enabled; private history is not promoted.

Shared Slack execution requests authorize the requested work without a second user-facing
approval or review phase. The reasoning provider cites the complete current request in
`authorization_message` for each requested step. The channel verifies its unchanged finalized
source, current run-creation permission, tenant/conversation and exact invocation, then records the
grant through the existing service. All exposed tools with `workspace_member` authority use this
path; it is not restricted to a creation allowlist. A request can cover multiple steps within the
run budget. Each step rechecks source and membership; receipts and runtime idempotency remain the
owners of duplicate/uncertain execution. Model interpretation does not prove universal language
accuracy. Questions, negation, draft-only requests and unrequested actions confer no execution
authority. Publication or spending requires a request covering that destination and scope;
creation alone does not imply either. Private DMs remain read-only.
First-use workspace members may request execution with `can_approve=False`; that field controls
reviewing a separate proposal, not delegating their own work. Disable/revocation and source freshness
are still checked before every requested step.

Completed generated images are delivered results, without a mandatory human-review checkpoint.
`review_status=not_reviewed` records provenance without claiming human review or visual quality.
Feedback is optional. Existing explicitly requested review tools remain available.
For an unrequested proposal, Slack shows the complete readable tool inputs or retains raw
paginated review for long inputs. Plain assent binds only a proposal already delivered to that same
user when the assent message is admitted. The admitted message stores that digest; later delivery
cannot retroactively qualify queued assent. Execution rechecks current source revision and approval
permission. Editing/deleting an assent or changing its target rejects execution. Exact hash commands
remain available, and brief refusal needs no hash. Public delivery retains its separate authority.

The pending proposal is replayed from canonical invocations, decisions and approval records,
independently of the latest conversational answer or read-only tool. Answering a question cannot
complete pending work. Explicit cancellation/replacement can clear it; stale hashes remain invalid.
Execution recovery uses the persisted EXECUTE step's invocation digest, including after intervening
reads. New optional decision/approval fields omit default values when serialized to preserve old
record digests; no history rewrite or database migration is required.

Slack thread and slash-command review input share `SlackCommands.review_input`; malformed,
oversized or out-of-range page arguments return guidance without entering continuation or
changing approval state. Complete readable proposals and unchanged authoritative `review_pages`
renderings count as delivered review evidence. Event failures project approval rejection codes into
actionable replies and log only message identity, action and a fixed code. Unknown exceptions
use `unclassified`, disclose no exception payload and retain blocked message state without requiring
a status command or operator contact. After reasoning fails at an OBSERVE boundary, a new user
message can revise the same Run under the execution lock. PLAN, APPROVE and EXECUTE interruptions
retain their original recovery paths; new input must not conceal or replay uncertain effects.

Unknown edit operations expose a scoped status and explicit reviewer abandonment endpoint.
The API derives authority from current authenticated membership, never the request body.
Abandonment binds the pending operation/invocation, reviewer, note and time to a durable
human-reported terminal failure with reserved cost consumed. External outcome remains unknown;
this is not a no-effect or verified provider failure receipt. Identical requests repair
completion/outbox projection without re-entering generation. The original start ledger and
artifacts remain available. Queued, unrelated or changed operations cannot be abandoned by
that decision. No automatic timeout abandonment is introduced.

### Managed server port

The default agent listener is loopback port 8090. New setup persists `port: 8090` in the
credential-free `~/.config/trace-marketing/server.json` outside the selected release. The process
launcher, updater health/drain/activation and operator status use this same integer setting
(1–65535, default 8090). Cloudflare independently routes the public HTTPS hostname to localhost:8090;
Slack callback URLs retain HTTPS without an internal port suffix. The updater need not load agent
secrets to learn the port. The standalone `service run --port` remains an independent explicit CLI.
Older fixed-8765 managers require an idle/offline reinstall with preserved configuration/state/current
link backup before the new channel can take over; ordinary self-update cannot bridge that change.

### Slack GitHub issue creation

The optional `github.issue.create` integration writes only to `corca-ai/ads-booster`. A private
operator token file takes precedence at service startup, outside release state; `server github-setup`
checks repository access and writes it atomically without rewriting Slack setup. Tokens never enter
catalogs, reasoning requests or receipts. When no default file exists, the same service process
resolves `GH_TOKEN`, `GITHUB_TOKEN`, then its own `gh auth token --hostname github.com` login.
The CLI receives fixed read-only argv, no shell or model input, closed stdin and a ten-second timeout;
its output stays inside the credential adapter. Missing login/CLI leaves the integration absent.
Explicit invalid file configuration never falls back. `TRACE_MARKETING_GITHUB_ENABLED=false`
disables all sources. Startup resolution is shared by normal service starts and updater restarts;
no developer credential is transferred and authentication alone does not prove issue-write permission.
The GitHub REST adapter rejects redirects, POSTs only the
approved title/body, GETs the created issue number and verifies its URL and exact text before returning
a minimal receipt. Known HTTP rejections return sanitized failure; uncertain mutation or readback
results use the canonical awaiting-reconciliation boundary with no blind retry.

Slack's signature/member/channel scope and exact invocation-bound approval remain mandatory; an
explicit current issue-creation request from an authorized member can supply that approval without
another confirmation. The public repository is fixed in the invocation. Private DM policy remains
read-only. Both Slack message and slash-command summaries project issue URLs
from matching successful receipt/output digests, independently of model-generated prose. No new
posting scheduler, GitHub shell authority or repository-wide token access is introduced.

### Slack progress and cancellation

Each mention/DM execution owns a durable `slack_progress` row binding the inbox message, Run and
Slack status-message timestamp. A worker-local status thread updates that message every five seconds
with the current execution stage/elapsed time; final outbox delivery updates the same timestamp and
removes buttons. Unknown initial sends are not repeated; final delivery may use a separate message
when no confirmed timestamp exists. Status threads join before final delivery to prevent late overwrites.

The signed form endpoint `/channels/slack/interactions` accepts only the stop action for the recorded
app/team/channel/message, from its author or a shared-channel approver. It persists cancellation
without the execution lock or an outbound Slack call, including during maintenance drain. It admits
no new work. Cancellation flags survive worker restart and affect only their original inbox job.

A thread-scoped control checks before/after reasoning and before tool dispatch. Official Codex
structured jobs terminate and reap their owned process group when cancelled; other processes and
services are untouched. The service appends an explicit STOP step after the execution yields.
Already-started external effects retain normal receipt/readback or awaiting-reconciliation handling,
then subsequent work stops. Canonical history and completed side-effect receipts are preserved.

### Codex image drafts in Slack

The installed composition registers approval-required `creative.image.generate` as a local-artifact
tool. Its dedicated ephemeral Codex turn uses the service user's official login, enables image
generation and disables shell, apps and browser tools. User/project configuration is ignored. The
visual brief cannot select paths or delivery destinations. The adapter reads the CLI JSON thread.started ID and selects the latest PNG from a bounded set of up to four variants in that runtime
thread’s generated_images directory, never from model-provided paths. It validates the bounded PNG
and persists a private copy by SHA-256, recording prompt/invocation provenance in the
canonical receipt/evidence stream. Cancellation uses the shared owned-process control; interrupted
admitted generation retains the runtime's uncertain-effect state without regeneration.

Mention-thread result delivery projects only matching successful receipt/evidence pairs. After
current member/channel authorization it reads the digest-bound file and shares a review draft in that
exact channel/thread through Slack's external upload protocol. `slack_image_deliveries` records
admission before upload, keyed by conversation/run/digest; unknown completion is never reposted.
Upload success or uncertainty is sent as a separate transport notice; the assessed answer and its
delivery identity retain their original bytes.
The bot credential goes only to fixed Slack API endpoints, never to the signed file upload URL.
Private conversations remain public-search-only. Slash/API callers can generate local artifacts,
but automatic image attachment is the mention-thread delivery surface.
### Combined work continuity and team knowledge

The service keeps two complementary context owners: scoped work memory for feedback/learning and
optional TEAM/SOUL/wiki knowledge for attributed cross-conversation facts. Both feed the same
canonical Run; neither can issue approval. Historical prepared knowledge stays in the ledger but
is excluded from generic conversation projection and is selected again under current authority.
Context selection uses the latest canonical follow-up together with the original goal.

Slack intake preserves its immutable source admission. Before executing a queued message, an
additive execution binding links its authenticated actor/message to the actual continuing Run;
queued messages received before the first Run exists cannot invent independent knowledge Runs.
Corrections/deletions fence that execution alias as well as the original source binding.

Image-edit queues recheck knowledge immediately before starting. A prestart
change settles without effect; an already-started result retains its actual receipt and cost.
A stale context discovered after runtime admission retains the pending invocation/reservation in
reconciliation, since no schema-safe prestart cancellation contract exists. It never silently
replans over the admitted invocation. Knowledge and image worker lifecycles coexist with the
managed server's persistent 8090 port and existing maintenance/shutdown boundaries.

Installed Slack Events admit signed mentions from all members in the configured app/team and any
internal channel where the bot receives mentions. User identity binding is created atomically on
first use without replacing existing grants or revocations. New bindings can create runs but cannot
approve effects. Shared threads remain channel/thread scoped; DMs remain member/session scoped.
Slack Connect events are excluded. Legacy channel/member lists still constrain slash commands, not
installed Events conversations.

Knowledge curation reads the latest admitted conversation event under the maintenance worker's
current read scope while preserving the original speaker in its evidence and authority references.
Reading another member's shared message does not turn the maintenance worker into that author;
question evidence-existence checks use the same scoped read path, while direct user-instruction
lookups still require the caller to be the speaker. Private event reads
remain member/session scoped, and curation rejects a source that differs from the canonical event.
Knowledge tools register separate schemas for their inner result and outer execution receipt,
so successful reads and structured errors both pass the canonical backend's receipt validation.

### Automatic conversation memory

Configured shared-conversation curation receives up to 12 current canonical user messages from
that same workspace, conversation and scope, plus bounded matching CORE reference entries.
Quoted/assistant/deleted/future or unreadable evidence is excluded. The latest correction can
therefore retain earlier conditions without inventing source links.

The curation provider can propose a typed `remember` intent containing only subject, summary and
canonical message evidence IDs. `CurationMemoryWriter` resolves the existing CORE document and
compiles a complete revision through the existing `memory_apply`/ChangePublisher path. It computes
IDs and digests, rechecks source capabilities/provenance, preserves unrelated entries and prior
retention/applicability, and uses the current document head as the CAS expectation. Older evidence
cannot replace newer memory; replay does not create duplicate revisions. Successful memory writes
admit the current source and feed existing index/view/consolidation workers.

These are ordinary subject-scoped reference facts, not global constraints or approvals. No manual
adoption is required for their automatic curation. Private conversations cannot use this writer to
promote data into shared CORE, and Wiki/constraint ownership is preserved. Explicit do-not-retain
instructions and source scope remain applicable. Incomplete or disputed input can still produce a
bounded clarification; only verified stored/selected memory establishes cross-conversation recall.

An authenticated explicit storage destination takes precedence over automatic CORE selection;
the corresponding Wiki or memory tools still enforce their normal authority and adoption rules.
Ordinary memory search excludes expired entries immediately; explicit historical search can retain
them. Public memory refresh schedules resolve their document targets from the persisted schedule
request, while internally generated refresh jobs retain revision-specific targets.

### Channel-owned knowledge

Shared TEAM/CORE/DAILY documents, SOUL brands, Wiki and source evidence retain an explicit channel
owner. A document cannot change owner in a revision, and lineage cannot widen channel evidence
into workspace scope or another channel. Retrieval, direct reads, corrections, prepared receipts
and transfer validation check the selected resource scope. Same-channel threads share knowledge;
DMs and other channels do not inherit it. Legacy workspace records remain under their original
scope and are excluded from Slack channel defaults. Workspace API/local admin authority remains
an explicit separate surface.

Channel ingestion jobs and curation batches persist the submitting actor separately from shared
channel identity. Background execution reloads current stored grants for that actor and session.
Consolidation and view jobs carry the same scope; generated views use
`teams/<workspace>/channels/<scope_key>/` to prevent filename collisions. A member leaving Slack
is not automatically detected by this local policy; signed ingress, stored member/session status
and current grants are the enforced authority sources.

The channel migration preserves historical scope keys, event/revision JSON and source bytes. It adds channel
identity, scopes canonical memory uniqueness and assigns existing brands to their legacy workspace.
Absent optional fields are omitted from serialization to retain existing hashes. Secondary
work-memory and observation keys also include channel identity for new channel records, with no
unscoped fallback. Workspace OAuth endpoints do not confer channel authority.

Brand and SOUL ownership follows the exact shared scope. Channel catalogs exclude other channels
and legacy workspace brands; the same brand names can exist independently in different channels.
The public knowledge CLI defaults to workspace administration. An operator-owned separate policy
file may specify `channel_id` to select exact channel authority; grants, admin sessions and new
brand/document IDs are scoped accordingly. Main service policy remains workspace-scoped. Brand
request bodies cannot override the policy scope.

### Persistent member preferences within a channel

`CHANNEL_MEMBER(workspace_id, channel_id, member_id)` owns USER memory independently of session.
The conversation actor remains CHANNEL: trusted Slack identity supplies the canonical member,
and ingress grants access to the common channel and only that member's personal scope. Background
jobs retain the submitting CHANNEL actor and reauthorize a personal job's exact target. Other
members, other channels and private DM sessions do not inherit these grants. Personal scope cannot
be supplied as a conversation actor or expanded into common scope.

Automatic curation classifies `memory_intent.destination` as `channel` or `user`. Common reference
facts use CORE; personal defaults use USER. USER publication accepts only direct reference facts
with the owner's canonical human message evidence. It cannot publish shared constraints or adopt
another speaker's preferences. Publication and direct catalog commits verify this boundary, retain
evidence and revisions, and atomically hide supporting messages from shared source search.
Source admission checks historical USER dependencies before replay so mixed common/personal
messages cannot be made globally searchable again; their common CORE facts can still be retrieved.

Context assembly selects the requester's USER entries with current channel knowledge, labels them
as defaults, and retains current-request, selected team-constraint and brand-voice precedence.
Personal reference blocks are considered before other optional memory references within the
existing budget. View and consolidation workers process only the channel and requester's own
personal documents and recheck current authority before writing USER.md. Paths encode member IDs
under the common channel's scope key; revision files retain the existing document/revision layout.
Retraction and purge remove a generated view only when its bytes match a redacted revision.
This removes deleted personal text without deleting another member's view or a newer clean view;
erase-aware restore cannot recreate the removed view from an old backup.

Published schema v3/v4 retain procedural skills and feedback learning. Channel ownership is v5 and
personal ownership is v6. Pre-merge channel-v3 and USER-v4 candidates are recognized by their exact
checksums and gain the missing skill/learning tables before converging on v6; their historical
schema rows are not rewritten. Unknown checksums fail closed. Scope keys, canonical JSON, original
files and existing memory ownership are preserved. No additional personal database or direct
Markdown ingestion path is introduced.

Workspace feedback-learning jobs retain their source-bound mutation and legacy-assessment path.
Channel conversation curation uses the scoped remember path; it does not acquire workspace grants
to publish global procedures. The remember shortcut is disabled for learning-purpose jobs.

## Package release publication

The GitHub Actions `Release on-prem agent` workflow runs after a successful main push verification
(or an explicit main recovery dispatch that rechecks verification). It binds checkout, tag and
artifacts to the exact verified SHA, and publishes only a new stable package version. Build and
fresh-wheel CLI checks precede draft upload and publication. Published versions are preserved;
concurrent publishers are serialized. GitHub write permission is confined to the release job.
It does not consume PR artifacts or server credentials. The on-prem updater above continues to
select verified main independently; release publication is not installed-server activation.

## Packaged Trace post production

`marketing.trace_post` is a discoverable installed procedure; `creative.trace_post` owns its
requested execution. The service freezes the exact invocation, request-bound grant and packaged
bundle in a private operation workspace before the deferred worker starts one Codex subprocess.
The worker instruction makes user delegation and optional human feedback explicit, overriding
conflicting review language in the frozen reference bundle without inventing human QA.
Its shell access is limited to the operation and required runtime files; external tools/network
are disabled. The model reads the frozen workflow, creates new content and executes A → B → L → C.
A started operation with an uncertain outcome is not automatically replayed after restart.

The server validates the frozen documents, content/image review bindings and six canonical outputs
before registering same-work, tenant-scoped assets. New results report `human_review_required=False`
and do not invent a human review record. Historical True values remain readable and do not gate
delivery. Completion adopts the Trace-post receipt only after its owner verifier rereads all six
current artifact records and bytes; those exact evidence and digest references then bind Slack
attachments. Completion resumes the existing work and queues an outbox message bound to that exact
Run, independently of transient progress UI. Receipt and asset-link validation resolve six images
from the configured artifact root; Slack uploads them to the original thread as named PNG files
with country captions. Current member permission is rechecked at delivery. Digest changes or absent
same-Run links prevent attachment; ambiguous uploads are not repeated after restart. Returning
results in the requesting thread is part of generation, while other destinations require a request.
Production credentials are not copied into the bundle.

The app-server transport copies PNG bytes from native image-generation events into the private
operation's `provider-images` directory and binds each file to its event ID and original SHA-256.
The frozen skill's helper guide names the outer-agent
`functions.exec`/`tools.image_gen__imagegen` binding, which does not exist inside this dedicated
app-server turn. The Trace-post provider therefore supplies an instruction-compatibility guard that
directs the model to send each prepared request to the turn's native image-generation tool instead
of looking for an outer orchestrator, plugin or MCP binding. This is a model instruction, not a
host-forced tool call; native generation events remain the only accepted execution evidence.
The child uses those files as receipt sources. Completion requires the official event count and
source path/hash set to match the frozen workflow's receipts. A durable provider proof permits
readback after a crash; a child-written completion summary alone cannot certify success.

The imported workflow originates at `corca-ai/trace-marketing-context` revision
`57779174c8be0dde741bab436fa21a61c2933f90`. This provenance is not a live repository dependency.
The executable product and its installed skill catalog are owned by `corca-ai/ads-booster`.

## First-use knowledge and concurrent Trace post production

A content request with no selected brand and no visible local brands receives `voice_unconfigured`:
use the user brief and selected procedure without inventing brand policy. Multiple visible brands
still need selection; an explicit inaccessible/missing/stale brand remains a preparation error.
A newly admitted input after a brand-selection/voice/scope wait is first prepared as team chat so
reasoning can classify the new request. Any resulting content action prepares its required context
again. Task transitions include the prior task identity, preventing reuse of a closed task.

An input-wait reply reads the current step's preparation or intent record, never an older reasoning
answer. Missing reasoning gets readable current-state text. An asynchronous callback whose result
matches the most recent delivered/skipped reply is durably marked skipped, preserving event-ID
idempotency without sending the same answer again.

Two maintenance-gated Trace post lanes own separate operation workspaces. The queue selection lock
protects an in-process active-operation set, so another lane cannot mistake live generation for a
crash-left operation. Per-Run locks cover canonical preflight/completion; the image provider runs
outside those locks. Started work left by a prior process retains uncertainty handling and is not
blindly retried. Maintenance counts each admitted lane and shutdown joins them before exit.

## Deferred media progress and notification recovery

`execution_control.progress_scope` gives a deferred worker a thread-local observed stage and a
five-second presentation heartbeat. Trace post and image-edit owners bind it to the tenant/Run;
Codex image started/completed notifications advance fixed stage labels without exposing prompts,
model reasoning, commands or paths. Elapsed time measures the worker scope, not output completion.
The Slack adapter updates the latest durable original progress-message identity for that Run,
rechecks current membership and canonical AWAITING_TOOL state under its Run lock, and never posts
a second progress message when the original Slack timestamp is unknown. Terminal state suppresses
late worker updates. The heartbeat stops and joins when the worker yields.

Completion/uncertainty notifications bind to the original progress message, including after restart.
Trace post adds the backward-compatible `trace_post_jobs.notified` column. A pending notification
is retried with its stable event ID after callback failure or restart; provider execution is never
repeated by notification recovery. Existing uncertainty becomes visible through the same mechanism.
Image-edit uncertainty is marked projected only after its notification callback succeeds.

The restricted Trace post subprocess invokes the resolved base Python executable already covered
by its runtime read grant. It does not attempt to execute the inaccessible installation-venv symlink
or broaden access to other installation files or credentials. The frozen helpers use the standard
library and the operation's packaged workspace.

Explicit Korean skill create/update directives may be followed by a sentence or colon with the
procedure details. Quoted instructions, negation and merely using a skill are not publication
requests. Existing workspace ownership, exact revision, source-currentness and private-chat read-only
boundaries remain unchanged.

## Linux launcher dependencies and uncertain-work dialogue

The shared image provider builds runtime read grants from the invoked and resolved Codex executable,
its recognized standalone/npm runtime directories, Node where needed, and `CODEX_HOME/tmp/arg0`
(the official dynamically generated launcher directory). It never grants the whole Codex home.
The existing workspace, minimal system and Python runtime grants remain the execution boundary.
A completed shell item containing a missing bwrap launcher is classified at the transport boundary
as `codex_sandbox_launcher_unavailable`; the provider stops rather than looping on identical calls.

Trace post and image-edit stores add nullable `failure_code` columns. Workers persist allowlisted
codes before projecting uncertainty. The service appends one `trace.deferred-provider-failure.v1`
evidence record per operation; raw stdout/stderr, paths and exception payloads are excluded.
Canonical uncertainty, receipts and no-replay semantics remain intact. Slack renders fixed reason
text from this evidence after restart.

Trace post additionally stores a nullable, strict `failure_diagnostic` JSON record before marking a
started provider call uncertain. It contains only allowlisted event counts, command exit-code
classes, agent-message count/length bucket, terminal
turn/item counts, and fixed workspace milestone statuses. The atomic workspace copy is mode 0600;
both representations are capped at 4 KiB and exclude IDs, prompts, commands, output, paths and
generated bytes. Diagnostic collection is best effort and can never replace the provider outcome or
trigger a retry. Terminal turn items are observed as counts only and do not establish completion.

The existing response-only waiting dialogue receives the same allowlisted failure code and fixed
reason from canonical evidence. It keeps the original Run and notification binding unchanged and
has no tool budget. The channel does not create a competing inspection Run or replace operation
ownership. Ordinary follow-ups retain that response-only contract; explicit independent work uses
the retained-original-Run binding described above.

The Ubuntu installer installs the system `bubblewrap` package because the pinned single-binary Codex download has no bundled bwrap resource. Installer preflight includes `bwrap`. The installed Linux gate exercises this dependency without credentials.

### Completion checks for generated images and remembered facts

An ordinary media-generation request is deliverable after canonical artifact-owner checks.
Appearance words in a brief do not impose a separate visual-review or human-approval stage.
Explicit visual inspection or a claim that inspection occurred still needs review evidence.
An authenticated scoped memory read can confirm facts already stored; it cannot prove a new
write. Completion assessment must not demand a redundant write to confirm existing memory.
Worker admission replaces the previous approval wait reason with `awaiting_tool`; resumed active
planning clears stale wait reasons. Slack projects this state as execution in progress.

For `run_tool_input` observation tools, each newly admitted read includes the canonical Run
revision in its idempotency identity. An identical refresh is a new observation, not a duplicate
effect. Recovery reuses the persisted invocation unchanged. External/artifact writes and explicit
`tenant_tool_input` identities retain their existing deduplication semantics and budget guards.
