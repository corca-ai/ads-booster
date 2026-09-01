# Testing and Verification

Status: Active
Last reviewed: 2026-09-01

## Focused checks

Choose the boundary that changed. Source tests are not installed-worker or hosted-runtime proof.

| Change | Command |
| --- | --- |
| marketing-agent feature/strategy contracts and P0 D1 foundation | `uv run pytest tests/marketing/test_marketing_agent_contracts.py tests/marketing/test_cloudflare_schema.py -q` |
| provider-neutral marketing runtime dispatch/receipt/session persistence | `uv run pytest tests/marketing/test_agent_runtime.py -q`; `uv run ruff check src/ads_booster/marketing/runtime.py tests/marketing/test_agent_runtime.py`; `uv run ruff format --check src/ads_booster/marketing/runtime.py tests/marketing/test_agent_runtime.py`; `uv run basedpyright src/ads_booster/marketing/runtime.py tests/marketing/test_agent_runtime.py`; `git diff --check` |
| shadow strategist, official Codex receipt, broker, and hosted callback | `uv run pytest tests/marketing/test_hosted_judgment.py tests/providers/test_codex_cli_generation.py tests/marketing/test_worker_loop.py tests/marketing/test_worker_broker.py -q`; `node --test cloudflare/test/hosted-marketing-agent.test.js cloudflare/test/mac-workers.test.js` |
| proof-first creative plan, execution ledger, conservative evaluation | `uv run pytest tests/marketing/test_hosted_creative_judgment.py tests/marketing/test_hosted_supervised_judgments.py tests/marketing/test_experiment_evaluation.py tests/marketing/test_cloudflare_schema.py -q`; `node --test cloudflare/test/hosted-creative-plan.test.js cloudflare/test/marketing-agent-supervised-runtime.test.js` |
| quarantined reference research and assisted marketing-agent runtime | `uv run pytest tests/marketing/test_hosted_reference_research.py tests/marketing/test_hosted_supervised_judgments.py -q`; `node --test cloudflare/test/marketing-agent-reference-runtime.test.js cloudflare/test/marketing-agent-supervised-runtime.test.js` |
| deterministic Calendar prepare/cleanup | `uv run pytest tests/capture/test_calendar_preparation.py tests/capture/test_codex_appium_capture.py tests/capture/test_codex_appium_handshake.py tests/providers/test_codex_cli_handshake.py` |
| v2 job and Appium adapter | `uv run pytest tests/capture/test_codex_appium_capture.py tests/capture/test_codex_appium_handshake.py tests/capture/test_appium_editor_verifier.py tests/providers/test_codex_cli_handshake.py tests/capture/test_appium_endpoint.py tests/capture/test_readiness.py` |
| background search and native validation | `uv run pytest tests/search/test_web_image_search_providers.py tests/search/test_background_fetcher.py tests/marketing/test_background.py tests/marketing/test_native_capture.py` |
| hosted Codex candidate generation | `uv run pytest tests/marketing/test_hosted_generation.py tests/providers/test_codex_cli_generation.py` |
| Threads D1 contract | `uv run pytest tests/marketing/test_cloudflare_schema.py -q` |
| Threads Graph/profile/scheduling/publication/engagement | `node --test cloudflare/test/threads-client.test.js cloudflare/test/hosted-threads-profiles.test.js cloudflare/test/hosted-threads-candidates.test.js cloudflare/test/threads-scheduling.test.js cloudflare/test/threads-publication.test.js cloudflare/test/threads-engagement.test.js` |
| optional Threads deployment config | `node --test cloudflare/test/threads-config.test.js cloudflare/test/deployment-health.test.js cloudflare/test/threads-security.test.js`; then render once with all `THREADS_*` values absent and require no Threads key plus `threads_ready: false` |
| Threads workspace/status/security | `npm --prefix cloudflare run build && node --test cloudflare/test/workspace-static.test.js cloudflare/test/hosted-threads-ui-api.test.js cloudflare/test/threads-security.test.js` |
| hosted feedback selection, worker receipt, and schema | `uv run pytest tests/marketing/test_hosted_generation.py tests/marketing/test_native_capture.py tests/marketing/test_worker_broker.py tests/marketing/test_cloudflare_schema.py`; from `cloudflare/`: `node --test test/hosted-workspace.test.js test/hosted-generation.test.js test/mac-workers.test.js test/hosted-capture-result.test.js` |
| native Trace lock-screen preview passthrough | `uv run pytest tests/marketing/test_native_capture.py tests/capture/test_codex_appium_handshake.py` |
| inbox/barrier/recovery | `uv run pytest tests/marketing/test_worker_loop.py` |
| update and installation guard | `uv run pytest tests/marketing/test_worker_update.py tests/cli/test_installer.py` |
| CLI surface | `uv run pytest tests/cli/test_cli_compatibility.py`; `uv run trace-marketing --help`; `uv run trace-marketing worker --help` |

For changed Python paths, run the matching scoped Ruff, formatter, BasedPyright, and
`git diff --check`. Do not run the full suite or repository-wide static checks unless requested.
The Trace checkout additionally runs `TraceTests/MarketingCalendarAutomationTests` on the selected
iPhone Simulator. A source parse or Python fake does not prove EventKit authorization or data flow.

## Installed-worker proof

Use the managed executable, not `uv run`:

```bash
"$HOME/.local/share/trace-marketing/current/bin/trace-marketing" version --json
"$HOME/.local/share/trace-marketing/current/bin/trace-marketing" worker doctor
"$HOME/.local/share/trace-marketing/current/bin/trace-marketing" worker status
```

For a real job record task ID, callback receipt, PNG path/SHA-256, native manifest, and resulting
`image_awaiting_review` state. `doctor` proves prerequisites only. A manifest proves bindings
only. Human review alone passes visual correctness.

A regression test for a post-barrier defect must assert `unknown_side_effect` and no automatic
native re-execution. Never place credentials, raw Codex output, or private user data in evidence.

Threads source proof uses injected fake Graph responses and a real local D1 migration chain. Browser
QA uses the built workspace at 1440x900, 1024x768, and 390x844 and verifies token-memory lock,
profile/default/toggle, candidate pinning, publication states, metrics, privileged replies, keyboard
focus, overflow, and console errors. It is not live Meta proof. A production-readiness claim also
requires explicit authorization for a non-production Meta test profile, authoritative post-ID and
permalink readback, then at least one metric snapshot and top-level reply. Never record tokens,
authorization codes, OAuth states, or unexpired signed media URLs in test artifacts.
