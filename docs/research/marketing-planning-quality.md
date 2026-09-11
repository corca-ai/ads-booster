# Campaign and reporting quality

Status: Active — procedure improvements; business impact unverified.
Reviewed 2026-09-11. Owning issue: #165.

## Selected official guidance

These are adaptations to Trace's existing installed service, not imported plugins or evidence
that another vendor's results transfer to Trace. All eleven supplied pages were accessible.

| Official source | Application in Trace | Limit or deliberately omitted practice |
| --- | --- | --- |
| [Anthropic marketing plugin](https://github.com/anthropics/knowledge-work-plugins/blob/main/marketing/README.md) | Separate reusable planning, writing and reporting procedures; inspect connected capabilities. | Do not claim its analytics/CRM connectors exist in Trace or install an overlapping agent runtime. |
| [Campaign plan](https://github.com/anthropics/knowledge-work-plugins/blob/main/marketing/skills/campaign-plan/SKILL.md) | Strategy v2 connects objective, audience, evidence, assets, dependencies and measurement. | Generic posting cadences, budget percentages and targets are not Trace benchmarks; missing owners stay unassigned. |
| [Performance report](https://github.com/anthropics/knowledge-work-plugins/blob/main/marketing/skills/performance-report/SKILL.md) | New performance-report procedure leads with the business question, comparable metrics and prioritized action. | No fabricated prior period, target status, revenue or ROI. Do not copy generic formulas without metric definitions. |
| [OpenAI Academy marketing](https://academy.openai.com/public/clubs/work-users-ynjqu/resources/use-cases-marketing) | Connect a reviewable creative brief and actual copy with owners, constraints and success measures. | Sample prompts are starting points, not evidence of campaign effectiveness. |
| [OpenAI Academy data analysis](https://academy.openai.com/public/clubs/work-users-ynjqu/resources/data-analysis) | Define metrics, inspect inputs, calculate and verify the report. | Trace's bounded funnel tool does not acquire ChatGPT's arbitrary spreadsheet/Python analysis capabilities. |
| [Brand.ai case study](https://claude.com/customers/brand-ai) | Preserve scoped brand context and conversation corrections through existing retrieval. | Customer/vendor claims are not an independent comparison; no model switch or wholesale brand-document injection. |
| [Google helpful content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) | Copy v3 addresses a reader need, adds supported value and checks title/body promises. | No invented firsthand experience, keyword padding, ranking guarantees or E-E-A-T score. |
| [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | Reuse one agent, existing tools and small composable procedures. | No multi-agent framework without demonstrated need. |
| [OpenAI practical agent guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) | Concrete steps and incomplete-input branches, with existing host approval boundaries. | A procedure never grants action authority or creates tools. |
| [Context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Keep substantive instructions in discoverable versioned skills, loaded on demand. | No appended global marketing checklist or new memory store. Existing scoped discovery already addresses this mechanism. |
| [Agent evaluations](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Preregister task criteria; inspect outcomes and traces separately from lifecycle; keep model/trial/time evidence. | Automated substring or lifecycle checks cannot grade usefulness; human calibration and repeated trials are still needed. |

## Before and after

- Strategy v1 connected message alternatives to a small experiment. V2 also specifies measurement
  feasibility, unassigned ownership, review/measurement dependencies and an execution sequence within
  supplied constraints. A copy-only request still receives copy directly.
- Growth and experiment guidance already handled mature nested cohorts and causal caution. The new
  `marketing.performance_report` adds source/definition checks, pooled-rate arithmetic, percentage
  points versus relative change, missing comparisons and an actionable stakeholder report. It calls
  `marketing.analyze` only when data fits its existing contract; repeated events are not coerced into
  people. No new analytics adapter is warranted by these sources alone.
- Copy v2 checked product truth and coherent partial edits. V3 additionally checks reader usefulness,
  original supported value, title/body alignment and fabricated experience in search-oriented writing.
- The existing opt-in Slack rehearsal now has a `planning` scenario with three task-specific criteria,
  elapsed time and an explicit ungraded quality state. The six prior dimensions remain the rubric;
  deterministic catalog and arithmetic tests remain separate from language-quality review.

## Verification and limits

The PATH-selected uv-tool command is a retired installation: `trace-marketing service run --help`
returns “No such command 'service'”; its interpreter cannot import the current `ads_booster` namespace.
It was preserved. The baseline is a fresh non-editable wheel built from main `262529e`, not that
retired installation. Baseline and candidate use separate fresh Python 3.14 environments and the
same synthetic three-turn task sequence with the official Codex CLI session. This is an isolated
candidate check, not a deployed installer or production Slack test.

The frozen development environment passed 37 focused tests across catalog receipts, protected
procedures and funnel arithmetic. Scoped Ruff lint/format and BasedPyright passed. The fresh
candidate CLI exposes `service run`; its installed skill module matches the source digest, and
the strategy/report/copy versions are 2/1/3 with ready catalog entries.

Baseline and first-candidate fresh wheels completed the same three real-model turns with
`gpt-6-astra`, one trial each. Their initial comparison follows below.
Runtime dependencies were held to the baseline installation's exact versions. The initial sandboxed
baseline emitted no provider receipts and stayed `running`; it is an environment failure, excluded
from quality grading and retained in the evidence directory. The authorized run completed normally.
An initial source-test collection failure was a missing `pypdf` in the old development environment;
the recorded focused passes used a separate `uv sync --frozen` environment.

| Task | Baseline / candidate score | Observed difference | Baseline / candidate seconds |
| --- | --- | --- | --- |
| Constrained campaign | 11/12 / 11/12 | Both preserve constraints; candidate states a 96-hour window and explains that views are not unique people. Both plans remain longer than requested. | 76.268 / 62.259 |
| Uneven-denominator report | 12/12 / 12/12 | Both correct 10.5% to 2.9% and refuse invented achievement. Candidate reads report v1; baseline reads growth v1. Both call `marketing.analyze`. | 46.928 / 48.111 |
| Useful blog introduction | 12/12 / 12/12 | Both reject unverified ranking/automation and return two usable sentences. Both answer directly; this does not isolate copy v3's contribution. | 12.157 / 10.909 |

Scores are implementing-agent judgments on the preregistered six dimensions, not independent
marketer review. Each task exceeds the 10/12 threshold without a hard failure; equal scores do not
establish general language-quality improvement. Timing is one observed run, not a speed benchmark.
The concrete procedure improvement is explicit campaign/reporting guidance, not newly acquired
arithmetic competence. The pooled 29/1000 formula remains visible in both responses.

Trace inspection exposed a tool-contract problem: currency was mandatory even without spend.
Baseline supplied KRW as a declared placeholder; the first candidate used XXX as a declared
no-currency marker. Neither reported monetary results, but the schema encouraged invented metadata.
The owner now permits omitted/null currency only when spend is absent and preserves null in the
result. Spend, including zero, still requires currency. Existing supplied currencies remain valid.
Five focused regression cases failed before this correction (including canonical schema admission)
and passed afterward. The same pattern is owned by `FunnelCohort`; its generated descriptor schema
updates automatically, so no consumer-only workaround or duplicate validator was added. No other
funnel currency owner was found. This fixes the cause rather than teaching a placeholder convention.

The final wheel was installed into another fresh environment with the same dependency constraints.
Both changed production modules matched source hashes. Installed arithmetic preserved null currency
and a 2.9% result, and rejected reported zero spend without currency. All three real-model turns
completed again. The report actually omitted currency and spend, so the corrected schema was
exercised rather than merely inspected. Its first call named a nonexistent objective stage; the
existing known-input rejection allowed one corrected call without an uncertain-effect retry.
The final report was correct, but this avoidable call earns 1/2 for decision quality: final task
scores are 11/12, 11/12, 12/12, with no hard failures. Final elapsed times were 69.058, 61.912 and
12.811 seconds, using 4, 4 and 1 provider calls. This is a contract fix with a visible remaining
tool-use imperfection, not evidence of an overall score or efficiency gain.

The [reviewed inputs, outputs and scores](marketing-planning-quality-results.json) retain actual
model IDs, per-turn provider/tool counts and wheel digests. Full synthetic runtime evidence is under
`/private/tmp/trace-marketing-review-20260911/{baseline-authorized,candidate-run,final-run}`. Reproduce with
the installed interpreter and `slack_colleague_canary.py --scenario planning` as documented in
[testing](../development/testing.md). No release, installed-host activation, real platform collection,
human brand acceptance or marketing lift is established.

## Follow-up: make the tool's required name relation visible

The final rehearsal's rejected call exposed a documentation gap: `objective_stage` was a generic
string in the model-facing schema, while validation required an exact existing downstream stage.
The schema now tells the model to copy a supplied downstream `steps[].name` verbatim, rather than
invent a rate label or select the entry stage. Validation and execution behavior are unchanged.
This applies the tool-interface guidance from the agent-design sources above at its owning contract.

The `funnel` diagnostic adds two tasks with different counts and stage names, including a change
from first-schedule creation to D7 retention. A fresh wheel matched the changed module's digest and
exposed the description in its real descriptor. The 20 focused funnel tests and scoped Ruff/
BasedPyright checks passed. Both pre-description and updated installations then passed both tasks
with `gpt-6-astra`, one trial per variant: exact first-call objective/stage names, no invented
financial inputs, zero failed receipts and correct 15%/8%, 15%/10% two-sentence answers.
Inputs, answers, calls, timings and wheel digests are in `schema_followup` in the linked JSON record.
Since both passed, this closes the ambiguous interface documentation but does not establish a
lower model error rate. The preceding PR head's GitHub CI also passed; follow-up CI is separate.

## Remaining work

- Human marketer review and repeated or blinded trials on held-out briefs; the implementing agent's
  rubric judgments alone cannot establish general writing quality.
- Shorter campaign answers and fewer avoidable tool-input corrections; the final rehearsal passed
  its output threshold but did not improve these dimensions reliably.
- Actual campaign measurement requires authorized account data, attribution and mature observations.
- Upgrade the selected retired local CLI through the established installation path as separate
  installation work; this PR does not activate an installed host or publish a campaign.
