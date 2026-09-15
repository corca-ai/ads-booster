# Trace agent channel audit Debug
Date: 2026-09-15

## Problem
Slack requests stalled, repeated approval/status text, or withheld usable results.

## Correct Behavior
Authenticated requests retain authority across slices; independent work and late results coexist.
Verified artifacts are delivered without unsolicited approval. Conversation facts survive assessment.

## Observed Facts
Read 68 roots and 257 replies across 53 threads. Live lookup ended with
`tool_idempotency_conflict`; image request reported `awaiting_approval`. Actual model
rehearsals exposed invalid evidence handles and extra image-review requirements.

## Reproduction
Installed main passed 73 fixtures but failed actual-model probes. Failing-first regressions
covered resumed authority, retained notification, progress, identifier and repeated-read boundaries.

## Candidate Causes
- Production launcher failure: previous incident; current model replies disconfirm blanket outage.
- Lost request/context/state at boundaries: reproduced with forced slices and restart.
- Structured model output errors: reproduced opaque-handle confusion.
- Unsupported workflow motif: explains cloud request limitation, not ordinary lookup failures.

## Hypothesis
Single-slice authority and conflated completion/read identities cause false blocking.
Disconfirmer: preserve source authority while revoking membership; unauthorized execution must remain denied.

## Pattern Ladder
Slack ingress versus resumed drive; actor versus assessor references; foreground versus deferred
wait states all lost context crossing an owner boundary. Canonical source, proof handles and task
state now cross those seams explicitly. Tenant/effect deduplication remains a negative control.

## Verification
Installed candidate: 146 focused passes; main-integrated fresh wheel: 177 focused passes. Actual model: five completed dialogue tasks, one honest
integration question. Six actual PNGs passed repaired assessment and six local upload sequences.
Memory recall and real search were inspected separately; private receipts remain local.

## Root Cause
Authority ran only at ingress. Completion failure became a conversation gate. Assessor context and
opaque ID domains were underspecified; its prompt invented review requirements. Input-level read
identity blocked legitimate refresh. Waiting checkpoints retained obsolete approval reasons.

## Invariant Proof
Original effects are not replayed; admitted source and current actor are rechecked. Old completions
retain their own Run and thread. Artifact ownership/digests stay mandatory. Reference context cannot
grant tools or prove writes. Final consumers are captured Slack and actual model outputs.

## Detection Gap
Short scripted canaries stopped before durable slices drained. Fake assessors never confused
opaque IDs or imposed review. Drain the existing canary and inspect every actual answer.

## Sibling Search
cross-file: actor/assessor schemas, ingress/drive authority, service/task-result waiting state fixed
as one producer-to-consumer pattern. Same-channel private policy stays filtered. Retained JSON
requires rollback backup; no destructive migration. follow-up:186-production verifies live upload
and activation after an authorized merge; follow-up:186-latency measures provider duration separately.

## Seam Risk
- Interrupt ID: channel-audit-186
- Risk Class: external-seam, host-disproves-local
- Seam: installed model to canonical completion and Slack delivery
- Disproving Observation: passing scripted fixtures hid actual model failures
- What Local Reasoning Cannot Prove: production activation and Slack file readback
- Generalization Pressure: monitor

## Interrupt Decision
- Resolution: resolved
- Critique Required: yes
- Next Step: spec
- Handoff Artifact: charness-artifacts/spec/2026-09-15-channel-boundaries.md
Bounded contract implemented. Scoped critique is recorded beside this artifact; no merge authorized
for this new PR. Preserve the existing user-owned latest pointer.

## Prevention
Keep falsifying revoked-member, uncertain-effect, tenant-deduplication and digest checks. Scope
completion to requested work, verify installed packages and record live limits instead of claiming all green.

## Evidence Disposition
- Report Identity: slack:channel-audit-186#sha256:d17be4aa5a352132df105524c8ebedda0b7a2ff3ca00908f5a7d90010d74f105
- Reported Findings: 10
- Dispositioned Findings: F1, F2, F3, F4, F5, F6, F7, F8, F9, F10
- Missing Findings: none
- Evidence Digest: sha256:c8d54400f2def4a931765ca1c29051770ba968656a6aa867345c57e12c10a4b6
- Report Source: docs/development/slack-channel-audit-2026-09-15.md
- Report Source SHA256: d17be4aa5a352132df105524c8ebedda0b7a2ff3ca00908f5a7d90010d74f105

## Adversarial Verification
- Finding: F1 | Source: channel-audit-186 | Expected: Requested work executes after yielding | Stimulus: Signed request and restarted drive | Disposition: reproduced | Observed: Approval wait disappeared after repair | Proof: executable fixture | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F1.json | Receipt SHA256: fa2307359b4c0edf5651e8172fbd281a766a2ee92a73c8264f605a58dc14203b
- Finding: F2 | Source: channel-audit-186 | Expected: Follow-ups reach reasoning and retain old results | Stimulus: Blocked and uncertain Slack follow-ups | Disposition: reproduced | Observed: New task and original late notification both delivered | Proof: executable fixture | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F2.json | Receipt SHA256: 874bd43a4ca2919adee50ad04970c34991fb72cce3ef6f6f1a43936c96b285e9
- Finding: F3 | Source: channel-audit-186 | Expected: Scoped service capabilities remain visible | Stimulus: Captured reasoning requests | Disposition: reproduced | Observed: Configured identity and scoped inventory supplied without dispatch authority | Proof: executable fixture | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F3.json | Receipt SHA256: dfb7846712a973b21fc942e6ca435a54a6760a35c19dc3f7b9a96865e7df2401
- Finding: F4 | Source: channel-audit-186 | Expected: Elapsed time survives slices | Stimulus: Restarted Slack progress clock | Disposition: reproduced | Observed: 180 seconds displayed instead of zero | Proof: executable fixture | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F4.json | Receipt SHA256: 903d7f49354486621ce642de66a663740b4ee8825d8ac6f1816abaf9ac87b4d6
- Finding: F5 | Source: channel-audit-186 | Expected: Only canonical evidence handles are cited | Stimulus: Mutated and output digests | Disposition: reproduced | Observed: Invalid references rejected by both provider schemas | Proof: executable fixture | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F5.json | Receipt SHA256: 41887d050e404147e45585093cfbf5bcc86a17e367bb2302e245e8015d60b634
- Finding: F6 | Source: channel-audit-186 | Expected: Requested output is delivered without extra review | Stimulus: Actual model and six generated PNGs | Disposition: reproduced | Observed: Five dialogue answers completed and six file-upload sequences delivered locally | Proof: runtime/provider roundtrip | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F6.json | Receipt SHA256: 4ca51e5b8e78ab0e792653da1f2937398ac16bcf289886edf3f42f9fc03f64cc
- Finding: F7 | Source: channel-audit-186 | Expected: Already stored memory is acknowledged | Stimulus: Actual authenticated memory readback | Disposition: reproduced | Observed: New-thread recall passed and existing-memory assessment satisfied | Proof: runtime/provider roundtrip | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F7.json | Receipt SHA256: 5687cdb61fd8b6fd5b461261c1d96be58eb65a07f36a40611885458cea3edbc6
- Finding: F8 | Source: channel-audit-186 | Expected: Search limits and source language are explicit | Stimulus: Actual public Threads search | Disposition: disconfirmed | Observed: Two actual search-result URLs with Traditional Chinese and snippet-only caveats | Proof: runtime/provider roundtrip | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F8.json | Receipt SHA256: a4b8d479ee34ca654ddb9f38e35968b07d543c2cbe7aeb7de54b2728c65df5e1
- Finding: F9 | Source: channel-audit-186 | Expected: No unsupported scheduling promise | Stimulus: Actual model recurring-analytics question | Disposition: not-applicable | Observed: Missing integrations stated and no schedule invented | Proof: runtime/provider roundtrip | Handoff: debug-186 | Next move: review PR | Receipt: none | Receipt SHA256: none
- Finding: F10 | Source: channel-audit-186 | Expected: Fresh reads can repeat identical input | Stimulus: Two admitted observations in one Run | Disposition: reproduced | Observed: Blocked before repair and completed with two reads afterward | Proof: executable fixture | Handoff: debug-186 | Next move: review PR | Receipt: charness-artifacts/debug/2026-09-15-trace-agent-channel-audit/F10.json | Receipt SHA256: ac3bf99b120a24d9e9827d235199a072afce4f7c393d910b3a6e728787155b2e
