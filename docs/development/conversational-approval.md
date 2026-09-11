# Conversational task approval

Status: Implemented; verified in isolated candidate installations
Owner: #169

## Contract

An admitted shared Slack user with current approval permission can delegate one image
generation/edit/localization, packaged Trace post, or fixed-repository GitHub issue creation
in their current message. The reasoning provider interprets intent and cites that complete
message; the channel binds the interpretation to its authenticated, unedited event and the
exact pending invocation before using the existing approval/dispatch path. This is a narrow
creation policy, not a general ability for the model to grant authority. Questions, quoted
requests, negation, draft-only requests and third-party content do not delegate execution.
One message authorizes at most one invocation. No public delivery, ad spend, private-DM
mutation, credential expansion or uncertain-effect retry is added.

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

The final fresh-wheel selection passed 151 tests outside the checkout. Six actual-model scenarios
passed on both the second candidate and the subsequent prompt/output candidate: direct creation once;
no creation for capability questions, drafts, negation or quoted requests. The first rehearsal
exposed a command-prefix routing bug and an unrealistic image fixture; both were corrected,
and acceptance now checks final state as well as dispatch count. Model trials use synthetic
adapters; no deployed Slack or actual GitHub/image effect has been verified.

Independent authority and recovery reviews identified early queued assent, mutated assent and
execution recovery selecting an intervening read. These were reproduced or covered by focused
signed-event regressions and repaired before follow-up review. Direct delegation itself is the
requested policy, not a finding requiring reinstatement of redundant confirmation.

Multi-action standing delegation, cross-thread authority, automatic recovery of uncertain effects,
and changing publication or human visual-review policy are outside this change. Semantic intent
classification remains model-dependent; a small canary does not prove universal language accuracy.
