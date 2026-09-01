# Marketing Agent Runtime

Status: Draft — post-merge implementation contract. This is the high-level agent over the existing
Trace control plane; it does not replace or directly reorganize Cloudflare automation in this PR.

## Problem

Small software teams need more than a content generator. They need an operator that can turn a
product change, customer evidence, and market observation into the next best growth action, explain
why, safely use connected tools, and learn only from measured outcomes. Existing Trace automation
provides useful effects but cannot itself decide which research, experiment, or medium is next.

## Capability contract

For a bounded `MarketingGoal`, the runtime creates a durable session; chooses and executes only
permitted skills/tools; records observations and decisions; pauses on authority boundaries; and
finishes with either a verified outcome, an explicit inconclusive result, or a typed stop reason.

The runtime's public value is not a chat transcript. It is a reviewable decision trace and an
evidence-backed experiment queue that improves across campaigns.

```text
Session: append-only events, checkpoint/replay, durable outside model context
Harness: provider/model selection, context projection, loop, budget, tool routing, stop policy
Hands: typed information/effect tools with authority and immutable receipts
Evaluator: grades final environment state and decision trace; promotes a playbook candidate only
           after evidence and human approval
```

This follows Anthropic's brain/hands separation: the harness must not know whether a hand is a local
research worker, Cloudflare, Appium, an analytics system, or a future CRM. It invokes a narrow tool
contract and re-plans from the receipt. [Scaling Managed Agents](https://www.anthropic.com/engineering/managed-agents)

## Current slice

Implement a provider-neutral, no-external-effect runtime package with an in-memory fake tool backend.
It is deliberately usable before Cloudflare is exposed as a tool.

### Entities

| Entity | Required fields | Owner |
| --- | --- | --- |
| `MarketingGoal` | id, outcome, product_context_ref, autonomy_level, budget, stop_policy | caller/harness |
| `AgentSession` | id, goal digest, append-only events, derived checkpoint, terminal state, pinned policy/skill snapshot | session store |
| `SessionEvent` | id, sequence, type, actor, causation/correlation IDs, schema version, payload digest, timestamp | session store |
| `Decision` | observation refs, alternatives, selected skill, expected evidence, cost estimate | harness |
| `ApprovalGrant` | pending call digest, authority scope, approver, expiry, revocation/policy version, use count | policy/human |
| `Skill` | version/digest, entry/exit criteria, tool allowlist, procedure, stop rules | skill registry |
| `ToolCall` / `ToolReceipt` | call/idempotency IDs, capability descriptor digest, scoped input/schema digest, grant/policy refs, effect disposition, actual usage/cost | hand/backend |
| `Observation` | source/receipt ref, digest, product scope, freshness, uncertainty, trust and retention labels | tool/backend |
| `Evaluation` | outcome state, trace assessment, grader evidence, score dimensions | evaluator |
| `PlaybookCandidate` | scoped learning, evidence coverage, counter-evidence, approval state | evaluator/human |

### State and authority

```text
created -> orienting -> planning -> executing <-> awaiting_tool -> evaluating
                       |                         |
                       +-> awaiting_human -------+
any nonterminal -> awaiting_reconciliation | stopped | failed | inconclusive | completed
```

- The harness can plan and call `observe` tools at `observe` autonomy.
- Reversible draft/artifact tools require `execute_reversible`.
- An effect tool returns `approval_required` rather than executing when its grant is absent; the
  runtime checkpoints and enters `awaiting_human`.
- A receipt with `unknown_side_effect` ends the current tool attempt; no autonomous retry is allowed.
- An irreversible unknown effect enters `awaiting_reconciliation`; only an owner/human reconciliation
  event can resolve it. The runtime cannot substitute another effect or claim completion.
- Each session has max iterations, wall-clock deadline, tool-cost budget, and per-tool call limit.
- Before dispatch, the harness reserves the worst-case iteration/time/cost bound. A receipt records
  actual usage; unknown cost consumes the conservative bound. Retry/backoff is allowed only after a
  confirmed no-effect disposition.

## Fixed decisions

- Use a small composable harness, not a framework-shaped rewrite. The model/provider interface is a
  dependency, so a deterministic scripted planner can drive the first tests.
- Session history is canonical and append-only. Context compaction is only a harness projection;
  it cannot delete the event history.
- A checkpoint is derived state, not authority. Replay replays committed `Decision` and dispatch
  events exactly; it never asks a nondeterministic model to make the “same” decision again. The
  harness invokes a planner only when no decision was committed, and persists the provider/model,
  prompt, context-projection, tool-schema, and skill-registry digests for every invocation.
- The store protocol is durable and compare-and-swap single-writer. The first implementation includes
  a serializable local test store; an in-memory store is a unit double only, not restart proof.
- Agent working context is session-scoped; product truth, customer intelligence, market evidence,
  and approved playbook knowledge remain distinct stores.
- Skills document when/how to use tools but never grant authority; tool policy and grants do.
- The planner returns a strict `DecisionProposal`, never an executable tool call. The harness projects
  an allowlisted `AvailableAction` set and accepts only an action in the intersection of pinned skill
  allowlist, session autonomy, capability descriptor, and current approval grant.
- Observations and tool outputs are untrusted data. Context projection attributes and quotes them,
  and embedded instructions cannot mutate policy, authority, skills, or tool definitions.
- Tool receipts, not model text, are ground truth for completion.
- Parallel workers may only receive immutable task packets and return observations; the orchestrator
  alone mutates session state and selects the next action.
- The evaluator grades both outcome state and process constraints: correct tool selection, evidence
  lineage, budget, stop policy, and approval behavior.

## Probe questions

- Which first three skills give the greatest internal Trace leverage: feature launch research,
  customer-intelligence synthesis, or creative experiment design? Answer with a scored internal
  campaign, not preference.
- Which model/provider configuration satisfies cost and tool-use quality for each skill? Keep this
  behind the provider interface and use eval traces for the decision.
- What product event coverage is sufficient for a customer-visible outcome claim? Answer before
  exposing autonomous outcome optimization.

## Deferred decisions

- Cross-tenant identity, billing, public onboarding, and final web UI.
- Long-running remote execution service, streaming transport, and multi-agent fleet scheduling.
- Automatic publication, spend, customer contact, and generic CRM mutation.
- Cloudflare capability bindings and operation diagnostics; add them only after this runtime can
  exercise a fake control-plane tool contract.

## Non-goals

- A general personal assistant, browser shell, or arbitrary-code execution agent.
- Treating arbitrary memory writes or raw customer transcripts as durable playbook knowledge.
- Claiming a growth result from a generation benchmark or an LLM judge alone.
- Replacing existing Trace Appium/Threads owners.

## Success criteria and acceptance checks

| Criterion | Verification type | Acceptance check |
| --- | --- | --- |
| Durable agent loop | unit | a reopened serializable store replays committed decisions/dispatches exactly; planner invocation is allowed only when no decision event was committed |
| Tool authority | unit | an ungranted external capability creates a pending/denied call but no effect receipt; an exact, unrevoked grant resumes one idempotent call |
| Receipt-grounded planning | unit | a failed receipt changes the next state to typed failure/stop; an unknown external-effect receipt enters `awaiting_reconciliation`, never fabricated success or automatic substitution |
| Bounded autonomy | unit | iteration, time, and worst-case cost reservation stop the run with a visible reason before another tool call; receipt actual usage is reconciled |
| Skill quality is measurable | eval | each shipped skill has valid, insufficient-evidence, denied-authority, and tool-failure cases; held-out fixtures are outside agent-visible skill definitions and outcome/trace graders are recorded separately |
| Worker isolation | unit | a delegated worker cannot mutate session/playbook state and its observation is digest-bound to its task packet |
| Control-plane portability | integration | the same harness scenario—including duplicate dispatch, revoked grant, unknown effect, and receipt tamper—passes against the fake backend and a Cloudflare contract double without importing Cloudflare runtime code |
| Marketing differentiation | eval | a held-out feature/call/market packet yields a traceable experiment proposal with claim containment, explicit counter-evidence, and a measurable outcome—not just a content draft |

## Evaluation design

The eval harness uses isolated fixtures, deterministic fake tools, full event traces, outcome graders,
and process graders. A run passes only if its environment state and trace both pass; a fluent final
answer is not evidence. This is consistent with Anthropic's distinction between an agent harness,
evaluation harness, trace, and final environment outcome. [Demystifying Evals for AI Agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

The first scorecard tracks: grounded-claim rate, invalid-tool-call rate, unapproved-effect attempts,
time/cost per useful experiment, evidence coverage, evaluator agreement, and downstream experiment
completion/outcome coverage. Revenue and conversion are delayed business measures, not early agent
quality proxies.

## Boundary ownership

| Boundary | Owner now | Runtime relationship after merge |
| --- | --- | --- |
| Product evidence / campaign ledger | Cloudflare D1 | typed observe/write tool backend |
| Appium native capture | Mac worker / Trace | approved local-artifact hand |
| Threads publish/metrics | `threads/*` | approved external-effect hand |
| Model loop / skill routing | new runtime package | harness |
| Session / context projection | new runtime package | canonical session + projection |
| Outcome/process grading | new runtime package | evaluator; later consumes D1 observations |

## First implementation slice

Create `ads_booster.marketing.runtime` with domain contracts, in-memory session store, scripted
planner protocol, skill registry, fake tool backend, and focused unit/eval fixtures. It must have no
Cloudflare import and no real external side effect. After the acceptance checks pass, perform a fresh
design critique, then add a thin control-plane adapter in a new post-merge PR.
