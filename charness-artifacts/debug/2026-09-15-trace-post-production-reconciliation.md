# Trace post production reconciliation Debug
Date: 2026-09-15

## Problem

Issue #186: production image requests stop uncertain without persisted images. Independent planning,
knowledge worker and updater errors obscure the actual outcome.

## Correct Behavior

Given an explicit supported image request, preserve its requirements, execute once, show observed
progress and deliver six verified files. Given incomplete execution, retain uncertainty and report
the saved reason without claiming generation or replaying the operation.

## Observed Facts

User-provided HANDOFF covers midnight–15:22:38 KST, not a full day. Two image jobs have null results,
no provider-images or calls.jsonl, and generation_event_required. This proves no saved completion,
not external non-execution. The 14:57 schema error eventually completed at 15:18. The 14:58
obligation conflict precedes image execution. Knowledge BEGIN IMMEDIATE failed at 15:09, after
the first image failure. Thirteen updater HTTP errors lack status. Restart overlap is not causation.
Production current values: Luna, CLI 0.154.0, both shell features true; historic snapshots absent.

## Reproduction

Before repair: semantic proposal additions fail decoding; wrong tool input raises instead of reaching
correction; a real concurrent SQLite writer raises OperationalError; final task text leaks
awaiting_reconciliation; HTTP fixtures expose only HTTPError. Each regression then passes its owner
repair. Zero-image terminal stream fixtures distinguish failed preparation and no observed generation.
Local baseline non-editable macOS provider runs with Astra and Luna each completed seven native events;
Linux zero-output behavior did not reproduce. Artifacts are private under
/private/tmp/trace-post-reconciliation, not checked-in synthetic proof of production success.

## Candidate Causes

- Model wire contract asks the actor to restate host-owned immutable requirements.
- Linux execution/tool availability differs from a local provider, despite current feature parity.
- Restart or transport interruption loses execution evidence before terminal persistence.
- Ingress acquires a writer transaction on every empty poll and lets claim contention kill its worker.

## Hypothesis

Host-derived proposal metadata removes accidental obligation rewriting. Pre-effect input failures
can be corrected under the existing model budget. Busy claims can remain pending without sink effects.
Image diagnosis needs actual stream boundaries, not a larger timeout.

- disconfirmer: unchanged host obligations and denied-tool negative controls; real SQLite lock/release;
  actual Luna roundtrip falsifies general Luna incompatibility. Existing updater tests protect active work.

## Verification

Candidate fresh non-editable installation outside checkout passed 129 focused tests. Source tests
passed 127, with two updater fixture failures caused by inherited PYTHONPATH; isolated execution of
the same selection passed both. Final integration includes main PR #197's native image instruction
bridge and PR #193's Threads actor IDs. Integrated installed selection passed 130 tests; CLI version
and service doctor passed. CI and live verification are pending.

## Root Cause

Confirmed local defects: model-facing proposal and canonical immutable contract disagree; invalid
pre-dispatch tool inputs escape planning; pre-effect lock contention escapes the ingress owner;
final result renderer omits known deferred failure evidence; updater drops HTTP phase/status.
Historical image cause remains unproven. PR #197 repairs a real outer-tool/native-tool instruction
mismatch but cannot retrospectively establish why these two turns ended.

Five whys stop at missing host contracts/observability: model variability is exposed to immutable
fields, rejected plans lack bounded feedback, polling assumes uncontended writes, and final consumers
collapse structured failures. No evidence identifies the production long-held DB writer.

## Invariant Proof

- Invariant: no effect before validated admission; no success without event-bound verified artifacts.
- Producer Proof: typed host additions, schema-path evidence, command/image counters and private atomic JSON.
- Final-Consumer Proof: Slack owner fixtures retain uncertainty, render saved reason, deliver no failed assets.
- Interface-Shape Sibling Scan: task result, image provider, ingress claim and updater diagnostics.
- Non-Claims: no historical Linux root attribution; local files do not prove Slack upload or deployment.

## Detection Gap

Earlier local model and Linux launcher probes cover different boundaries. Neither proved a Linux
image-to-Slack roundtrip. Stream diagnostics and deployed file readback close the observation gap;
strict fixture output alone does not measure model reliability.

## Sibling Search

Wrong model: local success or a terminal enum is enough to explain final user behavior.

- Same layer, cross-file: task_results versus deferred_failure. Decision: same bug, fix now;
  proof: executable final-renderer fixture. Disconfirmer: preserved Threads result branch.
- Abstraction up: updater HTTP diagnostic collapse. Decision: same class, diagnostic-only for this slice;
  proof: 401/403/429/503 fixtures. Network cause itself remains unknown; follow-up: deferred #186 updater HTTP diagnosis.
- Specialization down: ingress claim before sink versus post-sink receipt persistence. Decision: same bug,
  fix now only before sink; proof: real lock and existing post-effect failure controls.
- Mental-model sibling: native image completion versus file ingestion/Slack upload. Decision: valid follow-up
  outside the slice; proof: macOS provider roundtrip only; follow-up: deferred #186 deployed image acceptance.

Pattern ladder: observed opaque failures → owner information loss → final-consumer collapse across
Slack/updater → unproven assumption that local completion proves deployed outcome. Prevention remains
at each owner boundary; no global retry or exception suppression was introduced.

## Seam Risk

- Interrupt ID: trace-post-production-20260915
- Risk Class: external-seam, host-disproves-local
- Seam: Linux Codex native events through durable image job to Slack files.
- Disproving Observation: production zero saved images after earlier local green checks.
- What Local Reasoning Cannot Prove: Linux image generation or actual Slack delivery.
- Generalization Pressure: monitor

## Interrupt Decision

- Critique Required: yes
- Next Step: spec
- Handoff Artifact: charness-artifacts/spec/2026-09-15-production-reconciliation.md
- Resolution: open

Scoped full review: ../critique/2026-09-15-production-reconciliation.md.

## Prevention

Preserve contracts, budgets and uncertain operations. Merge independently reversible fixes, verify
exact main CI and public installed SHA, then inspect a fresh supported image request through actual
file delivery. Do not close #186 based only on diagnostics or existing historical task states.
