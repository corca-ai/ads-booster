# On-premises Marketing Agent Service

Status: Active service contract; unimplemented channel expansion is explicitly identified below.
Last reviewed: 2026-09-08

## Product invariant

One always-on `MarketingAgentService` owns canonical Agent Runs, exact approvals, durable receipts,
and the observe/plan/approve/execute/verify/evaluate/replan loop. Providers and channel adapters do
not create a competing Run ledger. Codex uses the service user's official login; systemd owns the
Linux process lifecycle. Cloudflare Tunnel is optional ingress.

The former hosted Workers/D1/R2 campaigns, Mac/Appium workers and Threads automation have been
removed. This change is source retirement, not an external-resource deletion or data migration.
Existing remote state must not be reported erased merely because its adapter is gone.

Current implementation owners are `marketing/agent_service/application.py`, `integrations.py`,
`lifecycle.py` and `channel_setup.py`. See [System Architecture](../architecture/system.md) and
[Code Architecture](../architecture/code.md) for current flow and file ownership.

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
approval, receipt, and reconciliation guarantees. The portable domain must not depend on external storage or channel schemas.

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
An unavailable tool disappears from planner input while independent ready tools remain selectable.

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

- `ReasoningProvider` returns structured decisions and provider receipts without dispatch authority.
- `ToolAdapter` validates and executes one descriptor-bound invocation. Research, creative images,
  Slack, Notion and GitHub have separate configured owners.
- `ChannelAdapter` translates authenticated commands, approvals and notifications into the same API.
- Browser and Slack projections read canonical state; they do not maintain independent truth.

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

The primary page is an Agent Run. One run shows goal, evidence and research,
strategy and alternatives, artifacts, pending approvals, tool executions and receipts, outcomes,
learnings, next experiment, and a bounded blocked reason. Tool details appear inside the relevant step. The implemented browser view uses `/runs/<run-id>` when Web is enabled. Slack-only
operation presents Run status and approval details inside Slack; it does not expose that Web URL.
Cross-channel result access remains subject to each deployment's authentication and channel support.

## Acceptance boundaries

- A fresh installed service creates, persists, reasons about and resumes a Run.
- Missing tool readiness changes the next capability snapshot without preventing other work.
- Restart after execution-start preserves uncertainty and does not duplicate an external effect.
- Web and signed Slack use the same Run and exact approval boundary under current identity grants.
- Source tests, isolated installed HTTP/SQLite proof and live platform delivery are separate results.
- The server's real systemd lifecycle, public installer and automatic main update require their own
  acceptance. Follow the [server launch guide](../operations/agent-server/slack-launch-guide.md).

Distributed active-active ownership, live KakaoTalk delivery, automated external marketing metrics
and causal lift are not supplied by this service contract.

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
plans and testing document. Live editing, external publishing/readback/metrics,
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
must not redispatch work or charge twice.
