# On-premises Marketing Agent Service

Status: Transition contract — the portable domain and service are being implemented in PR #99.
The current Cloudflare-hosted run remains operational during migration, but is not the target owner.

Last reviewed: 2026-09-03

## Product invariant

One always-on, on-premises Marketing Agent Service owns every canonical Agent Run. It observes
evidence, plans, requests approval, invokes connected tools, verifies receipts, evaluates outcomes,
and replans. Codex, Cloudflare, Mac/Appium, Threads, image or video generation, web research, Web,
Slack, and KakaoTalk are replaceable providers or adapters. None is a second agent.

The service must still create, reason about, persist, and resume a run when no Appium worker is
installed or ready. A Mac worker may execute only an invocation admitted and persisted by this
service. Codex CLI is one `ReasoningProvider`; it is not the process owner or durable memory.

## Current, transition, and target states

| State | Canonical owner | Cloudflare/D1 | Mac/Appium | User surface |
| --- | --- | --- | --- | --- |
| Current | Cloudflare hosted run plus narrow local JSON sessions | hosted workflow and campaign ledger | mixed reasoning and tool worker capabilities | Cloudflare workspace |
| Transition in PR #99 | on-prem service owns new portable runs; existing hosted automation is reached through compatibility adapters | ingress, webhook, projection, and remote tool backend | independent tool worker; existing capture implementation unchanged | run API and run-oriented web projection, with fake channel adapters |
| Target | on-prem service is the only canonical run and decision owner | optional remote adapters and projections only | one of many replaceable effect workers | Web, Slack, and KakaoTalk use the same Agent API |

During transition, existing D1 records remain authoritative for the effects and campaign facts they
already own. They are imported as receipts or observations into the on-prem run; they cannot advance
the canonical run by themselves. No migration may silently reinterpret an existing external effect.

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

The existing local capability snapshot, receipt ledger, intent/resume, delegation outbox, approval,
and reconciliation behavior are promoted into these records. Compatibility serializers may project
them to existing Cloudflare schemas; the portable domain must not depend on those schemas.

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
remain selectable. Worker heartbeat, research, creative, and hosted capability maps are projections
of this registry, not independent registries.

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
- `ProjectionAdapter`: publishes safe, account-scoped run views to Cloudflare/UI without becoming
  an authority.

## Agent API and channel contract

All clients use the same versioned API to create/list/read/resume runs, submit input, grant or revoke
approval, inspect steps/artifacts/outcomes/learnings, and subscribe to progress. Channel bindings map
an external workspace/user/conversation to an internal tenant/member and record the adapter instance.
Inbound webhook event IDs and outbound notification intents are idempotent.

Web is the reference channel. Slack and KakaoTalk adapters must cover install/connect, identity
binding, run request, exact approval, progress notification, and result link. Contract tests use fake
adapters and signed fake webhook requests. Live verification additionally requires real credentials,
public callback endpoints, platform configuration or review, and an explicitly authorized test
workspace/channel. Passing fake tests must never be described as live platform support.

## Product UI contract

The primary page is an Agent Run, not an Appium task. One run shows goal, evidence and research,
strategy and alternatives, artifacts, pending approvals, tool executions and receipts, outcomes,
learnings, next experiment, and a bounded blocked reason. Appium details appear only inside the
relevant tool step. The same run URL is returned to Web, Slack, and KakaoTalk.

## Compatibility and migration

1. Add portable contracts and an on-prem store without changing existing effect implementations.
2. Make the on-prem service create and resume an observe-only run without Appium.
3. Wrap existing local research and hosted campaign handoff as registered adapters.
4. Split reasoning readiness from Mac/Appium readiness and enroll the Mac against the on-prem API.
5. Project on-prem runs to Cloudflare and make new web/channel ingress call the on-prem API.
6. Migrate or link existing hosted run lineage explicitly; retain D1 data as remote receipts and
   projections. Remove hosted canonical ownership only after parity and recovery tests pass.

No compatibility step may bypass current approval, receipt, artifact validation, publish-once,
readback, or human-review gates. Existing automatic publishing remains off.

## Acceptance evidence

- A fresh-installed on-prem service creates, reasons about, and resumes a run with Appium absent.
- A separate Mac worker receives only a persisted, approved invocation and reasoning readiness is
  independent from Appium readiness and lifecycle.
- Disabling or losing readiness for one tool changes the next capability snapshot and permits a
  different executable plan.
- Restart after execution-start does not duplicate an external effect and follows reconciliation.
- Web traces one run from goal through outcome and next action.
- Fake Slack and KakaoTalk adapters round-trip the same run and exact approval; live status remains
  explicitly unverified until external prerequisites exist.
- Focused Appium, candidate, Threads, capture, reasoning, resume, delegation, migration, and channel
  tests pass after integrating current `main`.
- A fresh installed service and separately installed Mac worker complete the documented user path.

## Deliberately not claimed in this transition contract

- Live Slack or KakaoTalk installation without credentials and platform configuration.
- Autonomous ad spend or automatic external publishing.
- Distributed active-active run ownership; the first service is a durable single canonical writer.
- Causal marketing lift from descriptive channel metrics.
- Completion merely because the old Cloudflare workspace can display hosted tasks.
