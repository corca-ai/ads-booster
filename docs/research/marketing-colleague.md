# A useful Slack marketing colleague

Status: Active — bounded capabilities implemented; competitive superiority unverified.
Research and rehearsal: 2026-09-09–10. Owning issue: #152, PR: #153.

## What we take from other agents

The [earlier Hermes/OpenClaw/Ceal comparison](adaptive-skills.md) informed scoped discovery,
versioned procedures, durable context and verified learning. This pass adds marketing judgment.

| Primary reference | Useful mechanism | Trace application and limit |
| --- | --- | --- |
| [Manus marketing](https://manus.im/solutions/marketing) and [practitioner course](https://academy.manus.im/courses/manus-for-marketing-692f82a7f918f9c55d017ef4) | Connect research, analysis and finished assets in one workflow | Return actual copy plus an actionable decision, using available tools. These are vendor descriptions; Manus was not run against Trace. |
| [HubSpot Breeze channel adaptation](https://www.hubspot.com/products/artificial-intelligence/use-cases/tailor-content-for-channels) | Ground channel variants in brand and audience context | Reuse scoped team evidence and preserve campaign constraints. Trace does not acquire HubSpot's CRM integrations by adopting this procedure. |
| Hermes/OpenClaw/Ceal sources in the earlier comparison | Discover and reuse capabilities with persistent context | Skill discovery and exact revision reads are implemented. Runtime-generated executable adapters, broad browser automation and verified Ceal implementation parity remain outside this change. |

The target is one colleague that can turn evidence into a useful decision and deliverable, then
remember a verified procedure. More generated text or more tool calls do not establish better work.

## Practitioner mechanisms, not borrowed success claims

[Duolingo's growth model](https://blog.duolingo.com/growth-model-duolingo/) explains how the team
used user-state transitions and retention to choose growth work. Its reported business trajectory
is company evidence, not proof that one marketing tactic caused that trajectory. Transfer the
decision method: choose the desired customer outcome, inspect stage denominators and constrain
the experiment. Do not optimize cheap registrations when the goal is retained users.

[Duolingo's experiment account](https://blog.duolingo.com/improving-duolingo-one-experiment-at-a-time/)
describes a promotion that increased revenue while hurting engagement and retention, prompting
iteration. Trace adopts a primary outcome plus a customer-experience guardrail and complete
observation window. It does not copy Duolingo's thresholds, product mechanics or treatment effects.

[Spotify's Wrapped retrospective](https://newsroom.spotify.com/2024-12-04/10-years-spotify-wrapped/)
illustrates connecting product value to personal expression and a shareable experience. For Trace,
the useful question is which actual customer moment makes its verified value understandable.
This does not establish that Trace has personal analytics, automatic sharing or Spotify-scale
distribution. Customer interviews should expose alternatives and friction as well as appealing
quotes; three interviews cannot establish population prevalence.

## Implemented behavior

- `marketing.growth` connects the requested outcome and constraints to `marketing.analyze`.
  The tool calculates stage conversions, dropoffs and cost per target person from supplied nested
  unique-person cohorts. It returns explicit denominators, missing-value semantics and comparison
  limits. It neither collects platform data nor infers causal lift, significance or budget approval.
- `marketing.customer_insight` connects attributable interview evidence, existing alternatives and
  friction to a real draft and a behavioral follow-up question. Desired features are not facts.
- `marketing.experiment` v2 adds outcome and guardrail discipline plus measurement feasibility.
- `marketing.copy` v2 and customer insight require a coherent finished draft. Finding a contradiction
  does not justify leaving its correction to the user; partial edits must fit preserved text.

The calculation is bounded to eight cohorts and eight stages. Counts are strict nonnegative
integers; costs use bounded decimal strings. Ratios are six-place decimal strings, compatible with
the canonical ledger's prohibition on floating-point JSON. A known semantic input rejection yields
a zero-cost failed receipt with field locations, rather than uncertain-effect reconciliation.

## Evaluation design and observed iterations

The opt-in `slack_colleague_canary.py --scenario marketing` writes criteria before its first model
call. Six signed synthetic Slack turns exercise goal-aligned arithmetic, a zero-budget Japanese
Threads experiment, interview-based copy, attributed research, a first-sentence-only revision and
a three-line handoff. Service composition is rebuilt between turns; durable thread history remains.
Search fixtures and Slack sends are synthetic/captured. Codex `gpt-6-astra` calls are real.

Review dimensions are evidence, product truth, business metric, usable deliverable, decision quality
and scoped follow-through. Score each 0 (missing/wrong), 1 (partial) or 2 (meets this task); for a
dimension not requested, 2 means preserving the established constraint without gratuitous claims.
The predeclared target is at least 10/12 per task, with no invented features, causal proof or effects.
These are implementing-agent judgments, not independent human ratings or a statistical benchmark.

The baseline is the prior PR wheel, not old production or a competing agent. It already handled all
six ordinary tasks well, including correct $20/$10 costs and cautious D7 interpretation. There is
no evidence here for a general baseline-to-candidate language-quality gain.

1. Initial implementation: add the calculation and growth/customer procedures. A real canonical
   execution exposed floating-point incompatibility; decimal arithmetic and ledger-safe string
   outputs fixed the cause. The first installed candidate discovered/read `marketing.growth` and
   invoked `marketing.analyze` before answering, making arithmetic independently reproducible.
2. Boundary review: a schema-valid but nonnested funnel threw during execution and left the Run in
   reconciliation. A failing canonical regression captured this before changing the adapter; known
   validation failures now produce inspectable failed receipts and allow the conversation to finish.
3. Deliverable review: first-candidate turn 3 mixed next week with this week, recognized the problem,
   but asked the user to fix the final text. The new copy review step requires repairing the actual
   draft. Turn 5 correctly preserved text but inherited the earlier inconsistency. This is a real
   candidate weakness, not a baseline failure. A fresh-wheel rerun checks the correction.

The rerun completed all six turns. Turn 3 returned a coherent two-line Japanese draft, acknowledged
that changing its body/CTA made it more than a first-line-only experiment, and retained the
unverified-effect caveat. Turn 5 changed only the first sentence and preserved the remaining line
exactly. Both candidates used the calculation tool; the baseline calculated correctly without it.

| Task | Baseline | First candidate | Revised candidate | Main review observation |
| --- | --- | --- | --- | --- |
| Funnel decision | 11/12 | 11/12 | 11/12 | All correct; candidate arithmetic has a tool receipt. Next-test implementation feasibility still needs confirmation. |
| Constrained experiment | 12/12 | 12/12 | 12/12 | Actual Japanese alternatives, one variable, D7 outcome, guardrail and measurement limitation. |
| Customer evidence to copy | 12/12 | 9/12 | 12/12 | Initial candidate left a time contradiction for the user; revised candidate fixed the delivered text. |
| Research transfer | 12/12 | 12/12 | 12/12 | Synthetic citation remained explicitly synthetic; no original-read or market-proof claim. |
| Partial revision | 12/12 | 10/12 | 12/12 | Initial candidate preserved an inherited inconsistency; revised candidate kept coherent body text exactly. |
| Handoff | 12/12 | 12/12 | 12/12 | Three lines distinguished finished work, unvalidated claims and one human action. |

These small subjective scores establish only this rehearsal's acceptance threshold. They do not
establish that the revised candidate is better than the already strong baseline or other agents.

The existing CI also exposed `context_receipt_idempotency_conflict` when a newly learned but
budget-excluded skill changed exclusions without changing visible block IDs. The context owner
now hashes the complete observation. The pre-integration regression proved completion and exact skill selection. A replay/time
regression preserves exact idempotency and distinct observations.

Local evidence roots are `/private/tmp/trace-marketer-152/{baseline,candidate,final}`; they contain
synthetic dialogue and actual provider receipts. They are not committed product data. Final results
and current CI state belong in the PR verification section. Source tests alone do not establish
Slack usability, and this rehearsal does not establish live Slack delivery or marketing lift.

## Integration with current main

During this work, main advanced to 0.7.0 with channel-owned and personal memory. The branch
integrates that work without broadening its authority. Slack evidence supports channel learning;
workspace skill publication requires workspace-scoped authenticated API evidence. Channel input
cannot become a workspace-wide skill merely because a write tool is listed. Private reads retain
the current grants. Earlier 0.6.0 Slack skill-writing fixtures are historical, not current proof.

The merged source passed 87 focused tests, including channel memory and requester isolation.
Its fresh 0.7.0 wheel is being checked separately; final installed/model and CI results are in PR #153.

## Remaining competitive work

Status: Draft — not implemented by this PR.

Meaningful superiority needs a marketer-reviewed, blinded task set run on competitor installations,
with repeated trials, time/cost accounting and real accepted deliverables. Business impact needs
authorized campaign execution, reliable attribution and mature cohorts. Automatic creation of
executable tools needs its own bounded specification, sandbox, provenance and promotion policy;
procedural skill creation is not that capability. No release, production activation or campaign
publication is implied by this research or rehearsal.
