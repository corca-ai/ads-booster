# Conversational marketing colleague

Status: Active — bounded candidate verification, not deployed Slack acceptance.
Reviewed: 2026-09-11. Owning issue: #168; PR #166.

## Observed problem and intended behavior

A read-only sample of six September 9–11 `trace-agent-test` threads showed ordinary greetings
working, but also internal capability terminology in replies, language correction followed by
fresh onboarding, and a post request answered only with an intention to read a procedure.
The earlier skill inventory thread offered a later catalog lookup after listing selected context.
These are delivered-message observations, not proof of the deployed model, tool receipts or
learned procedure contents. Private transcripts and employee information are not copied here.

Trace should act as a teammate with marketing expertise: maintain the current task, make
reasonable creative choices for reversible drafts, discover relevant procedures, use actual tools
when useful, and deliver the requested work. Ordinary assistance does not need a campaign setup.
A missing account connector must be explained as a concrete limitation, not an invented service
failure or a list of runtime identifiers. External actions still use existing approval boundaries.

## Sources and selected adaptations

The [eleven-source review](marketing-planning-quality.md) remains the marketing and evaluation
baseline. This follow-up changes how that expertise is applied during conversation.

| Reference | Applied mechanism | Limit |
| --- | --- | --- |
| [OpenClaw skills](https://docs.openclaw.ai/tools/skills) | Skills teach when/how to use tools; loading respects environment and availability. | No runtime replacement, arbitrary shell access or implicit plugin installation. |
| [Hermes working with skills](https://hermes-agent.nousresearch.com/docs/guides/work-with-skills) | Discover compact metadata, read the chosen procedure, then read further material only when needed. | Do not load every skill or copy its self-modification policy into shared Trace memory. |
| [Hermes skills system](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills) | Separate reusable procedures from execution tools and scope their availability. | A new procedure does not implement a connector or grant external authority. |
| Ceal public-channel work examples inspected through Slack | Short completed-result statements and a concrete request for missing evidence. | Observed conversation only; no claims about Ceal internals or comparative performance. |

## Implementation

`providers/codex_reasoning.py` owns the colleague interaction contract. It prioritizes current
intent, user-facing results, continuity of language/format edits and bounded initiative. Technical
details and uncertainty appear when they affect a decision rather than as a routine footer.
Delegated visual choices no longer unconditionally trigger a request for a complete brief.

Scoped skill guidance prefers `skill_list`/`skill_get` when present, falling back to the existing
built-in catalog. Inventory requests perform the lookup immediately; selected context is a
shortlist. A relevant procedure is read and applied, and only a remaining subtask warrants another
procedure. Ordinary edits reuse prior context. Compatible business calculations use
`marketing.analyze` for reproducible evidence; unsupported data is not coerced into a funnel.
The first candidate still skipped this tool for simple counts, which motivated the final clause.

The old provider instruction also suggested shared skill writes from admitted experience, while
the versioned learning procedure and host require a current explicit request. The provider now
matches that host contract. Existing skill storage, revisions, tool registration and authority
remain unchanged. Nothing here adds a scheduler, account scraping or new production tools.

## Verification contract

The existing installed Slack rehearsal adds `--scenario colleague`: inventory, delegated post
draft, copy-only correction, language correction, a two-sentence metric report and an unavailable
recurring account-analysis request. Inspect each final reply and tool trajectory. A promise to
read a skill is not a deliverable, and `completed` alone is not a quality verdict.

Each six-turn trial uses a fresh non-editable wheel, identical synthetic inputs, the same runtime
dependencies and `gpt-6-astra`. Slack transport is signed but sends are captured locally; the
adapter restarts between turns. The baseline is the prior PR wheel, not the inaccessible deployed
server. Per-task acceptance criteria were fixed before the baseline calls. The named scoring
dimensions were refined after baseline launch; therefore this comparison reports task observations,
not a preregistered aggregate score or statistical improvement.

Focused source checks select `tests/providers/test_codex_reasoning.py`,
`tests/marketing/agent_service/test_skill_tools.py` and `tests/knowledge/test_procedural_skills.py`.
Prompt contract tests do not prove natural conversation; actual-model trials supply that bounded
evidence. The existing `installed_learning_model_canary.py --scenario minimal-skill-reuse` checks
explicit shared skill creation and exact revision reuse after restart with real model calls.

Remaining acceptance includes actual deployed thread follow-through, custom team procedures,
human marketing judgment, account integrations and longer conversations with exhausted budgets.
No general parity with OpenClaw, Hermes or Ceal, business lift, or live image success is claimed.

## Observed results

The [synthetic inputs, replies, calls and receipts](conversational-colleague-results.json) preserve
all three trials. Baseline and first candidate both discovered the catalog, read copy v3, delivered
a post and kept it through copy-only/language edits. Both calculated 7.5% correctly without the
available arithmetic tool, failing that task's tool-use criterion. The final candidate used
`skills.list` → `skills.read(marketing.copy, v3)` for the first two tasks, no tools for narrow edits,
and `marketing.analyze` for the report, with three `no_effect` receipts and no failed tool receipt.
The numeric answer stayed correct; this establishes a reproducible calculation path, not improved
arithmetic accuracy. The extra calculation took 27.964 seconds versus baseline 11.470 seconds.

All final six replies met their bounded task criteria by implementing-agent inspection. No
questionnaire, restart greeting, internal skill narration or claimed scheduling appeared. Existing
replies were also mostly good, so overall tone superiority is unproven. In particular, this
restricted built-in-catalog rehearsal does not reproduce the deployed custom trace-post skill.

The separate shared-skill canary passed 11 checks in four actual foreground model calls, with no
background/research calls or external Slack sends. It stored an explicitly requested synthetic
rule, preserved its exact revision over restart, read it and answered with the specified token.
That run used the first candidate. All four request projections have identical prompt hashes in
the final wheel because the subsequent calculation clause only appears with `marketing.analyze`,
which this scoped canary does not expose. This is bounded compatibility evidence, not a second
execution or proof of arbitrary skill learning.

Focused source tests: 25 passed. Three changed Python files pass Ruff formatting/lint and
BasedPyright. The final installed CLI exposes `service`, and the provider module matches the
source. Final wheel SHA256: `787c703764ab9acf7f8f923050e98728c802e77148fafb184a9d431dafce4d2d`.
