# Code Architecture

Status: Active
Last reviewed: 2026-08-28

## Composition

`ads_booster.cli.marketing` exports the sole CLI, `trace-marketing`. `worker run` composes a
`MarketingWorkerLoop` from D1 `WorkerBrokerClient`, SQLite `MarketingInbox`, and
`HostedWorkspaceCaptureExecutor`.

```text
cli/marketing
  -> marketing/worker_loop
     -> marketing/worker_broker       D1 lease, barrier, callback
     -> marketing/inbox               SQLite inbox and outbox
     -> marketing/native_capture      prepare/execute and final validation
        -> marketing/background       background intent/provenance
        -> capture/codex_appium_job   immutable v2 contract
        -> capture/appium_codex       one job and native collector
        -> providers/codex_cli        official CLI subprocess
```

## Responsibility boundaries

| Area | Owns | Does not own |
| --- | --- | --- |
| `worker_broker.py` | D1 claim, acknowledgement, barrier, callback HTTP | SQLite/Appium |
| `inbox.py` | ingress, claim, admission, terminal result, callback retry | Cloudflare/Codex |
| `worker_loop.py` | prepare-barrier-execute ordering and ambiguity handling | Trace UI method |
| `background.py` | `background_intent`, digest, provenance | native export |
| `native_capture.py` | hosted payload/context, request paths, PNG/manifest validation | UI selectors |
| `codex_appium_job.py` | v2 context/device/digest/nonce/time/calendar contract | process execution |
| `appium_codex.py` | device lock, contract file, one Codex result, native collection | UI reasoning |
| `codex_cli.py` | schema-constrained official CLI subprocess and localhost-only permission profile | custom agent/auth/thread state |

`CodexAppiumJobContract` uses `trace.codex-appium-job.v2`. Its canonical digest covers identity,
marketing context, prepared background, device, locale/time zone, nonce, and calendar namespace.
The contract is mode 0600 in a mode-0700 request root. Codex runs without user/project configuration;
its commands can access the workspace and loopback Appium, but not home secrets or external hosts.
The result reports UI completion, namespaced cleanup, and session close. Only the worker collector
proves the App Group export and validates the manifest.

The package deliberately keeps no alternate execution runtime or legacy command compatibility.
`trace-agent` and `trace-ads` are migration-only names.
