# Marketing colleague: discover procedures, act, inspect, continue

Status: Candidate implementation
Date: 2026-09-08
Owning issue: [#93](https://github.com/corca-ai/ads-booster/issues/93)

## Findings and scope

The current service already owns a durable Run ledger, exact tool approvals, scoped knowledge,
creative adapters and continuation. Replacing that kernel with another framework would introduce
new ownership and migration risks without fixing the observed wiring gaps.

Three gaps are addressed here:

1. The versioned skill catalog was exposed through HTTP, but ordinary reasoning had no tool to
   discover/read it. The initial prompt referred to skills without an actionable discovery path.
2. Creative skill v1 ended with an unconditional human handoff even though `creative.prepare`
   can return an automatic route with an available execution capability. The generic prompt also
   foregrounded preparation and missing inputs instead of completing the requested work.
3. Slack computed a transcript while choosing a message action, but same-Run revision admission
   passed only the new message and attachments. Generic Run evidence excludes assistant intents,
   so the planner could lose its own earlier alternatives. The signed Slack regression returned
   two alternatives, restarted the service, and requested the second: before the fix the previous
   alternative was absent from the actual provider request; after the fix it was present.

These are source/fixture findings. This Mac's previously installed CLI exposes only the older
simulate/bridge commands and cannot establish the current Slack server's deployed behavior.
No production incident frequency or measured marketing uplift is inferred from these findings.

## Reference review

These are architectural references, not performance benchmarks or dependencies added to Trace.
All were inspected on 2026-09-08; upstream `main` links can change.

| Primary source | Useful mechanism | Decision for Trace |
| --- | --- | --- |
| [Deep Agents skill middleware](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/skills.py) | Discovery metadata and on-demand procedure loading | Add compact list and exact-version read tools; keep procedure bodies out of the initial prompt. |
| [Deep Agents construction](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/graph.py) | Separate planning/context/tool/approval middleware | Keep our canonical execution owner; improve its procedure and conversation inputs. Do not import another runtime. |
| [Anthropic research-agent demo](https://github.com/anthropics/claude-agent-sdk-demos/blob/main/research-agent/research_agent/agent.py) | Specialist descriptions, bounded tool inventories, research-to-analysis-to-report handoffs | Express specialist work as discoverable procedures. Do not copy the demo's permission bypass or add unmeasured parallel model workers. |
| [Agency Agents content creator](https://github.com/msitarzewski/agency-agents/blob/main/marketing/marketing-content-creator.md) and [growth specialist](https://github.com/msitarzewski/agency-agents/blob/main/marketing/marketing-growth-hacker.md) | Channel-specific deliverables and experiment responsibilities | Add copy/strategy/experiment procedures with concrete outputs. Reject the listed universal success percentages as unsupported for Trace. |
| [OpenHands event store](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/conversation/event_store.py) | Persistent, addressable conversation events | Reproject our existing scoped inbox; do not create a second conversation store. |
| [Letta agent prompt](https://github.com/letta-ai/letta-code/blob/main/src/agent/prompts/letta.md) | Discoverable memory paths and explicit memory tools | Preserve separate conversation, procedure and reviewed-knowledge surfaces. Autonomous permanent memory promotion is outside this change. |
| [Superpowers](https://github.com/obra/superpowers) | Reusable procedures and explicit verification discipline | Verify final consumer behavior, not merely successful schema serialization. Do not impose a coding workflow on every marketing question. |

## Implemented contract

- `skills.list {}` returns installed IDs, versions, purposes and required capabilities.
- `skills.read {"skill_id": "marketing.copy", "version": "1"}` returns one procedure and its
  criteria. Unknown IDs/versions return a recoverable `not_found`; no filesystem/URL lookup exists.
- Both are credential-free observe tools using canonical invocations/receipts. A catalog entry
  describes a procedure, not tool readiness. The current planner snapshot remains authoritative.
- Opportunity, strategy, copy and experiment skills guide domain work. Creative v2 continues
  automatic work, resolves retrievable inputs with tools, and hands off only blocked operations.
- The generic harness guides discovery, context use, execution, observation and completion.
  It does not hard-code keyword routing or require skill loading for a simple answer.
- Slack injects current conversation pairs and selected memory as data at each planning boundary.
  Transcript projection remains bounded; canonical history remains intact. Shared/private and
  thread boundaries are preserved, and edits/deletions are read from the existing inbox owner.

## Verification and critique

The regression uses signed Slack admission, SQLite, service restart and the actual reasoning
request boundary. It also sends a new thread and checks that the prior answer is absent.
Skill tests execute discovery and versioned reading through actual canonical Run receipts with a
fixture reasoner. Fixture reasoning proves transport and scope, not smart model behavior.

The opt-in `colleague_canary.py` runs the real official Codex adapter from a fresh wheel install.
It has fixed synthetic search results, a read-only tool allowlist, four task-specific review
criteria, and records decisions/provider receipts for manual review. No Slack post, image,
publication or external marketing effect can execute through its allowed adapters. It is a
bounded rehearsal, not a statistically reliable benchmark or evidence of real market trends.

Observed fresh-wheel rehearsal: official Codex CLI 0.153.4, requested model `gpt-6-astra`, one
trial per scenario. All four Runs completed and the outputs were inspected against their criteria:

| Scenario | Observed action sequence | Output inspection |
| --- | --- | --- |
| Copy | list → read → answer | Two actual Korean drafts and a recommendation, limited to the supplied features. |
| Opportunity | list → read → search → search → answer | Synthetic source links, two alternatives, recommendation and a small experiment; no real-trend claim. |
| Experiment | list → read → answer | Correct 6%/3% comparison, confounds and a one-variable follow-up; no causal winner claim. |
| Creative reference | list → read → prepare → search → search → answer | Executed the automatic route, rejected an irrelevant lead, and distinguished text-only leads from inspected images. |

The installed module bytes matched all five changed production modules. Wheel SHA-256:
`10b43539ed930356e15b7a86b34329ba97bb3497f94ff4e60cef53e56733b9cf`.
The canary is not an old/new statistical comparison. Its discovery calls and occasional second
search are visible costs; no claim of reduced latency or general intelligence improvement follows.

Risks: skill discovery consumes calls from the existing eight-call Slack budget, model behavior
remains probabilistic, and oversized history can still be omitted by the existing bounded
projection. The harness tells the model to reuse already loaded procedures; it does not silently
increase budgets or retry uncertain effects. A skill result is procedure guidance, never evidence,
extra tools or approval. Existing persisted v1 skill goals are not rewritten.

## Follow-up scope under #93

- Verify the deployed Slack server version/configuration and collect representative authorized
  conversations before claiming production improvement.
- Broaden repeated, independently reviewed task evaluations after the initial canary. Measure
  unnecessary questions, premature handoffs, completed deliverables, tool cost and factual errors.
- Add task-plan persistence or targeted history retrieval only against demonstrated long-task
  failures; no second memory store or autonomous self-modification is introduced here.
- Live proactive scheduling, channel analytics, publication and paid/community execution retain
  their existing implementation and approval boundaries. This PR does not complete those tracks.
