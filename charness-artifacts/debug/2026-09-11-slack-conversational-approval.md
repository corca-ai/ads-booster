# Slack Conversational Approval Debug Review
Date: 2026-09-11
Owner: #169

## Problem

In the supplied Slack thread, an image proposal becomes completed after “읽었어”; exact approval
then fails. A subsequent explicit issue-creation request repeats the review/hash ceremony.

## Capability Failure

Delegated work loses its pending execution while the user is merely conversing.

## Correct Behavior

Given an authorized shared Slack user, direct creation requests bind one exact operation without
repeated confirmation. Questions preserve unexecuted work; cancellation and revisions remain real.

## Observed Facts

Read the complete supplied thread through the issue-creation request. Local default installed CLI
is older and lacks `server`; it cannot establish the deployed service version. User-owned debug
latest.md and unrelated untracked material are preserved. No live Slack writes were made.

## Reproduction

Before wiring the fix, two signed-event regressions failed: acknowledgement produced COMPLETED;
explicit issue creation made no adapter call. Later review's EXECUTE/runtime-admission interruption
cases also failed before the recovery fix: the approved image never executed after an intervening read.

## Candidate Causes

- Continuation overwrites pending state and model stop is interpreted as task completion.
- Required tool policy has no path binding the current explicit request to approval.
- Deployed version, credential or permission mismatch could differ from this source.

## Hypothesis

Replay pending proposals independently of responses, bind permitted creation to an authenticated
current event, and recover execution from its persisted digest. Signed input should reach the
correct tool once and preserve pending state through questions/restart.

## Pattern Ladder

Observed: source fixture reproduces completed-before-generation. Local: `continue_work` replans
and `stop` completes the Run. Sibling: read invocations become latest even when approving an older
proposal. Structural: latest conversation/planning activity was treated as execution ownership.
The disconfirmer is an intervening read plus crash at each persisted approval/execution boundary.
Canonical proposal and execution bindings now decide ownership; no separate mutable task ledger.

## Verification

151 focused tests passed in the final fresh non-editable wheel outside the checkout. Six actual-model
synthetic scenarios passed in both the second and subsequent prompt/output candidates. First rehearsal revealed
the approval-word prefix swallowing a normal bug-report sentence; signed regression now covers it.
First image fixture reported success while explicitly saying no image existed; corrected fixture
uses real PNG bytes and the production output shape, and checks final state as well as dispatch.
Final exact commands and installed results are recorded in the PR.

## Root Cause

Conversation completion, latest invocation and pending execution were conflated. Separately, the
channel never interpreted direct creation intent as input to its exact-approval owner. Existing
tests encoded extra review as mandatory and lacked discussion/read/crash sequences.

## Invariant Proof

- Invariant: admitted task intent reaches one authorized exact invocation; answers cannot erase it.
- Producer Proof: signed source and permission checks; immutable request/digest approval records.
- Final-Consumer Proof: captured Slack output, adapter calls, preserved pending digest and crash recovery.
- Interface-Shape Sibling Scan: slash review, natural assent, deferred execution and receipt URL output.
- Non-Claims: deployed version, live provider image output, public issue write, universal model accuracy.

## Detection Gap

Add a pending/read/approval sequence at approval_committed, execute_committed and runtime_admitted;
cover assent admitted before delivery and edited/deleted assent after plan freezing.

## Sibling Search

- Mental model: last response or invocation owns unfinished work.
- Same layer: pending reasoning and stop; fix now, signed fixtures.
- Abstraction up: Slack summary/review and command-prefix routing; fix now, consumer fixtures.
- Specialization down: execution/deferred recovery; fix now, persisted digest and focused tests.
- Cross-boundary: source/permission changes and late proposal delivery; fix now, source-bound tests.
- follow-up: #167 owns deployed incident attribution; no live access claim.

## Seam Risk

- Risk Class: external-seam
- Seam: signed Slack, model semantic interpretation and canonical dispatch.
- Generalization Pressure: monitor
- What Local Reasoning Cannot Prove: live installation and universal language classification.

## Interrupt Decision

- Resolution: resolved
- Critique Required: yes
- Next Step: PR
- Handoff Artifact: docs/development/conversational-approval.md

## Prevention

Independent file-backed reviews delivered authority and recovery blockers. Act Before Ship:
freeze delivered proposal at admission, recheck mutated assent, recover the EXECUTE target.
Bundle Anyway: clarify direct-request policy and avoid duplicate verified issue links.
Over-Worry: reinstating a second confirmation for delegated creation contradicts the user request.
Valid but Defer: production activation and broader language evaluation remain explicit non-claims.
Follow-up reviews passed for recovery and authority. The first authority follow-up had an invalid
packet identity and was not accepted as approval; its duplicate-assent finding was reproduced and
fixed, then a new identity-bound retry delivered a pass. Authority findings digest:
`4b6991565134e81f56ea9352afa2886197d0c4242c00fe5252a29a3b9f92a29d`.
Recovery findings digest: `3caa93e795b17b2db0e580b0b3694aaa76036ee660adbd70e3e612d5e8d6a5f8`.
Fresh-Eye Satisfaction: worker-delivered; both terminal ledgers report findings-received and
approval_eligible. Tier application is host-defaulted, not independently confirmed.
Final wheel SHA256: `20f154ecd30e171799d5ea7fa3f4d827c76b7ecfb9d2b66c208db32b8db1553c`.
