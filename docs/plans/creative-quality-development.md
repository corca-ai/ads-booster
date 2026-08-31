# Creative Automation Quality Development Plan

Status: Draft — the attempt feedback foundation is implemented; offline evaluation, prompt
experiments, and post-publication measurement are not yet implemented.

Last reviewed: 2026-08-31

## Goal

Improve caption, concept, persona fit, and image design as a measured system rather than by editing a
single large prompt. Every quality claim must identify the prompt/harness version, evaluation set,
scorer, human review sample, and comparison baseline. Human approval remains mandatory.

This follows the evaluation pattern of specifying the desired behavior, measuring it under the real
workflow, and improving from observed errors. It also keeps the complete harness visible because
prompt, context selection, validation, retry policy, and tools can all change measured performance.
Human feedback, override, and post-deployment monitoring remain explicit controls.

Primary references:

- [OpenAI: How evals drive the next chapter in AI for businesses](https://openai.com/index/evals-drive-next-chapter-of-ai/)
- [OpenAI: A shared playbook for trustworthy third-party evaluations](https://openai.com/index/trustworthy-third-party-evaluations-foundations/)
- [NIST AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)
- [Cloudflare Workers AI prompting](https://developers.cloudflare.com/workers-ai/features/prompting/)

## Implemented foundation

- Caption/image feedback events atomically retain stage, candidate revision, rating, tags, note,
  capture task, artifact digest, the immutable profile snapshot and its digest, and available
  generation provenance.
- The next image attempt receives the immediately preceding image rejection.
- Durable rules are grouped by profile scope, stage, target, and tag; raw repeated attempts on one
  candidate cannot promote a rule.
- Three distinct candidates promote a rule. Rules are inspectable, disableable, and do not modify
  canonical account/profile context.
- Caption rules and image rules enter different prompt boundaries.
- Tasks and worker callbacks bind the selected feedback envelope by SHA-256. This proves selection
  and worker consumption only; semantic compliance still requires human review.

## Quality dimensions and scorecard

| Dimension | Deterministic checks | Human rubric | Primary feedback targets |
| --- | --- | --- | --- |
| factual/product fidelity | required fields, reference IDs, Trace item/time equality, prohibited claims | no misleading feature or screen behavior | `app_screen`, `brand_policy` |
| persona/country fit | profile/country/language binding | audience, situation, idiom, cultural plausibility | `persona`, `locale` |
| concept strength | novelty keys, duplicate similarity threshold | clear hook, use moment, differentiated benefit | `concept` |
| caption quality | length/format/language policy | hook, clarity, voice, credible CTA | `caption` |
| visual quality | plan schema, artifact/provenance/digest | realism, hierarchy, legibility, AI artifacts | `visual_quality` |
| system reliability | valid response, task/callback success, latency/cost | reviewer effort and correction size | harness/version metadata |

Use a 1–5 anchored rubric per human dimension. Do not collapse all dimensions into one score for
promotion: a candidate with a brand-policy or fidelity failure cannot pass because its aesthetics are
strong. Report approval rate, severe-failure rate, median rating, retry count, reviewer disagreement,
latency, and cost separately.

## Versioned prompt structure

Split each model request into named, digestible modules and persist their versions with the attempt:

1. system role and non-negotiable product/policy constraints;
2. output schema and deterministic validators;
3. account instruction;
4. country context;
5. immutable persona/profile snapshot;
6. selected references;
7. stage-specific learned rules;
8. immediate attempt correction;
9. task content such as topic, caption, Trace items, and background intent.

Add a `generation_config` receipt containing model, module versions/digests, temperature, token
budget, validator version, and retry count. Keep system rules ahead of user-controlled context.
Truncate only at module boundaries with an explicit receipt; never silently truncate the persona or
the immediate correction.

## Persona development

- Treat the D1 profile as canonical human-authored context and learned rules as a separate overlay.
- Add examples only when they identify a real boundary; avoid prose that repeats the same trait.
- Maintain a coverage matrix across country, audience, situation, tone, and content slot.
- Build counterfactual cases where only one persona field changes. A valid prompt version must alter
  the intended output dimension without leaking traits from the comparison persona.
- Require human review before changing starter profiles or converting a learned pattern into a
  canonical profile edit.

## Evaluation corpus

Create a private, versioned corpus from production-shaped inputs:

- accepted attempts representing each country/profile/slot;
- rejected attempts grouped by the current tag taxonomy;
- close calls with reviewer disagreement;
- adversarial or broken inputs for unsupported claims, locale mismatch, duplicate concepts, and
  Trace time/data mismatch;
- holdout cases created after each prompt change to limit overfitting to known feedback.

Keep raw assets and review notes access-controlled. De-identify personal content. Store immutable
case IDs, input digests, expected hard constraints, rubric anchors, and adjudicated labels. Split by
candidate lineage so variants of one candidate never cross train/development/holdout boundaries.

## Experiment and promotion loop

1. Register a change hypothesis against one owned module and one primary metric.
2. Run baseline and challenger on the same frozen corpus with the same model, budget, tools, and
   retry policy. Randomize output order for human pairwise review.
3. Run deterministic graders first, then model-assisted graders for scalable signals, then sample
   every failure class and borderline score for expert review. Model graders advise; they do not
   replace the human image gate.
4. Check for broken cases, leakage, scorer shortcuts, reward hacking, and regressions by country and
   persona. Record the entire harness, not only the prompt text.
5. Shadow the challenger on live inputs without changing the shown candidate. Compare it with the
   selected production output using blinded review.
6. Promote only if severe failures do not increase, the target dimension improves on holdout and
   shadow samples, and no protected country/persona slice materially regresses.
7. Canary by account/profile with a reversible config switch. Roll back the prompt/config version,
   never rewrite historical attempts.

Initial promotion gate: at least 30 independent candidate lineages overall, at least 5 in every
affected slice, no new fidelity/policy failures, and a human pairwise preference improvement whose
confidence interval excludes zero. Revisit these numbers after collecting reviewer variance; they
are an operating starting point, not a universal statistical claim.

## Phased implementation

### Phase 1 — receipts and offline replay

- Add `generation_config` and prompt-module digests to candidate/capture receipts.
- Export a redacted evaluation case from a reviewed attempt.
- Add a local replay command that never writes candidate state or calls external publication.
- Exit: any reviewed attempt can be reconstructed to the module/version level.

### Phase 2 — rubric and graders

- Implement hard validators for locale, Trace data/time fidelity, schema, provenance, and prohibited
  claims.
- Add anchored human scorecards and reviewer disagreement/adjudication.
- Add model-assisted concept/caption/visual graders with stored grader version and rationale.
- Exit: grader results correlate with adjudicated human labels and known disagreement is reported.

### Phase 3 — prompt/persona experiments

- Add a prompt registry and account/profile canary assignment.
- Add baseline/challenger batch evaluation and blinded pairwise review.
- Run isolated experiments for caption structure, concept diversity, persona binding, then visual
  planning; do not change all modules in one experiment.
- Exit: one challenger passes the promotion gate on holdout and shadow traffic.

### Phase 4 — operational learning

- Add dashboards by model, prompt version, country, profile, stage, target, and reviewer.
- Monitor rule creation, disablement, retry rate, severe failures, and drift.
- When publication becomes available, add delayed performance outcomes as a separate signal; never
  let engagement metrics override brand/policy/fidelity gates.
- Exit: rollback, rule override, incident review, and scheduled evaluation are routine operations.

## Non-goals and limits

- No automatic publishing or automatic final approval.
- No autonomous edits to canonical profiles, country documents, or brand policy.
- No promotion based only on raw approval rate or one reviewer.
- No use of repeated retries from one candidate as independent evidence.
- No claim that the current hard-coded Workflow quality step is a real evaluator; replacing that
  placeholder belongs to Phase 2 and must use the same versioned scorecard and receipts.
