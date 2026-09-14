# Slack Marketing Approval Debug Review
Date: 2026-09-11

## Problem
Slack image request led to two exact-hash approval failures; asking `검토 1이 뭐야` also returned the identical generic error. Owning issue: #167; candidate PR: #166.

## Correct Behavior
Review help must explain how to inspect a pending action without changing it. Approval rejection must preserve authority and exact-target checks, identify known reasons, and never blindly retry an uncertain effect.

## Observed Facts
Read the supplied Slack thread (17 replies). No successful generation receipt appears there; this does not prove no execution occurred. SSH to configured on-prem host timed out. Installed SHA, actual actor permission and approval exception remain unknown. Prior 2026-09-10 Slack GitHub incident also lacked host access; unrelated user-owned latest.md remains untouched.

## Reproduction
Signed Slack fixture: `검토 1이 뭐야`, negative/out-of-range/non-numeric pages, no pending work and three exact-approval failure conditions. All nine cases fail against ac1b5b4 with the observed generic response. No external provider is used.

## Candidate Causes
- Parser treats arbitrary review suffix as an integer: reproduced.
- Actor lacks approval permission or current invocation differs: generic-response behavior reproduced with explicit fixtures; actual incident attribution unproven.
- Tool readiness/adapter or deployed version differs: possible; host access unavailable.

## Hypothesis
Centralized review-input handling removes conversion failures without entering continuation. An allowlist of known error codes distinguishes rejected approval conditions; unknown errors disclose no exception payload and do not suggest reapproval.

## Verification
Pre-fix: nine signed transport regressions fail. Final focused selection: 52 passed in source and a fresh non-editable Python 3.14 wheel with pytest 8.4.2 outside checkout. Both installed channel modules match source hashes. CLI service help passes. Wheel SHA256: `71fd02c45bc21f239225a84abd993968d776fd4d4f19058f4d008cd720afa263`. BasedPyright passes; scoped Ruff passes excluding existing EM101/TC001/TRY004/I001 debt; the new test passes full Ruff. No full suite was run manually.

## Root Cause
Confirmed local chain: natural-language suffix reaches int -> ValueError -> catch-all response -> user repeats approval/help -> tests covered valid pages and raw exceptions but not delivered error guidance. Exact production approval failure itself remains unproven.

## Invariant Proof
- Invariant: when a channel command rejects invalid input or authority, the delivered Slack response must explain the rejection without creating another invocation or changing approval state.
- Producer Proof: signed input through real parser/repository with controlled authority/readiness.
- Final-Consumer Proof: captured Slack sender text plus unchanged canonical run and records; verified in source and fresh wheel.
- Interface-Shape Sibling Scan: slash review has the same eager int conversion; strict internal review_pages remains authoritative for delivery evidence.
- Non-Claims: live server, actual model tool discovery, generated image, real Slack send and actual approval failure cause.

## Detection Gap
Existing review test expected a raw ValueError for an invalid page. Add channel-consumer regressions including unchanged canonical records and no natural-approval entitlement from help text. Existing CI selects the channels directory.

## Sibling Search
- Mental model: any command-prefix suffix is structured input and all errors can use one retry instruction.
- Same layer: review and approve routing; decision: fix now; proof: signed fixtures.
- Abstraction up / cross-file: slack_commands slash review; decision: fix now with one input owner; proof: signed form fixture.
- Specialization down: negative, huge, Unicode and absent approval pages; decision: fix now; proof: bounded parsing and regressions.
- Mental-model sibling: actual model listing only selected context and offering a later full-catalog lookup; decision: follow-up #167; proof: thread observation only, no runtime trace.

## Seam Risk
- Interrupt ID: slack-marketing-loop
- Risk Class: external-seam
- Seam: installed approval execution cannot be inspected from this network.
- Disproving Observation: none; local fixture cannot identify deployed failure.
- What Local Reasoning Cannot Prove: installed version, permissions, provider effect.
- Generalization Pressure: monitor

## Interrupt Decision
- Resolution: resolved
- Critique Required: yes
- Next Step: PR
- Handoff Artifact: this issue-linked bounded contract: fix parser and diagnostic delivery only; require nine regression cases, existing exact/natural approval controls, and fresh installed wheel checks. Live approval recovery stays open in #167. No approval bypass, automatic retry, tool installation or public delivery changes.

## Prevention
Two independent file-backed Codex reviewers passed the supplied diff: authority/evidence and user recovery lenses. Act before ship: no blockers. Bundle: align the mapped target-change code with `slack_production_target_changed` (applied). Over-worry: no evidence that help grants approval or logs reveal exception payloads. Valid but defer: live incident cause and complete tool-discovery behavior remain #167. The bounded follow-up passed with all four file hashes matching: packet `3c551c09b33e3b53a05c9396e5ba52922acf5f1dbc138b640c3b735b2ba382bb`, findings `4bcfe99d7d29e4987f7d96365074bc7438632937242b73e210d486e973a7f137`; typed receipt and delivery ledger permit acceptance. The semantic runner refused missing adapter sections, so the compatible file-backed runner was used with hashed input, read-only envelope and receipt/ledger validation; first preflights did not start a reviewer. Fresh-Eye Satisfaction: worker-delivered for both initial lenses and final follow-up. User explicitly requests PR delivery and existing automatic updater, not further SSH work. Preserve raw invocation review pages and existing user artifacts.

Verification command: `python -m pytest -q -p no:cacheprovider --tb=short tests/marketing/channels/test_slack_approval_feedback.py tests/marketing/channels/test_slack_production_approval.py tests/marketing/channels/test_slack_commands.py tests/marketing/channels/test_slack_events.py`. Installed run cwd: `/private/tmp/trace-slack-installed-check`, interpreter: `/private/tmp/trace-marketing-review-20260911/slack-venv/bin/python`.
