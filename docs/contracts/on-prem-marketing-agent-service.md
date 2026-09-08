# On-premises Marketing Agent Service

Status: Draft — target architecture and remaining migration gates. The portable service and Slack
integration are implemented in source; full hosted ownership cutover is not complete.

Last reviewed: 2026-09-08

The current source boundaries below do not establish fresh-install or live channel acceptance.
Automated Mac enrollment/lifecycle management, Cloudflare projection-only cutover, KakaoTalk
delivery, and removal of hosted canonical campaign ownership remain unimplemented. Current runtime
details belong in [System Architecture](../architecture/system.md); this contract defines the target
constraints and the evidence required to complete that migration.

## Target product invariant

One always-on, on-premises Marketing Agent Service owns every canonical Agent Run. It observes
evidence, plans, requests approval, invokes connected tools, verifies receipts, evaluates outcomes,
and replans. Codex, Cloudflare, Mac/Appium, Threads, image or video generation, web research, Web,
Slack, and KakaoTalk are replaceable providers or adapters. None is a second agent.

The service must still create, reason about, persist, and resume a run when no Appium worker is
installed or ready. A Mac worker may execute only an invocation admitted and persisted by this
service. Codex CLI is one `ReasoningProvider`; it is not the process owner or durable memory.

## Current source and target states

| State | Canonical owner | Cloudflare/D1 | Mac/Appium | User surface |
| --- | --- | --- | --- | --- |
| Current hosted compatibility path | Cloudflare/D1 owns hosted runs, campaign facts, and publication effects | hosted workflow and campaign ledger | separately enrolled worker for hosted tasks | Cloudflare workspace |
| Implemented on-premises path | MarketingAgentService owns portable runs in an append-only SQLite repository; configured adapters reach local research, hosted workflow, Slack, and Notion | remote hosted workflow/catalog backend; retains its existing effect ownership | hosted workflow, opt-in local capture, or explicitly configured remote capture; automated enrollment/lifecycle is pending | Run API and browser view when enabled; signed Slack Commands/Events and original-conversation replies |
| Remaining target | on-prem service is the only canonical run and decision owner | optional remote adapters and projections only | one of many replaceable effect workers admitted by the on-premises service | Web, Slack, and KakaoTalk share the service and its run identity |

During transition, existing D1 records remain authoritative for the effects and campaign facts they
already own. They are imported as receipts or observations into the on-prem run; they cannot advance
the on-premises run by themselves. No migration may silently reinterpret an existing external effect.

The implemented service is in `marketing/agent_service/application.py`, with configured tools in
`integrations.py` and channel wiring in `channel_setup.py`. Slack has real form and Events ingress
in `marketing/channels/slack_commands.py` and `slack_events.py`; it is not limited to fake adapters.
KakaoTalk has a provisioned ingress contract, but no configured delivery path. Package ownership is
defined in [Code Architecture](../architecture/code.md).

## Portable domain

The portable domain lives in Python without imports from Cloudflare, Appium, Threads, Codex, HTTP,
Slack, KakaoTalk, or UI packages. Its public records are versioned, immutable, tenant-scoped, and
canonical-JSON digestible.

| Contract | Responsibility |
| --- | --- |
| `AgentRun` | goal, tenant, state, budget, current phase, revision, and terminal or blocked reason |
| `AgentStep` | one ordered observe/plan/approve/execute/verify/evaluate/replan decision boundary |
| `Intent` | host-admitted next action with evidence, budget, and expected-result bindings |
| `CapabilitySnapshot` | immutable view of tools that were ready and policy-eligible for one plan |
| `ToolInvocation` | descriptor-bound, non-secret input plus idempotency key |
| `Approval` | actor-bound, expiring, revocable grant for one exact invocation |
| `Receipt` | immutable execution disposition, cost, provenance, and reconciliation data |
| `Outcome` | measured result with source, window, uncertainty, and causal classification |
| `Learning` | reviewed conclusion with applicability and counter-evidence; never raw model memory |

These records are defined in `contracts/agent_run.py`; the service composes the existing runtime's
approval, receipt, and reconciliation guarantees. Compatibility serializers may project records to
existing Cloudflare schemas; the portable domain must not depend on those schemas.

## Unified tool contract

Every selectable tool is registered once as a `ToolDescriptor`. A descriptor contains:

- stable capability and version identity plus owning adapter;
- canonical input, output, and configuration JSON Schemas and their digests;
- effect class (`observe`, `local_artifact`, `control_plane_write`, or `external`);
- approval policy and authority scope;
- worst-case cost and optional metering unit;
- readiness with observed time and bounded reason code;
- idempotency policy and key scope;
- reconciliation policy, lookup capability, and terminal dispositions;
- secret-resolution boundary and receipt schema.

The registry separates definition, installation/configuration, and live readiness. The planner sees
only installed, enabled, ready, policy-eligible descriptors whose cost fits the remaining budget.
An unavailable Appium tool therefore disappears from planner input while research or strategy tools
remain selectable. In the target, worker heartbeat, research, creative, and hosted capability maps
are projections of this registry. The current hosted workflow retains its capability maps behind
the compatibility adapter until cutover.

## Canonical run loop

```text
observe -> plan -> approve? -> execute -> verify -> evaluate -> replan
   ^                                                        |
   +--------------------------------------------------------+
```

1. `observe` freezes evidence, current outcomes, budget, and the eligible capability snapshot.
2. `plan` asks a replaceable `ReasoningProvider` for structured intents; the service validates and
   persists the chosen intent. A provider cannot dispatch a tool or mutate run state.
3. `approve` stops only when the descriptor policy requires an exact grant.
4. `execute` persists admission and execution-start before handing the invocation to its adapter.
5. `verify` accepts only a descriptor-bound receipt. An ambiguous external effect is never retried;
   its reconciliation policy is used instead.
6. `evaluate` records what is known, unknown, or merely correlated.
7. `replan` takes the new evidence, receipt, outcome, and remaining budget. It may choose a different
   ready tool, ask for input, stop, or schedule the next experiment.

The append-only event ledger is authoritative. Materialized run views are rebuildable projections.
Restart replays the ledger; an invocation with an execution-start but no terminal receipt enters
reconciliation and is not executed again.

## Adapter boundaries

- `ReasoningProvider`: structured plan/evaluation requests and receipts. Codex CLI is the first
  implementation and uses the service user's official login session.
- `ToolAdapter`: validates and executes one descriptor-bound invocation. Existing Appium capture,
  candidate generation, Threads publish/readback, research, and creative code remain implementation
  owners behind adapters.
- `RemoteToolAdapter`: reaches Cloudflare or another remote effect owner and reconciles by readback.
- `ChannelAdapter`: translates identity-bound user commands, approvals, and notifications to the
  same Agent API. It never owns a run or creates channel-specific planning logic.
- `ProjectionAdapter`: the remaining cutover must publish safe, account-scoped run views to
  Cloudflare/UI without making the projection an authority.

## Agent API and channel contract

The service API supports creating/listing/reading/resuming runs, submitting input, exact approval,
and inspecting run records. Browser jobs and Slack channels invoke the same service; progress is
read through job/run status or delivered as channel replies. Channel bindings map
an external workspace/user/conversation to an internal tenant/member and record the adapter instance.
Inbound webhook event IDs and outbound notification intents are idempotent.

Slack Commands and Events verify signatures and configured identities, admit work durably, and send
responses through the configured Slack API transport. Approvals require an explicit reviewer action
bound to the exact invocation; free text does not grant approval. `TRACE_MARKETING_SLACK_ONLY=1`
disables the browser, OAuth routes, and bearer API access, and suppresses public Run links.

The remaining KakaoTalk target must cover install/connect, identity binding, run request, exact
approval, progress notification, and result access through the same service. Its provisioned ingress
contract redirects approval to Web re-authentication; this is not live KakaoTalk delivery. Contract
tests use fake transports and signed fixtures. Live verification requires real credentials, public
callback endpoints, platform configuration or review, and an explicitly authorized test
workspace/channel. Follow the [server launch guide](../operations/agent-server/slack-launch-guide.md)
for installation and acceptance; passing fake tests must never be described as live platform support.

## Product UI contract

The target primary page is an Agent Run, not an Appium task. One run shows goal, evidence and research,
strategy and alternatives, artifacts, pending approvals, tool executions and receipts, outcomes,
learnings, next experiment, and a bounded blocked reason. Appium details appear only inside the
relevant tool step. The implemented browser view uses `/runs/<run-id>` when Web is enabled. Slack-only
operation presents Run status and approval details inside Slack; it does not expose that Web URL.
Cross-channel result access remains subject to each deployment's authentication and channel support.

## Compatibility and migration

1. Implemented in source: portable contracts, an on-prem store, and an Appium-independent run loop.
2. Implemented in source: configured research/hosted handoff adapters and browser/Slack admission
   into the same service.
3. Remaining: automate Mac enrollment and lifecycle while preserving independent reasoning and
   Appium readiness. Explicitly configured local and remote capture adapters already exist.
4. Remaining: project on-prem runs to Cloudflare and switch hosted ingress to the on-prem API.
5. Remaining: migrate or link existing hosted run lineage explicitly; retain D1 data as remote receipts and
   projections. Remove hosted canonical ownership only after parity and recovery tests pass.

No compatibility step may bypass current approval, receipt, artifact validation, publish-once,
readback, or human-review gates. Mac workers do not publish. The existing hosted Threads path is
disabled by default and may publish only after profile connection, operator activation, and human
image approval, as defined in
[System Architecture](../architecture/system.md#threads-publication-and-observation).

## Migration acceptance gates

These are required proofs of the completed target, not a report of checks passed by this document.

- A fresh-installed on-prem service creates, reasons about, and resumes a run with Appium absent.
- A separate Mac worker receives only a persisted, approved invocation and reasoning readiness is
  independent from Appium readiness and lifecycle.
- Disabling or losing readiness for one tool changes the next capability snapshot and permits a
  different executable plan.
- Restart after execution-start does not duplicate an external effect and follows reconciliation.
- Web traces one run from goal through outcome and next action.
- Slack Commands/Events and KakaoTalk contract fixtures round-trip the same service and exact
  approval boundary. Web result links are checked only in Web-enabled mode; Slack-only status and
  approval are checked inside Slack. Live delivery requires a separately recorded platform canary.
- Focused Appium, candidate, Threads, capture, reasoning, resume, delegation, migration, and channel
  tests pass after integrating current `main`.
- A fresh installed service and separately installed Mac worker complete the documented user path.

## Deliberately not claimed in this transition contract

- Live Slack or KakaoTalk installation without credentials and platform configuration.
- Autonomous ad spend or new publishing authority from the on-premises migration. Existing hosted
  Threads publication remains governed by its independent approval and activation gates.
- Distributed active-active run ownership; the first service is a durable single canonical writer.
- Causal marketing lift from descriptive channel metrics.
- Completion merely because the old Cloudflare workspace can display hosted tasks.

## September 7 small-work extension

The product may enter at any useful point in the responsibility graph. Existing images,
questions, Figma output and human captures are valid initial inputs; campaign identity is
optional. A task records original/derived assets, requested preservation/change, locale,
human reports and independent verification. Production, final publication, post-publication
changes, community actions, Paid budget/execute and format promotion/deactivation remain
different review targets. Explicit preparation approval never enables external execution.

Canonical Run history, runtime invocation receipts and scoped shared knowledge have separate
owners. Private requests cannot promote or mutate shared context. Memory corrections/expiry
remove notes from current selection while original audit history remains. Tombstone deletion
is retrieval deletion, not physical erasure of historical evidence.

Implemented candidate surfaces and evidence are tracked in the existing product/runtime
plans and testing document. Live editing/capture, external publishing/readback/metrics,
community actions, Paid execution and model-quality generalization remain unverified or Draft;
prepared packets and fake adapters must not be described as those integrations completing.

Performance reports enter as attributed human observations scoped to the current work, retaining
source, account/country and observation window. Corrections preserve originals and invalidate
derived memory before approval or retrieval. Shared Web readback cannot promote private reports.
Optional image editing uses the existing exact production approval and asynchronous completion
boundary; it checks unchanged pixels and records promotional/background provenance with pending
visual/human review. It does not grant final publication authority or verify native product support.

Slack asset intake is an optional small-work tool pair: inspect authenticated file bytes, then
import the exact digest with human-confirmed source/use terms under runtime approval. It does
not grant downstream production/publication approval. Register before linking for Web readback;
failed registration cannot attach an existing different asset to that Run. Returned image bytes
are bounded separately from inline upload size; provenance and reported status remain explicit.

An asynchronous tool acknowledgement binds the original invocation and executor to one operation.
It does not claim success, verified artifacts or final cost. The canonical owner retains the
pending work and resolves only the matching terminal result. Restart and duplicate completion
must not redispatch work or charge twice. The opt-in remote transport authenticates the configured
worker and verifies native artifacts before invoking the internal completion boundary.

Remote capture uses a complete profile/job digest in addition to the native visual request digest.
The profile pins tenant, worker, simulator and execution paths. Queue admission records the exact
production approval, synthetic schedule and background revision. Worker-token authority is scoped
to profile/heartbeat/claim/source/start/status/complete/uncertain routes, with a 16 KiB control
request bound and a 14 MiB authenticated completion wire bound (decoded image at most10 MiB).
User API limits remain separate. Started work cannot be reassigned. Receipt projection may be
repaired from durable completion; missing execution evidence is not permission to repeat a job.
