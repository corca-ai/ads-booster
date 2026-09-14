# Conversational task approval

Status: Implemented; verified in isolated candidate installations
Owner: #169

## Contract

An admitted shared Slack user with current execution permission delegates work in their current
message. The provider cites that complete request for each necessary invocation; the channel
binds it to the authenticated, unchanged source and exact operation through the existing service.
All exposed workspace-member tools share this policy. Requested successive steps continue within
the run budget with fresh source/membership checks, without a second approval/review conversation.
Questions, quoted requests, negation, draft-only requests and unrequested actions do not delegate
execution. Creation alone grants no publication or spending; those require their own explicit
scope in the request. Private DMs remain read-only; uncertain effects are not retried.
Generated images are delivered results; human feedback is optional, and provenance never falsely
claims human review.

Conversation and execution completion are independent: answering a question while a tool
awaits approval preserves the pending invocation. Read-only tools can help answer without
replacing it. A changed plan or explicit cancellation can supersede it; stale hashes remain
invalid. The pending projection is derived from canonical records and survives restart.
Slack renders a readable action summary and accepts plain assent bound to that exact
delivered proposal. Raw invocation review remains available for inspection.
The delivered digest is frozen at message admission, so an assent queued before delivery
cannot approve a later proposal. Edited/deleted assent is rejected again at execution.
After approval, recovery resolves the EXECUTE step binding rather than the latest read call.

## Acceptance

Signed Slack tests must cover direct creation, questions while waiting, read tools, replacement,
cancellation, restart, duplicate events, stale hashes, revoked users, private scope and
uncertain results. Repeat focused tests against a non-editable installed wheel outside the
checkout. Use real-model synthetic Slack turns for direct, negative and ambiguous intent.
Do not claim deployed Slack or actual image/GitHub writes from fake adapters.

## Verification scope and review

The initial fresh-wheel selection passed 151 tests outside the checkout. Six actual-model scenarios
passed on both the second candidate and the subsequent prompt/output candidate: direct creation once;
no creation for capability questions, drafts, negation or quoted requests. The first rehearsal
exposed a command-prefix routing bug and an unrealistic image fixture; both were corrected,
and acceptance now checks final state as well as dispatch count. Model trials use synthetic
adapters; no deployed Slack or actual GitHub/image effect has been verified.

Independent authority and recovery reviews identified early queued assent, mutated assent and
execution recovery selecting an intervening read. These were reproduced or covered by focused
signed-event regressions and repaired before follow-up review. Direct delegation itself is the
requested policy, not a finding requiring reinstatement of redundant confirmation.

Standing delegation across unrelated messages and automatic recovery of uncertain effects are
outside this change. Semantic intent classification remains model-dependent; a small canary does
not prove universal language accuracy. The follow-up removes the one-operation restriction and
creation allowlist, and replaces mandatory generated-image review with optional feedback.

Follow-up installed verification: 39 focused tests passed on a fresh non-editable wheel outside
the checkout, including the multi-step request and between-step membership checks. The original
revocation fixture attempted an immutable identity insert; it now changes the persisted binding
to exercise revocation rather than an unrelated identity-conflict exception.

All six actual-model scenarios also passed on this follow-up installed candidate. Direct image
and issue requests completed without review/approval instructions in their replies. Effect
adapters remained synthetic; deployed Slack behavior is not claimed.
