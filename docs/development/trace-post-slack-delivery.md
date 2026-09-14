# Trace post requests and Slack delivery

Status: Implemented
Date: 2026-09-14
Owner: #169

## Problem and cause

A user asks what trace-post does, then asks it to create images. The reported thread instead
displayed an error directing them to status/approval/operator steps, repeated the previous answer
on status, and did not progress on the next execution request.

Three independent boundaries reproduced locally:

- First-use workspace members are admitted with `can_approve=False`. Requested execution checked
  that reviewer flag, so normal members could converse but could not execute delegated work.
- A failed reasoning call leaves the canonical Run at OBSERVE/RUNNING. Slack rejected new input
  for every RUNNING state, including this pre-invocation boundary. Its generic failure text added
  unnecessary operator/status instructions.
- The file adapter recognized only ordinary image-generation results. Trace post returns six
  registered asset references, and its asynchronous notification has no progress-message record.
  Both result resolution and notification-to-Run binding were missing.

The trace-post procedure and success metadata also still required human review. The ordinary-image
adapter added a mandatory review instruction to otherwise successfully delivered images.

The precise provider exception in the deployed report is unknown: production logs were not read.
The synthetic reasoning failure reproduces the stuck follow-up, not the original provider cause.

## Implemented behavior

The authenticated current request authorizes exposed workspace-member tools using run-creation
permission; `can_approve` remains relevant to reviewing a separate proposal. Source edits/deletes,
disable/revocation, private scope, exact invocation and execution budgets remain enforced.

New input may revise interrupted reasoning only when the last committed step is OBSERVE and the
service execution lock is held. Interrupted PLAN/APPROVE/EXECUTE and uncertain effects retain
their canonical recovery boundaries. No automatic provider or effect retry was added.

Trace post completion binds the outbox message to its original Run. The result adapter checks its
successful receipt, exact asset revisions, same-Run links, tenant and bytes before uploading six
named PNG files to the requesting thread. Country captions accompany the files. The final reply
comes from actual attachment outcomes instead of an earlier model guess about file availability.
Uploads are admitted durably before network effects and are not repeated after uncertain outcomes.

Human feedback is optional. New operations emit `human_review_required=False` without invented
human QA. In-flight old operations retain a True marker only when their frozen output schema
requires it; this historical metadata never blocks delivery. Other-channel delivery and public
posting still require a request for that destination.

## Verification

Baseline main is `718052e`. Its isolated installed modules for Slack approval, image delivery and
Trace post matched main byte-for-byte. The new first-use and failed-reasoning regressions both
failed against that installation with zero generation calls. Before repair, the signed Trace post
completion regression also finished its worker but made zero Slack upload calls.

The focused source selection in [testing.md](testing.md) plus
`tests/providers/test_codex_reasoning.py` passed **126 tests**. It includes first-use requests,
interrupted dispatch exclusion, six filenames/captions, old output-schema compatibility, tampered
bytes, absent Run links, disabled members, ambiguous upload responses and restart deduplication.
No full local suite was run. Scoped BasedPyright reported zero errors. Ruff on the changed files
has zero new findings relative to main; 41 pre-existing findings remain in those files.
The same **126 tests passed** from outside the checkout against the final non-editable wheel
in a new Python 3.14.7 environment with frozen runtime dependencies and pytest 8.4.2.
`trace-marketing version --json` returned 0.7.1; `service doctor` returned `ready: true`.

Actual-model comparisons used `gpt-6-astra` with `너 trace-post 스킬에 대해 뭔지 알고있어?`
followed by `그 스킬 이용해서 이미지 만들어줘`, a new workspace
member, real signed Slack ingress and the real deferred worker. PNG generation and all Slack
network operations used synthetic adapters; model calls used the official Codex CLI.

- Baseline: four model decisions, then a review/hash approval request; no worker call or upload.
- Initial candidate: execution and six uploads succeeded, but the pre-upload model answer wrongly
  claimed files were unavailable. This exposed a model/transport ownership mismatch.
- Corrected candidate: five model decisions, one worker execution and six complete upload flows
  (18 captured HTTP requests). Final output contained six named attachments and country captions,
  with no approval question or contradictory file-availability claim.

These are single-scenario observations, not a model quality benchmark. Fixture captions/PNGs are
not production creative quality evidence. Real Slack preview/download and deployed activation
remain post-merge observations, separate from the tested attachment transport contract.

The local proof bundle is `/private/tmp/trace-post-request-proof`: `model_check.py`,
`baseline-model-network/result.json`, `candidate-model/result.json`, `final-model/result.json`,
`ruff-delta.json`, wheels and isolated installations. It contains synthetic conversation state.

## Review and rollout

Review request admission and interrupted-reasoning recovery first, then the Trace post output
contract, then receipt/asset projection and asynchronous Slack delivery. The tests exercise both
source and final-consumer boundaries; passing worker validation alone was insufficient.

No environment-variable change, database migration, version bump or release tag is required.
The existing main verification and installed updater remain the activation path. Merge and
installation are separate observations. Rollback uses the existing installed update mechanism;
canonical histories, artifact bytes and upload deduplication rows are retained.
