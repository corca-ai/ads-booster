# Code Architecture

Status: Active
Last reviewed: 2026-09-01

## Composition

`ads_booster.cli.marketing` exports the sole CLI, `trace-marketing`. `worker run` composes a
`MarketingWorkerLoop` from D1 `WorkerBrokerClient`, SQLite `MarketingInbox`, and a planless hosted
executor that routes immutable tasks to `HostedWorkspaceCaptureExecutor`, hosted caption
generation, or a discriminated marketing judgment executor.

```text
cli/marketing
  -> marketing/worker_loop
     -> marketing/worker_broker       D1 lease, barrier, callback
     -> marketing/inbox               SQLite inbox and outbox
     -> marketing/native_capture      prepare/execute and final validation
        -> marketing/background       background intent/provenance
        -> capture/codex_appium_job   immutable v2 contract
        -> capture/calendar_automation_contract typed EventKit file contract
        -> capture/calendar_preparation post-barrier seed/verify/cleanup
        -> capture/calendar_lifecycle cleanup on every post-prepare exit
        -> capture/appium_codex       one job and native collector
           -> capture/appium_codex_validation paths and completion proof
           -> capture/appium_editor_verifier live UI and process-binding proof
        -> providers/codex_cli        official CLI subprocess
     -> marketing/hosted_judgment     source- and reference-bound strategy proposal
     -> marketing/hosted_creative_judgment
     -> marketing/hosted_candidate_judgment
     -> marketing/hosted_learning_judgment
        -> providers/codex_cli        distinct official structured-judgment subprocess receipts
     -> marketing/hosted_experiment_evaluation
                                      deterministic, no-model outcome evaluation
```

## Responsibility boundaries

| Area | Owns | Does not own |
| --- | --- | --- |
| `worker_broker.py` | D1 claim, acknowledgement, barrier, callback HTTP | SQLite/Appium |
| `inbox.py` | ingress, claim, admission, terminal result, callback retry | Cloudflare/Codex |
| `worker_loop.py` | prepare-barrier-execute ordering and ambiguity handling | Trace UI method |
| `background.py` | `background_intent`, allowlisted candidate ranking, digest, provenance | native export |
| `native_capture.py` | hosted payload/context, request paths, native preview PNG validation | UI selectors, image editing |
| `contracts/feedback.py` | strict `trace.feedback-context.v1` shape and canonical digest | rule promotion, review storage |
| `codex_appium_job.py` | v2 context/device/digest/nonce/time/calendar contract | process execution |
| `calendar_automation_contract.py` | typed prepare/cleanup request, result, and event time projection | EventKit execution, layout |
| `calendar_preparation.py` | post-barrier Trace helper launch, Calendar proof and request-owned cleanup | Trace layout, Calendar UI navigation |
| `calendar_lifecycle.py` | independent cleanup budget and primary/cleanup error preservation | EventKit execution |
| `appium_codex.py` | device lock, Calendar lifetime, contract file, one Codex result, native collection | UI reasoning |
| `appium_codex_validation.py` | request paths, result/Ready/Saved consistency, expected titles | process execution |
| `appium_editor_verifier.py` | live editor titles and Trace process launch-binding verification | UI navigation, export rendering |
| `codex_cli.py` | schema-constrained official CLI subprocess, bounded Ready-session recovery, and localhost-only permission profile | custom agent/auth/thread state, image generation |

`CodexAppiumJobContract` uses `trace.codex-appium-job.v2`. Its canonical digest covers identity,
marketing context, prepared background, device, locale/time zone, nonce, and calendar namespace.
The contract is mode 0600 in a mode-0700 request root. Codex runs without user/project configuration;
its commands can access the workspace and loopback Appium, but not home secrets or external hosts.
The worker starts the DEBUG Trace EventKit helper only after execution admission. Its helper launch
adds `-traceMarketingCalendarAutomation`; the final Codex editor keeps the original immutable launch
arguments. Codex reports UI completion and session close only. Worker-owned Calendar proof and
cleanup never enter Ready/Saved/result schemas. Only the worker collector proves the App Group export
and validates the manifest.

Cloudflare owns review-event binding, distinct-candidate rule promotion, override state, task
selection, and callback receipt comparison. Python validates the same feedback envelope, inserts
caption rules into the candidate instruction or carries image correction context in the Appium job,
and returns only the selected digest as its consumption receipt. Neither side changes generated
candidate fields or native artifact fields to transport feedback.

The package deliberately keeps no alternate execution runtime or legacy command compatibility.
`trace-agent` and `trace-ads` are migration-only names.

## Hosted Threads modules

| Module | Owns |
| --- | --- |
| `cloudflare/src/threads/config.js` | disabled/ready classification, all-or-none bindings, public config rendering contract |
| `cloudflare/src/threads/client.js` | pinned Graph operations and strict public client surface |
| `transport.js` / `responses.js` | bounded GET retry, no-retry POST, typed Graph parsing |
| `crypto.js` | versioned AES-GCM token encryption |
| `profiles-api.js` / `profiles-store.js` | account-scoped OAuth state, profile lifecycle, default and toggle CAS |
| `media-capability.js` | short-lived HMAC/digest/account/publication-bound private PNG fetch |
| `scheduling.js` | bounded due publication selection and per-row isolation |
| `publication.js` | quota, container, irreversible barrier, publish-once, post-ID readback |
| `engagement.js` | independent metric/reply polling, cursors, reauth/deletion, retention |
| `status-api.js` | public-safe status/metrics plus privileged replies and unknown resolution |

`render-config.mjs`, deployment health, and `index.js` share the same configuration owner. Disabled
deployments omit public Threads variables and skip publication and engagement tasks; partial
configuration fails before deployment or scheduling.

`hosted-workspace.js` owns candidate creation and dual human approval. It snapshots the default
profile at candidate creation and inserts the immutable publication decision in the same D1 batch as
accepted image review. `index.js` composes separate candidate-generation, publication, and engagement
schedulers. Threads modules do not import or extend Mac worker task kinds or the generic `/v1`
adapter; the sibling marketing-agent route delegates no-effect judgment tasks to that broker.

## Marketing-agent contracts

`ads_booster.contracts.marketing_agent` owns the Python source contract for feature evidence,
strategy portfolios, registered outcomes, and frozen context receipts. D1 migration
`0017_marketing_agent_foundation.sql` owns the new `agent_v1` persistence epoch and its shadow
no-tool-action guard. `marketing/hosted_judgment.py` owns validation, private workspace admission,
the schema-constrained strategy turn, claim/reference quarantine, and the bound result. Cloudflare
`marketing-agent.js` owns campaign ingestion and task creation;
`hosted-marketing-judgment-callback.js` independently validates and atomically persists successful
strategy state. The generic worker broker owns leasing and callback transport only.

`marketing/hosted_creative_judgment.py` owns the proof-first MediaPlan proposal and creates no tool
action. `marketing/hosted_candidate_judgment.py` materializes one approved, evidence-bound
candidate; `marketing/hosted_reference_research.py` returns an immutable quarantined observation
snapshot; and `marketing/hosted_learning_judgment.py` creates only a reversible learning candidate.
`marketing/hosted_experiment_evaluation.py` is deterministic and has no model or external effect.
Their Cloudflare callbacks independently validate every receipt, claim, plan, assignment, and
approval binding before writing a projection. `marketing-agent.js` owns assisted campaign gating,
variant links, product-event intake, scheduled evaluation dispatch, and learning approval.

Migrations `0018`–`0023` own the execution/observation lineage, assisted-shadow origin binding,
quarantined reference snapshots, and assignment-specific artifact proof. Existing candidate review,
native capture, and `threads/*` modules remain the only effect owners; marketing-agent code refers to them by immutable IDs rather than reimplementing them.

`0023_marketing_adapter_capabilities.sql` owns account-scoped adapter registrations and
context-receipt-scoped immutable capability bindings. It records descriptor/schema digests, owner,
effect class, enabled state, and whether a capability is active or reference-only; it does not add a
generic dispatcher or move capture/Threads effect ownership.

The legacy `MarketingWorkflow` / `MarketingAccountAgent` tables and Durable Object storage are not
the owner of new strategy state. Existing `hosted-workspace.js`, native capture modules, and
`threads/*` modules keep their present responsibilities and will be referenced through immutable
IDs and receipts rather than generalized or reimplemented.
