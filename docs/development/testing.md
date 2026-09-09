# Testing and Verification

Status: Active
Last reviewed: 2026-09-08

## Focused checks

Choose the boundary that changed. Source tests are not installed-service or live-provider proof.

### Package-boundary migration

Source owners move to `agent/{core,service,runtime.py}`, `channels/http`, `tools`, `bootstrap` and
the named domain packages. Test directories remain `tests/agent_core` and `tests/marketing`; their
historical directory names do not authorize imports from `ads_booster.marketing`.

This package-only relocation does not require product QA. If a later behavior change needs boundary
verification, select the package-boundary scanner with the affected registry, service, channel and CLI
compatibility owners. The scanner should reject forbidden `agent.core` or `knowledge` dependencies and
stale executable imports while allowing the documented contract dependencies. An optional fresh-wheel
check can then verify that `ads_booster.agent.runtime` imports while `ads_booster.marketing` is absent.
Classify descriptor digest and persisted-ID strings separately from executable imports; legacy
`marketing` identifiers are not a failure by themselves. Preserve recorded historical test counts as
historical evidence rather than treating them as verification of this migration.

### Discoverable marketing procedures and Slack follow-ups

Select these tests for `skill_tools.py`, the skill catalog, discovery wiring, generic reasoning
guidance or current Slack dialogue projection:

```bash
python -m pytest -q -p no:cacheprovider --tb=short \
  tests/marketing/agent_service/test_skill_tools.py \
  tests/marketing/agent_service/test_integrations.py \
  tests/marketing/agent_service/test_creative_procedures.py \
  tests/marketing/agent_service/test_daily_slack.py \
  tests/marketing/agent_service/test_http_api.py \
  tests/marketing/channels/test_slack_continuation.py \
  tests/marketing/channels/test_slack_events.py \
  tests/providers/test_codex_reasoning.py
```

Use a dependency-complete development environment. The September 8 candidate passed 66 tests in
an isolated `uv sync --frozen` environment. The new signed Slack regression failed before the
dialogue fix, then passed with service restart and cross-thread exclusion. Skill tests cover
canonical list/read receipts, exact versions, recoverable unknown IDs and unchanged action policy.
Run scoped Ruff and BasedPyright for modified Python files; no full-suite run is required.

For actual model behavior, install a fresh wheel outside the checkout, confirm its import path,
and invoke the opt-in harness with that installed interpreter:

```bash
/absolute/fresh-venv/bin/trace-marketing service run --help
/absolute/fresh-venv/bin/python -I \
  /absolute/repo/tests/marketing/agent_service/colleague_canary.py \
  --output-root /absolute/new-rehearsal-directory \
  --codex /absolute/path/to/codex --model gpt-6-astra
```

The output directory must not exist. This runs four real-model scenarios with synthetic search
and only skill discovery, creative preparation and search adapters. It does not use real Slack,
image generation, Appium or publication. Review the emitted decisions against each scenario's
recorded criteria: concrete copy, attributed opportunity analysis, confound-aware performance
comparison and search execution after creative procedure loading. `completed` is a runtime state,
not a quality grade. Retain actual provider receipts and disclose model/trial count and fixture
limitations. Do not turn prompt-substring assertions into claims of model competence.

For latest-request projection, include `test_work_continuation.py`, `test_application.py`,
`test_application_deferred.py` and `test_knowledge_context_continuity.py` in the service directory.
For DM read wiring, include `tests/knowledge/test_installed_service_context.py` and
`test_slack_continuity_binding.py`; existing actor/session authorization remains the owner.
For conversational output, select channel `test_slack_result_link.py`, `test_slack_events.py`,
`test_slack_progress.py` and `test_slack_run_notifications.py`. Verify ordinary body brevity,
explicit status readback, asynchronous completion and private-link exclusion separately.

Run the opt-in `tests/marketing/agent_service/slack_colleague_canary.py` with the same installed
interpreter and `--output-root`, `--codex`, `--model` arguments shown above. It sends six synthetic
signed events through one persistent thread, rebuilds the installed composition between turns,
captures Slack sends locally and uses synthetic search. Review the saved dialogue and actual tool
intents, including subject changes and the one-sentence request. This is not production Slack QA.
The earlier PR candidate already avoided repeating its first answer in one comparison; do not
claim the supplied deployed failure was reproduced or statistically eliminated by this rehearsal.

For acknowledged asynchronous tool work, select `tests/marketing/test_runtime_deferred.py`,
`tests/marketing/agent_service/test_application_deferred.py` and
`tests/marketing/channels/test_slack_deferred.py`. Include existing `test_agent_runtime.py`,
Service `test_application.py` and `test_work_continuation.py` for their directly affected
receipt/recovery/steering behavior. These fake adapter tests exercise real SQLite admission,
approval expiry after admission, exact operation/executor/cost binding, duplicate completion,
acknowledgement/completion crash windows, explicit uncertainty, pending pause and signed Slack
follow-ups. They do not prove live provider or channel behavior.
Fresh wheel proof must also show that a second Run can answer while the first waits, and that
restarting and replaying completion neither calls the adapter again nor charges twice.
## Team knowledge checks

The knowledge implementation is covered by focused `tests/knowledge` contracts, repository/filesystem,
ingress, Slack-scope, retrieval, curation, transfer, and deletion tests. Select the affected boundary
with `uv run pytest -q <test-file>`; the focused owners are:

| Boundary | Test files under `tests/knowledge/` |
| --- | --- |
| Canonical API/Slack identity, session isolation and context preparation | `test_installed_service_context.py` |
| Extracted text, source evidence and authenticated user authority | `test_curation_inputs.py`, `test_source_memory_evidence.py`, `test_curation_user_authority.py` |
| First-event collection window, flush, receipts, cancellation and restart | `test_batch_runtime.py` |
| Private batch identity, current grants and exclusive-owner crash recovery | `test_batch_recovery.py`, `test_batch_startup.py`, `test_batch_policy_failures.py`, `test_private_batch_ingress.py` |
| Continuous processing after durable item failure without automatic replay | `test_ingress_runtime_failures.py` |
| Source HTTP success, redirect, 304 and retryable/terminal error classification | `test_source_fetch.py` |
| Brand registration replay, current authority and failed-session cleanup | `test_brand_cli.py` |
| Backup integrity, current erase-ledger restore and purge | `test_backup_restore.py`, `test_deletion.py` |
| Constraint transfer and current checks on validation replay | `test_transfer_material.py`, `test_transfer_validation_replay.py` |

The configuration seam must also be checked directly with synthetic absolute paths: all three
`TRACE_MARKETING_KNOWLEDGE_ROOT`, `TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT`, and
`TRACE_MARKETING_KNOWLEDGE_POLICY` values absent means disabled; any partial set must fail; relative
paths must fail; root/control permissions are `0700`; policy and `identity.json` are `0600`.
The service composition check covers `trace-marketing service run --help` and source inspection of
the enabled runtime, but a source checkout or local test does not prove a fresh installed service,
Codex entitlement, Slack delivery, remote deletion, or deployment.

The context-transfer checks must cover both required and disabled policies, exact SHA-256 binding,
workspace/account/run/task/action/brand matching, expiry, stale head/grant/tombstone rejection,
pre-dispatch and callback validation, and the callback use receipt. A cached acceptance must fail
after expiry, revocation, stale heads or tombstones; replay cannot bypass current checks. Slack checks
must cover shared workspace scope, private member/conversation scope, read-only DM capability filtering, pending edit/
delete/correction fences, and replayed outbox delivery. Deletion checks must assert the erase-ledger
sequence, local tombstones and reverse dependency blocks, clean restore, and remote replica
`purge_pending` until separately reconciled. Local purge must not execute or acknowledge
remote deletion without a configured transport. Restore checks include exact file digests,
private nested paths, mixed-memory redaction and a searchable surviving revision. Run
`tests/cli/test_server_onboarding.py` for fresh private knowledge-root creation and interrupted setup.
Its timezone-data regression clears the system search path and cache before setup, verifying that
the installed `tzdata` dependency supports initialization on minimal hosts.

The registered `trace-marketing knowledge` reference surface requires `--root`, `--control-root`,
and `--policy` on every command. Focused CLI checks should cover `init`, `doctor`, `ingest --envelope`,
`run --once`/`--until-idle --flush-batches`, `search --query`, `get --id`, `context --request`,
`backup --destination`, `restore --backup`, `retract --source`, `purge --request`,
`questions --pending`/`--answer --text`, and the memory/brand/task subgroups. `run` and
`memory consolidate` require Codex availability when they execute curation. These are source-level
reference operations until the installed `knowledge --help` and a fresh installation are checked;
do not claim operator usability or deployment proof from source alone.

### Shared feedback-learning checks

The authored regression owners cover the new boundaries without replacing the existing knowledge
checks:

| Boundary | Focused owner |
| --- | --- |
| Skill revisions, built-in protection, explicit overrides, CORE correction and task-only overlays | `tests/knowledge/test_procedural_skills.py`, `tests/knowledge/test_feedback_correction.py` |
| Workspace 10-turn/10-receipt readiness, durable watermarks, replay, source invalidation and grant partitions | `tests/knowledge/test_feedback_learning.py` |
| New-thread reuse, nonterminal correction, classifier-call bounds, silence and DM exclusion | `tests/marketing/channels/test_slack_learning.py` |
| Conflict-answer separation, built-in release fallback and unbound context/tool filtering | `tests/marketing/agent_service/test_feedback_learning.py` |

The shared 10/10 readiness rule is separate from the existing 60-second routine curation window.
The counter wakes one logical review round; the existing member, session, scope and grant partitions
still determine the actual curation batches. Terminal experience tests must use stored invocation
and receipt bindings, so a tool result cannot become a synthetic `ConversationEvent(role=USER)`. A
completed `no_effect` receipt counts as terminal work for readiness, but it is separate from effect
success. Reviewed complete observed evidence can support a reusable procedure; failed, unknown, and
invalidated outcomes cannot promote one. Actual experience evidence must retain the typed invocation
input, typed tool/provider output, receipt, and source/Run binding.
Selected skills must assert the nested `source_refs` and `source_revisions` inside each
`selected_skill_revisions` entry; generic retrieval references do not prove skill provenance.
The authored fixtures reconcile the additive schema expectation and the actual canonical
`ack`/`after_commit` seams. The reduced evidence covers the bounded repair, installed basic CLI/API
smoke, and one external installed minimal reuse canary; the expanded matrix remains unexecuted.

The original F1/F2 matrix remains excluded from the reduced release. F1 is the broader focused
behavior pass if that matrix is enabled later:

```bash
uv run pytest -q tests/knowledge/test_procedural_skills.py tests/knowledge/test_feedback_correction.py tests/knowledge/test_feedback_learning.py tests/marketing/channels/test_slack_learning.py tests/marketing/agent_service/test_feedback_learning.py tests/knowledge/test_changes.py tests/knowledge/test_curation_user_authority.py tests/knowledge/test_private_batch_ingress.py tests/knowledge/test_batch_recovery.py tests/knowledge/test_slack_continuity_binding.py tests/marketing/channels/test_slack_memory.py tests/marketing/channels/test_slack_progress.py
```

F2 is scoped static and documentation review. Pass only the final changed source/test files to
Ruff and BasedPyright, then check the approved documentation paths:

```bash
uv run ruff check <final changed source and test files>
uv run ruff format --check <final changed source and test files>
uv run basedpyright <final changed source and test files>
git diff --check -- README.md docs/architecture/system.md docs/architecture/code.md docs/development/testing.md docs/operations/agent-server/README.md tasks/todo.md
```

F3 builds and installs a fresh wheel, then runs the installed harness from outside the checkout. The
three absolute knowledge paths and fixture identity/policy must use a new isolated home. The sender
and provider are fixtures, while the installed HTTP router, signed ingress, service, and knowledge
runtime remain real:

```bash
uv build --wheel --out-dir .omo/evidence/agent-feedback-learning/dist
uv venv .omo/evidence/agent-feedback-learning/venv
uv pip install --python .omo/evidence/agent-feedback-learning/venv/bin/python <exact wheel path>
cd /absolute/outside/checkout
/absolute/checkout/.omo/evidence/agent-feedback-learning/venv/bin/python -I /absolute/checkout/tests/operations/installed_learning_smoke.py --mode fixture --home /absolute/new-learning-home --output /absolute/checkout/.omo/evidence/agent-feedback-learning/f3-installed.json
```

The reduced F4 canary uses a separately installed non-editable interpreter outside the checkout and
the minimal reuse scenario. Preserve the official `HOME`, `CODEX_HOME`, and logged-in Codex session.
Only the Slack sender is fake; the configured Codex path supplies the model. The old
`installed_learning_smoke.py --mode real-model` route is not the reduced F4 command:

```bash
/absolute/fresh-installed-venv/bin/python -I /absolute/checkout/tests/operations/installed_learning_model_canary.py --scenario minimal-skill-reuse --home /absolute/fresh-model-home --output /absolute/checkout/.omo/evidence/agent-feedback-learning/f4-model-reuse.json
```

The canary must record actual typed input/output provenance and distinguish terminal `no_effect`
completion from effect success. The reduced canary is bounded evidence for the core reuse path, not
general learning quality or the original full matrix. No local command establishes live Slack
delivery, a deployed server, or a marketing result.

## Source and installed-service checks

| Changed boundary | Focused owners |
| --- | --- |
| Canonical service, lifecycle and API | affected files in `tests/marketing/agent_service/`, `tests/agent_core/` and `tests/cli/test_cli_compatibility.py` |
| Runtime admission, receipts and recovery | `tests/marketing/test_agent_runtime.py`, `tests/marketing/test_runtime_deferred.py` |
| Signed Slack conversation and approval | affected files in `tests/marketing/channels/` |
| Codex reasoning and direct images | corresponding tests in `tests/providers/` and `tests/marketing/agent_service/test_image_generation.py` |
| Research collectors and evidence | `tests/marketing/test_dynamic_evidence_research.py` and the affected research contract tests |
| Server setup, Tunnel and updater | `tests/cli/test_server_onboarding.py`, `tests/cli/test_agent_server_update.py` |
| CLI surface | `tests/cli/test_cli_compatibility.py`; installed `trace-marketing --help`, `service --help`, `server --help`, `agent research --help` |

Select test files by changed behavior. Run scoped Ruff, formatter, BasedPyright and `git diff --check`.
Do not run the full suite or repository-wide static checks unless explicitly requested. Removed
Cloudflare, Mac/Appium and Threads tests have no current production owner and are not verification
requirements. Fresh installation must also show that removed CLI groups and worker routes are absent.

## Main agent web and Slack onboarding

For Linux CLI onboarding, run `tests/cli/test_server_onboarding.py`,
`tests/cli/test_agent_server_update.py` and `tests/cli/test_cli_compatibility.py`. Cover token validation
before writes, member/approver mapping, secret file permissions, preserved configuration/unowned units,
dedicated Tunnel token-file use, domain-specific manifest export, and bootstrap CI rejection.
Build and install a fresh wheel, then execute `server --help` and `server manifest` from that installed
CLI to prove data files ship. The Ubuntu job performs this installed manifest check. Mock systemctl
and Slack authentication do not establish live service startup or Slack acceptance. Public installer
success requires running the actual merged URL/ref on a fresh Linux environment; local candidate
bootstrap proof alone does not establish that claim.

Focused owners: tests/marketing/agent_service, tests/marketing/channels,
tests/providers/test_codex_reasoning.py, and tests/cli/test_cli_compatibility.py.
Run these tests together for login/channel/service composition changes, with scoped Ruff,
format checks and BasedPyright on changed source and tests. Do not run the repository-wide suite.

Prove browser-bound OAuth state, PKCE and CSRF rejection; signed real Slack form admission before
reasoning; app/team/channel/member denial; durable restart/dedupe and no notification retry after
an ambiguous send; one canonical asynchronous web Run; and Slack-only scheduled delivery without
Notion. Use an isolated installed candidate wheel for HTTP/browser QA. Fake provider/channel
success is not live OAuth, Codex, Slack, Cloudflare Tunnel, or Linux service evidence.
The operator's live completion checklist is in docs/operations/agent-server/README.md.

For Linux main updates also run `tests/cli/test_agent_server_update.py` together with the main-agent
onboarding owners above. Verify busy-work deferral without a stop, passive candidate failure with real
SQLite backup/restore, interrupted switch recovery, no rewind after activation, failed/pending CI
rejection, and old-main protocol rejection. `test_maintenance.py` covers concurrent drain accounting,
new-request refusal and signed Slack-only admission with web/API denial. Scoped Ruff and BasedPyright
include the standalone `docs/operations/agent-server/agent-manager.py`. Build a fresh wheel and install
it via the candidate manager; source success alone is not installed-product evidence. The Ubuntu CI
check `Verify on-prem agent` runs on every main push and is required by the updater. Faked systemctl
and health calls prove transaction behavior, not live Linux lifecycle or live GitHub automatic rollout.

That Ubuntu check also runs `tests/marketing/tool_adapters/test_compatibility.py`; unrelated
checks are not server admission requirements. The updater tests cover unrelated failed/pending checks
alongside rejection of missing, wrong-app, failed, skipped or incomplete required checks.
Slack conversations: `tests/marketing/channels/test_slack_events.py` proves signed challenge and
app/team admission, pre-reasoning ack, mention/message dedupe, thread continuation, same-Run input,
private context/tool/member separation, explicit hash/reviewer approval, close/reopen, removal before
work, unknown send no-retry and interruption recovery. Include it through the existing channels
selection in `Verify on-prem agent`. Fresh installed HTTP must verify the Events challenge and
maintenance rejection in Slack-only mode. Fixture provider/sender tests do not prove real Slack:
operator acceptance requires mention → thread follow-up, DM isolation, approval, restart, and a live
main SHA transition on Ubuntu as described in the server launch guide.

## Portable Ubuntu installation acceptance

Run focused `tests/cli/test_server_onboarding.py` and `tests/cli/test_agent_server_update.py` for
setup replay, preserved operator edits, doctor readiness, anonymous paginated CI reads and rollback.
`Verify on-prem agent` additionally builds `tests/operations/ubuntu-server.Dockerfile` for Ubuntu
22.04 and 24.04 on x86_64 and runs the real installer twice under a new unprivileged user.
`tests/operations/installed_server_lifecycle.py` runs using the installed interpreter outside the
checkout: real systemd start/restart, signed Slack URL challenge, fixture upstream main advance,
locked candidate installation, state backup, activation health and enabled update timer/linger.
Only Slack auth identity and upstream GitHub trust are fixtures; no Slack message or Codex inference
is sent. This is candidate/source installation proof. ARM assets are pinned but require separate
ARM host acceptance. The deployed default main URL and a real Slack conversation remain post-merge
acceptance, not inferred from source or mocked providers.

## Continuing small work acceptance (2026-09-07)

For this composition change, run the affected `tests/marketing/agent_service` and
`tests/marketing/channels` owners, `tests/providers/test_codex_reasoning.py`,
`tests/providers/test_codex_image_review.py`, existing Codex generation compatibility and
`tests/cli/test_cli_compatibility.py` and `tests/agent_core/test_contracts.py`; scoped
Ruff/format/BasedPyright and diff check. The original 2026-09-07 candidate passed 233 focused tests; this historical count is not
verification of subsequent changes.
Do not run the repository-wide suite. Focused new regression owners cover canonical context,
work continuation/interruption, creative asset/upload, memory/API, prepared delivery and
Slack image access.

Build a wheel and install it into a fresh isolated venv. From outside the checkout with
PYTHONPATH unset run `tests/operations/installed_work_continuity.py --checkout <checkout>
--output-dir <new-private-directory>` using that venv interpreter. It rejects editable/source
imports, exercises real installed HTTP/SQLite and signed events with fixture provider/sender,
reconstructs a waiting service, and writes an evidence packet plus synthetic image. A/B/C
prove input/locale/constraint continuity, not actual image edit or localization quality.

Separately run installed CLI help/version and a real `service run --home <isolated-root>`
loopback start/health/stop/restart. This does not establish Ubuntu systemd/linger/timer or
public installer acceptance. Preserve the existing installation and server onboarding.

Visual evidence must name the actual viewed image and actual provider response. Current
Mac candidate vision was exercised with a synthetic low-contrast calendar (gpt-6-astra);
model findings are not human final approval, actual Trace capture, editing, or marketing lift.
Live Slack requires optional files:read grant/reinstallation and permission probe; no test
may infer this from fake HTTP. Existing deployed app manifests are not edited by these tests.

Approved legacy-memory learning guards select `tests/knowledge/test_legacy_memory_guard.py` with
`tests/knowledge/test_feedback_correction.py`. These use the real SQLite review and selection owner,
exercise foreground and background trusted bindings, require exhaustive note assessments, and check
conflict hold, stale-question filtering, current continuation actor scope, task-only isolation, and
unchanged legacy notes. They do not call a model or treat fixture judgments as semantic-quality proof.

Human-effort changes use `test_work_observations.py`, `test_slack_work_observations.py`,
memory/API and Slack continuation/event tests. Cover correction/restart totals, author/reviewer
scope, source-derived learning invalidation before approval and receipt selection, and report
time versus measured duration. Slack asset intake changes select `test_slack_image_files.py`, `test_slack_image_review.py`,
`test_slack_asset_intake.py`, `test_slack_asset_intake_flow.py`, `test_slack_asset_link_failure.py`
and `test_slack_creative_setup.py`.
The flow uses actual service/SQLite/approval owners with fake HTTP/reasoning, from trusted
signed-file binding through inspection, approval wait/restart, exact import and same-Run output.
It is not signed live Slack transport or image quality proof. Asset projection changes also
select `test_creative_api.py` for registration/link failure and
bounded Web readback of registered files above the inline upload limit.

Performance reporting selects `test_performance_observations.py`, `test_performance_memory.py`,
`test_performance_api.py` and `tests/marketing/channels/test_slack_performance.py`. These exercise
actual scoped SQLite owners, signed-event intake, authenticated readback, correction/restart,
latest-report ordering, mismatched observation windows and source invalidation before memory
review/selection. They use human-reported fixture metrics; no live analytics or causal effect
is established. Run only directly affected memory/Slack/API checks after subsequent changes.

Bounded image production selects `tests/providers/test_codex_image_edit.py`,
`test_creative_image_edit_contract.py`, `test_creative_image_edit.py`, `test_image_edit_setup.py`
in the service test directory, and `tests/cli/test_image_edit_lifecycle.py`. Cover exact preserved
pixels, changed source/approval/readiness, no regeneration after unknown execution, interrupted
uncertainty and completion projection, source/output root containment, private production denial,
provider protocol/tool-inventory rejection and maintenance/shutdown behavior. Fake image pixels
do not establish translation or visual quality. A live proof must open the original and final
image and retain generation/provenance evidence; capability discovery is insufficient.

Managed review selects `test_managed_image_review.py`, affected `test_creative_procedures.py`
and lifecycle tests. Same-Run generated PNG review exercises actual source copying/decoding
with fixture inference, preserving pending human QA. Asset discovery/readback and Web changes
select `test_creative_asset_listing.py`, `test_creative_api.py` and `test_web_performance.py`;
the Node harness fences delayed Run/image/metric responses and untrusted markup. Browser fixture
rendering is separate from live service/provider evidence. Slack result navigation and natural
assent select `test_slack_result_link.py`, `test_slack_production_approval.py` and affected
event/command tests, including full-page delivery, current membership and stale targets.

`test_image_edit_api.py` and the coordinator's abandonment regression verify current reviewer
authority, exact uncertain target, durable human-reported abandonment, cost retention and
projection-only retry. They do not confirm what the external provider executed. September8
actual Codex0.153.4 readiness fails restricted thread setup (error32603), so no live edit output
or localization quality is claimed. The initial failed generation attempt is not retried.

September 8 final continuation selection: 142 passed across the 18 files below. Run from the
worktree with `.venv/bin/python -m pytest -q <selected files> -p no:cacheprovider --tb=short`;
`PYTHONPATH=src` is source evidence only. Do not expand to the whole suite.

```text
tests/cli/test_image_edit_lifecycle.py
tests/marketing/agent_service/test_creative_image_edit_contract.py
tests/providers/test_codex_image_edit.py
tests/marketing/agent_service/test_creative_image_edit.py
tests/marketing/agent_service/test_image_edit_setup.py
tests/marketing/agent_service/test_image_edit_api.py
tests/marketing/agent_service/test_managed_image_review.py
tests/marketing/agent_service/test_creative_procedures.py
tests/marketing/agent_service/test_performance_observations.py
tests/marketing/agent_service/test_performance_memory.py
tests/marketing/agent_service/test_performance_api.py
tests/marketing/channels/test_slack_performance.py
tests/marketing/channels/test_slack_production_approval.py
tests/marketing/channels/test_slack_result_link.py
tests/marketing/channels/test_slack_events.py
tests/marketing/channels/test_slack_commands.py
tests/marketing/agent_service/test_creative_asset_listing.py
tests/marketing/agent_service/test_web_performance.py
```

Ruff check/format and BasedPyright passed for the 37 Python files changed in this continuation
(relative to f3de492, including newly added files). Two final formatting/unused-result findings
were corrected and their two files rechecked without repeating unrelated tests.

Fresh installed evidence: local wheel 0.4.21, SHA256
`8fd386e3ab32a2d74cfafc285b95401b446bd7c4ba8869f44bcb777b9a5b9092`, installed in a separate
venv and run outside the checkout with PYTHONPATH unset. All 21 changed production file hashes
match installed bytes. Installed `version` and `--help`, real loopback `service run` health,
default-disabled/explicit-enabled image catalog, SIGINT listener shutdown and three starts
with the same Run/performance state passed. Standalone installed owners exercised real SQLite,
PNG composition and managed source copying with explicit fake reasoning/image assessment:
2,048 original pixels preserved, one generation and one review call after replay, 2,204 pixels
preserved outside a localized rectangle, and corrected performance learning excluded. The opened
composed fixture is a tiny white synthetic image, not a translation or visual-quality result.
Evidence and reproducible standalone harness are retained locally under
`/private/tmp/trace-image-performance-installed-proof/` (`proof.py`, `evidence.json`,
`installed-source-manifest.json`). This is a local wheel proof, not public installer,
service-manager, real device, real Slack or operational image-generation acceptance.

Port selection uses `tests/cli/test_agent_server_update.py`: default/missing port, explicit 8090,
custom port, invalid values, and agreement across actual launch argv, update health and CLI status.
The installed Ubuntu lifecycle fixture occupies 8765 with an unrelated HTTP service, starts the
agent on 8090, performs real systemd update/restart and verifies the unrelated service and persistent
port survive. This does not prove the live on-prem port migration or Cloudflare route change.

## GitHub issues from Slack

Focused owners: `tests/marketing/agent_service/test_github_issues.py`,
`tests/marketing/channels/test_slack_github_issues.py`, and `tests/cli/test_github_setup.py`.
For composition changes include the existing service/channels/provider/CLI selections above.
The server CI includes these owners. Assert fixed repository and exact approved payload, no calls
before approval, creation plus readback, receipt-backed URL rendering, duplicate Slack delivery,
uncertain write no-retry after restart, private-DM denial, safe credential storage and secret-free
failure output. Reuse existing member/approver/hash/recovery tests instead of duplicating those rules.
Run the focused owners against a newly built non-editable installed wheel outside the checkout with
pytest's source pythonpath disabled. This proves installed composition with fixture GitHub/Slack and
reasoning transports, not a live GitHub write or on-prem credential configuration. Operator acceptance
requires one authorized real issue, readback URL and Slack reply after configuring the server token.

Slack progress/cancellation: `tests/marketing/channels/test_slack_progress.py` covers status replacement,
heartbeat/final ordering, signed immediate stop during active reasoning and maintenance, exact message
scope, non-owner denial, duplicate old buttons, restart and externally completed/uncertain issue
requests. `tests/providers/test_execution_control.py` runs a real sleeping child through the official
Codex adapter seam, proves cancellation/reaping and retained timeout behavior. Include existing
service/channels tests and `tests/providers/test_codex_cli_generation.py` for structured subprocess
compatibility. Server CI includes the cancellation subprocess tests. Repeat these focused cases with
a freshly installed wheel outside the checkout. Fixtures do not prove real Slack interactivity;
operator acceptance requires enabling the callback URL, observing a live stage update, stopping a
running answer and successfully starting another request.

Image drafts: `tests/marketing/agent_service/test_image_generation.py` owns artifact/thread binding
and `tests/marketing/channels/test_slack_images.py` exercises signed mention -> exact approval
-> configured image tool -> PNG verification -> original-thread file attachment. It also covers DM
denial, invalid output, path/symlink/digest rejection, upload URL origin and credential isolation,
uncertain completion and restart deduplication. Run with service/channels, Codex reasoning/cancellation
and onboarding owners; repeat the image/progress/process cases against a fresh non-editable wheel.
Fixture PNGs and HTTP transports do not establish live image entitlement or Slack upload permission.
Operator acceptance uses the server's actual login, one approved image brief and a visible draft in
its originating thread after adding files:write and reinstalling the Slack app. A human reviews the
result's visual correctness before use.
## Work continuity and knowledge integration

New combined regressions select `tests/knowledge/test_slack_continuity_binding.py` and
`tests/marketing/agent_service/test_knowledge_context_continuity.py`, plus affected image-edit
coordinator files. They cover queued messages and execution aliases, current actor
scope, original history retention, revoked prepared knowledge, follow-up retrieval, and knowledge
change between approval and actual start. CLI compatibility additionally proves doctor creates no
state. Repeat only an affected owner after a further fix; use the final GitHub head's Ubuntu
checks as installed CI evidence before merge.

Workspace-wide mention admission is covered by `tests/marketing/channels/test_slack_events.py`:
installed composition, new users/channels, distinct identity, preserved approver rights, rejected
foreign/shared workspaces, disabled/revoked users and non-approver effects. Run this owner together
with Slack progress and installed knowledge ingress checks; actual multi-user Slack delivery
requires live workspace verification.
