# Trace Marketing Pipeline

`ads-booster` is transitioning to an always-on, on-premises Trace Marketing Agent Service. The
service owns canonical Agent Runs; Codex, Cloudflare, Mac/Appium, Threads, research, and creative
systems are replaceable provider or tool adapters. The only installed command remains
`trace-marketing`; no separate custom-agent executable is introduced.

## On-premises Agent Service (implemented foundation)

The current PR adds the installed service boundary, portable Run/Step/Intent/CapabilitySnapshot/
Invocation/Approval/Receipt/Outcome/Learning contracts, a unified tool descriptor registry, a
replaceable Codex reasoning provider, append-only SQLite recovery, exact effect approval, and a
tenant-scoped HTTP API. Start it with the same macOS user's official Codex CLI login:

```bash
export TRACE_MARKETING_SERVICE_TOKEN='replace-with-a-private-token'
trace-marketing service doctor
trace-marketing service run --model gpt-5.4 --host 127.0.0.1 --port 8765
```

`POST /v1/runs` creates a canonical run, `GET /v1/runs/:id` returns its complete step and record
journey, `POST /v1/runs/:id/input` resumes requested evidence, and
`POST /v1/runs/:id/approval` decides the exact pending invocation. The bearer token is bound by
service configuration to one tenant and principal; callers cannot supply either identity in the
request body. Appium is not inspected or required for service startup or reasoning.

Open `http://127.0.0.1:8765/` and enter the same service token to create and inspect Runs. Channel
result links use `http://127.0.0.1:8765/runs/<run-id>` and open the same run-centric UI directly.
If the official Codex turn is temporarily unavailable, run creation returns HTTP `503` with
`{"error":"reasoning_provider_unavailable","retryable":true}`. The admitted Run remains durable;
submit the identical create request or refresh and retry after provider readiness is restored.

### First dogfood after merge and release

Merging source is not an installed-product release. After the merge commit has a matching tag and
GitHub Release, install it with the verified release bootstrap in
[Bootstrap a verified Mac worker release](#bootstrap-a-verified-mac-worker-release), then run:

The complete hosted workspace → Mac/Appium → approval → optional Threads canary procedure and its
external-preparation checklist are in
[`docs/operations/first-marketing-run.md`](docs/operations/first-marketing-run.md).

```bash
trace-marketing version --json
trace-marketing service doctor
export TRACE_MARKETING_SERVICE_TOKEN='generate-a-private-local-token'
trace-marketing service run --model '<approved-codex-model>' --host 127.0.0.1 --port 8765
```

In a second terminal, verify the installed service—not the checkout—and then use the browser UI:

```bash
curl -s http://127.0.0.1:8765/health
open http://127.0.0.1:8765/
```

The first safe exercise is an Appium-independent goal such as “일본 Threads에서 검증된 Trace
포맷을 확장하기 위해 다음에 확인할 근거를 정한다.” Confirm that the Run URL survives a service
restart and that a provider outage yields the retryable response above instead of dropping the HTTP
connection. This service exercise proves the canonical local Run boundary only. Use the hosted
workspace flow below for the currently integrated candidate/Appium/Threads path until the production
tool registry cutover is complete.

This is a transition, not a claim that the full target product is already live. The production
registry still needs the existing research, candidate, Appium, Threads, and Cloudflare owners
wrapped as tool adapters. The run-centric browser UI and Slack/Kakao channel adapters are not yet
live. Existing Cloudflare/D1 hosted runs below remain the compatibility owner for the old web path
until the cutover is implemented. Fake channel tests will not count as live Slack/Kakao installation
or platform-review evidence.

## Legacy compatibility path

The existing hosted Trace marketing workspace and replaceable macOS capture worker continue to
operate unchanged while that transition proceeds. The Mac remains a request-bound image worker;
default-OFF Threads publishing and engagement polling remain in the hosted Cloudflare boundary.

## Current request path

```text
hosted candidate -> D1 lease -> durable inbox -> safe preparation -> local admission
-> D1 execution barrier -> one Codex/Appium job -> independent PNG/manifest validation
-> durable callback -> R2/D1 -> caption and image review
-> next-slot Threads decision -> Cloudflare publish barrier -> readback -> engagement polling
```

A separate no-effect strategy path starts from immutable product evidence:

```text
feature packet -> agent_v1 shadow campaign -> marketing_judgment lease
-> one structured official Codex turn -> bound context receipt + strategy + experiment
```

The installed CLI also exposes the first dynamic, observe-only Marketing OS slice:

```bash
trace-marketing agent research --input request.json --home /private/path/to/state --model gpt-5.4
```

[`docs/examples/dynamic-evidence-research-product-only.json`](docs/examples/dynamic-evidence-research-product-only.json)
shows the complete request shape. Its hashes are illustrative; replace them with fresh installed
product evidence before making a product-truth claim.

This command freezes one `trace.dynamic-evidence-research-request.v1`, asks the official Codex CLI
to choose exactly one still-needed evidence scope at a time, invokes only registry-bound
`observe.product_truth`, `observe.customer_intelligence`, or `observe.market_evidence` hands, and
re-plans from persisted, receipt-bound evidence summaries and caveats. The installed command requires
an explicit model so a default-model change cannot alter a resumed session. Planner calls record
provider/model/protocol and prompt, context, schema, and skill digests. Known packet claim text,
recognizable URLs, and proposal source IDs/titles/summaries are deterministically redacted from the
bounded planning signal. The remaining semantic string is still untrusted data, never authority. A
request-supplied customer projection is not independently approved by the local runner,
and market proposals remain unverified and `insufficient` until a trusted byte-receipt verifier is
connected. Raw proposals are preserved only in the private content-addressed hand result and cannot
expand product claims. Sessions resume without
repeating a committed decision or completed hand. Missing evidence ends `inconclusive`, and an
ambiguous post-dispatch backend failure ends `awaiting_reconciliation` with exit code 3. It creates no
candidate, Appium action, Threads post, outreach, ad spend, or hosted campaign mutation.

The primary internal dogfood path is now a hosted, channel-independent agent run. The workspace,
and later Slack or KakaoTalk adapters, submit the same immutable
`trace.feature-launch-run-request.v1` to `POST /api/marketing-agent/runs`. D1 persists the run and
queues one `feature_launch_run_v5` worker capability using the
`hosted_marketing_agent_run_v5` no-effect contract. Before dispatch, Cloudflare derives and freezes
the exact observe-only capability snapshot—including configuration bounds, schema digest, per-tool
cost bound, and approval policy—from the requested research scopes. A compatible installed Mac
worker pins the configured `MARKETING_AGENT_MODEL`, constructs its runtime registry from that host
snapshot, performs dynamic evidence research with the official Codex CLI, and returns a bound
research result plus an ordered canonical invocation/receipt/observation envelope and its quarantined market
proposal. Cloudflare independently re-derives the snapshot and verifies complete scope coverage,
lineage uniqueness, exact cost totals, planner protocol, source projection, and task/result bindings.
It recomputes the descriptor, invocation, call, decision, hand-result, receipt, and observation
digests from the redacted proof payload before appending the chain to D1. It then verifies the
proposal digest and source lineage and hands the exact
frozen proposal to the existing hosted byte
verifier, and may then hand one admissible continuation to the shadow-campaign owner. The verifier
does not ask the model to recreate that proposal: it fetches the proposed URLs, records byte receipts,
and only those verified observations can reach strategy. The worker receives its worker token,
never `CONTROL_PLANE_TOKEN`, and cannot create a campaign, candidate, Appium job, publication, or
spend by itself. `GET /api/marketing-agent/runs` and `/runs/:id` expose account-scoped lifecycle,
the frozen capability-snapshot digest, host-validated envelope count, and next links without returning raw
research content. Planner prompt/context/schema hashes and the private session-trace hash remain
authenticated worker claims rather than provider attestation or a host-replayed full trace; this
first action plane therefore remains observe-only.

After the research envelope is formed, a separate structured judgment sees only a host-derived,
eligible no-effect intent snapshot. `stop` is always available; `request_more_evidence` appears only
for an insufficient scope; and `propose_shadow_strategy` appears only for an exact quarantined
continuation. Cloudflare reconstructs that snapshot and the planner prompt before accepting the
choice. It appends an immutable run step: stop creates no task or campaign, while propose delegates
to the existing shadow-campaign owner. When customer evidence was requested but absent,
request-more moves the run to `needs_input`. An authorized operator may resume that same run once
with an existing account-owned marketing-context snapshot through
`POST /api/marketing-agent/runs/:id/resume`. Cloudflare compare-and-swaps the expected head and
appends a new immutable child broker task instead of resetting the completed task. The worker reruns
the same requested research scopes with the governed customer projection and records a second model
decision. That bounded second step must stop or propose; it cannot request an unbounded third cycle.
Public status exposes only the safe intent and loop projection; model-authored rationale remains in
the protected durable decision record.

The installed CLI remains a direct operator fallback that connects the same reasoning loop to the
existing hosted workflow without taking ownership of any execution adapter:

```bash
TRACE_MARKETING_CONTROL_TOKEN=... trace-marketing agent launch \
  --input launch.json --url https://control.example.com --home /private/path/to/state \
  --model gpt-5.4
```

[`docs/examples/feature-launch-shadow.json`](docs/examples/feature-launch-shadow.json) shows the
closed-gate request shape; all hashes and timestamps in it are illustrative.

`launch` currently admits only a closed-gate shadow packet with both product-truth and market scopes.
It reruns or replays the exact research request, and creates no hosted request unless the sole open
trust boundary is a successfully quarantined market proposal. The hosted market-research worker then
verifies source bytes before strategy. The host derives the campaign body, idempotency key, and tool
capability from the frozen request; it binds the caller-supplied agent-run ID as the campaign ID.
None of them are model output. A local append-only session commits the bound handoff and
execution-start marker before POST. An ambiguous response is never POSTed again: later invocations use
only `GET /api/marketing-agent/campaigns/:id` to reconcile. D1 stores immutable agent-run, research
input, trace, and continuation digests and carries them through market research into strategy. The
handoff binds the researched account to the authenticated hosted account and rejects payloads over the
hosted 64 KiB request limit before research or network I/O. Existing
Appium, candidate materialization, Threads approval/publication, evaluation, reassessment, and learning
owners are unchanged.

After the hosted workspace is deployed, open an account and expand **마케팅 에이전트**. This is the
human handoff surface for the agent loop, not a replacement for the existing candidate/Appium/Threads
screens. Enter the exact product repository/path and ref alongside the feature, desired business
outcome, and current control, then copy **Codex에 준비 요청 복사** into a local Codex session that
can inspect that product source. Codex prepares an immutable
`trace.feature-launch-run-request.v1`; paste that JSON into **검증된 실행 요청 JSON** and choose
**에이전트 실행 접수**. The browser now submits and polls the hosted run rather than asking the
Codex conversation to hold a control-plane token or execute the CLI. Enter the control-plane token
only when opening the run/campaign/review view. It is kept in tab memory, cleared on account change or
panel close, and never included in the copied prompt. The selected hosted account must match
`research.account_id`. If the named product ref cannot be read and digest-bound, the handoff instructs
Codex to stop instead of inventing product evidence. Public/private Git connectors and automatic
source-to-packet construction are not implemented yet, so this JSON preparation remains the current
internal onboarding seam rather than a claimed one-click SaaS experience.
Each run card can open a bounded, read-only outcome journey derived from the existing immutable
campaign origin, evaluation, reassessment, next-experiment, and activated-successor records. It does
not reopen the terminal launch run or create a second activity ledger. Shadow, execution-preparation,
observation, and lineage-integrity states remain distinct in the UI.

If a run card reports that customer intelligence is needed, first approve and freeze the relevant
customer signals into an account-owned marketing-context snapshot. Enter that snapshot ID on the run
card and choose **검증된 근거로 재개**. The browser preserves one resume identity across polling and
safe retries, while the server rejects another account, a stale step head, changed retry body, raw
evidence, or a second resume cycle.

The same panel shows account-scoped campaign progress and pending strategy, creative, next-experiment,
and learning decisions. Opening a decision reads its exact server-projected review packet; approve or
reject submits that packet's current action. This reviewer ID is an audit label under the shared
control-plane authority, not individual RBAC. A review does not itself publish to Threads.

`POST /api/marketing-agent/campaigns` accepts an account-scoped source packet, business outcome,
current control, and caller-chosen campaign ID. `GET /api/marketing-agent/campaigns` and
`GET /api/marketing-agent/campaigns/:id` expose its durable state only with control-plane authority.
All requests are scoped by `X-Trace-Account-ID`. This shadow path cannot create
candidates, images, tool actions, or Threads publications. A source-only packet may shape a
hypothesis, but cannot claim installed availability or open a publication gate.

Exact strategy review at
`POST /api/marketing-agent/campaigns/:id/strategy-approval` can request a second no-effect creative
judgment. That judgment chooses proof and a medium per experiment arm, returning a reviewable
MediaPlan without invoking Appium, recording, composition, Figma, candidate creation, or Threads.
The host derives selectable formats from the account's currently active adapter subset; an optional
tool being disabled no longer blocks a format that does not require it. The current installed
capture/copy toolset still admits only `native_sequence`. Unsupported recording, carousel,
designed-static, or text-only labels cannot enter an otherwise executable plan. A later tool
activation affects later plans only; an in-flight plan rechecks the exact bindings it froze.
`POST /api/marketing-agent/campaigns/:id/media-approval` records exact plan review.

An account-authorized `mode: "assisted"` campaign must name a same-account shadow origin, contain
installed evidence with an approved claim set, and bind every action to the exact packet, plan,
treatment, assignment, and approval digests. It can request one candidate materialization through
the existing Mac broker; that turn still creates no capture or publish effect. The existing candidate
and image-review path remains the sole owner of native capture and default-OFF Threads publication.

Variant links resolve at `/api/marketing-agent/v/:token`; a versioned Trace event receiver stores
deduplicated, privacy-safe first-open through setup-complete receipts. The hosted scheduler creates
only conservative, pre-registered experiment evaluations after their observation windows close.
Direct-response rates stay descriptive. The causal-estimation contract additionally requires a
server-owned randomized block plan, immutable allocation receipt, a complete immutable Threads
exposure-slot schedule, exact publication/schedule readback within the fixed tolerance, and an exact
two-sided randomization test. Missing, late, canceled, or mismatched exposure remains inconclusive.
Live `new_launch` strategy judgments also carry a reviewable Decision Dossier: selected ICP or
explicit research need, evidence-bound positioning, every frozen evidence disposition, and one
bounded next action. Customer-signal freshness and confidence are re-derived; product and
quarantined-market freshness remain `unknown`, not a live latest-event feed. Before quarantined
market observations reach strategy, Cloudflare fetches every declared public HTTPS source itself,
requires at least two distinct source and final hosts, and freezes byte-level SHA-256 receipts in
D1. The strategy callback rebinds those receipts to D1; this proves source availability and exact
bytes at collection time, not that the model's summary is faithful, current, or credible. After an
experiment from a dossier-bearing live strategy is evaluated, its callback freezes the evaluation
and prior strategy into exactly one
`outcome_reassessment` turn. That no-effect turn distinguishes an ordinary result, a control win or
stopped performance path, and an observed publication unknown-side-effect; it produces a bound
hypothesis-by-hypothesis reassessment for inspection but cannot publish, spend, retry, or alter the
active campaign. When that reassessment supports another bounded experiment, Cloudflare persists a
`next_experiment` request even while every compatible Mac worker is offline. A later structured
Codex turn must interpret every frozen evidence ID and every contradictory or insufficient item;
it may propose only a challenger concept, may cite only claims owned by its selected parent
hypotheses, and cannot select a held constant as the manipulated component. The host copies the
control, primary outcome, held constants, lineage, admission, and IDs. The protected exact review
packet presents host-verified evaluation/disposition facts separately from untrusted model
interpretations. Approving it records reviewer acceptance but creates no
candidate, capture, publication, spend, or tool action. It appends an immutable activation intent.
When an exact strategy worker is available, the scheduler rechecks the approved draft, grant,
packet claims, source records, unknown-effect state, knowledge, research lineage, and optional
customer-context expiry, then creates exactly one successor `shadow` campaign and its existing
`shadow_strategy` task. The approved challenger, prior control, outcome, and held constants remain
host constraints, and the successor must pass the ordinary strategy review before any later stage.
The source campaign status exposes the latest activation as `pending`, `blocked`, or `activated`
without exposing reviewer authority. Product and market semantic freshness is not certified by this
step; the successor remains a no-effect shadow artifact for explicit strategy review.
The separate decision-quality evaluator still covers synthetic market-event
scenarios offline. General market-event intake and general tool-effect reconciliation are not live.
Replicated evaluated lineages may create a learning candidate, and only an exact human decision can
promote it to a scoped principle. Generic recording, composition, Figma, and generated-media
artifact executors are not yet product operations. New marketing candidates use the same structured
weekly schedule and todo image-input contract as the main generator; legacy `HH:MM` rows remain
read-compatible but are not a valid new marketing materialization. A new materialization fails before
reservation unless a `candidate_materialization_v2` worker is online, and its callback must return
the schema promised by that task capability; only already in-flight capability-less tasks retain v1
callback compatibility. Strategy approval, MediaPlan approval, assisted
campaign creation, candidate materialization, artifact registration, product-event ingestion,
evaluation, and learning approval require the relevant control-plane authority; authority never
enters a worker payload or durable campaign record.

1. A teammate can request an automatic candidate batch. The hosted workspace writes an immutable
   `generate_candidates` task, and a compatible Mac runs one structured official Codex CLI turn to
   return drafts. The worker never restores a plan object or custom Agent runtime; Cloudflare stores
   the idempotent callback result for human candidate review. Repeated strong rejections from three
   distinct candidates can add a scoped caption rule to the task; the callback must return the
   selected feedback digest before Cloudflare accepts it.
2. An approved hosted candidate creates an immutable task with its marketing context, Trace items,
   candidate revision, and `background_intent`. An image retry also receives the exact preceding
   rejection for that candidate and any promoted image rules. This feedback input is additive;
   candidate fields, PNG/manifest output, R2 storage, and review-state transitions keep their
   existing contracts.
3. D1 leases it to a ready, enrolled Mac. The worker writes the task to its SQLite inbox before it
   acknowledges the lease. The workspace's collapsed `실행 기록` timeline then shows safe,
   account-scoped lifecycle events such as preparation, execution, and callback application.
4. Before capture side effects, the worker resolves an iPhone Simulator, validates locale/time zone, fetches
   the allowlisted background, records provenance and SHA-256, creates a private request directory,
   and checks Appium readiness.
5. It commits local admission, then records `execution_started` in D1. Codex cannot start when
   that barrier fails.
6. It starts exactly one ephemeral `codex exec` with user/project configuration disabled and the
   `trace-appium` permission profile. Model-generated commands can read and write only the request
   workspace and can reach only the loopback Appium endpoint; home credentials and external network
   destinations stay blocked. The non-secret
   `trace.codex-appium-job.v2` contract binds context, background, device, digest, nonce,
   locale/time zone, and request-owned calendar namespace. After the D1 barrier, the worker asks the
   DEBUG Trace EventKit helper to seed and verify those events. Codex then observes and operates only
   the real Trace UI, choosing its layout and settings without a prescribed click order. The worker
   removes only the recorded request calendar after collection.
   Before Save, Codex publishes the active Trace wallpaper editor state; the worker independently
   confirms the editor identity and requested titles, clears any earlier export, and only then
   acknowledges Save. This binds collection to the final Save generation rather than an earlier
   lifecycle export from the same request.
7. The worker independently verifies PNG size/SHA-256, request digest, nonce, bundle ID, Simulator
   UDID, dimensions, and `native_appium` provenance from the Trace manifest. That Trace PNG is an
   intermediate `trace_wallpaper`. A second official Codex turn enables `image_generation`, receives
   the packaged default iPhone date/time reference, and replaces only the localized date and time.
   It must preserve the reference's neutral white color, typography, hierarchy, spacing, and top
   placement. Backgrounds, phone frames, status bars, widgets, notifications, and editor chrome are
   rejected. The worker rescales that layer to the Trace canvas, composites it over the verified
   Trace PNG, and records source, prompt, UI-layer, and final digests in
   `trace.imagen-ios-ui.v1`. The returned `imagen_ios_ui` image is a generated copy of default
   iPhone UI, not proof that iOS applied a system wallpaper. The worker then queues the final callback
   durably and retries callback delivery without rerunning the job.
8. Cloudflare writes the accepted image to R2 and state to D1. Caption and image review remain
   mandatory. Final image approval reaches `submitted` and atomically records either a strictly-next
   morning/evening publication or a terminal OFF cancellation; manual-slot candidates are excluded.
9. When the account's default-OFF Threads setting is enabled, Cloudflare alone decrypts the selected
   profile token, checks quota, gives Meta a short-lived digest-bound PNG URL, rechecks the setting and
   profile at the irreversible D1 barrier, calls publish once, and requires post-ID readback before
   `published`. Ambiguous results become `unknown_side_effect` and are never blindly retried.
10. Confirmed posts collect bounded lifetime metrics and top-level replies independently of the
    auto-publish toggle. Reply bodies expire after 30 days and metric snapshots after 365 days; none
    of this data is fed back into candidate generation automatically.

A manifest proves request-bound native export, not visual or semantic fidelity. Human review is the
visual approval boundary.

The workspace's 생성 근거 panel shows whether feedback was selected and whether the Mac returned
the matching consumption receipt. This is transport provenance, not a claim that the generated
content followed every instruction. A token-authorized control-plane request can disable a promoted
rule while retaining its underlying review evidence.

`실행 기록` is intentionally not raw stdout/stderr. It retains recent fixed event names, worker
display name, task kind/ID, timestamps, and sanitized failure codes for fourteen days. Prompts,
provider output, callback bodies, tokens, enrollment codes, exception messages, and local paths stay
on the Mac and are never exposed through this workspace endpoint. Event delivery is best-effort and
uses a bounded local queue, so a saturated or unavailable control plane may drop diagnostics but
cannot block or retry the underlying job; D1 task state and callbacks remain authoritative.

## Bootstrap a verified Mac worker release

```bash
bash -euo pipefail <<'TRACE_MAC_BOOTSTRAP'
repository="corca-ai/ads-booster"
release="$(gh release view --repo "$repository" --json tagName,isDraft,isPrerelease \
  --jq 'select(.isDraft == false and .isPrerelease == false) | .tagName')"
release_dir="$(mktemp -d "${TMPDIR:-/tmp}/trace-marketing-bootstrap.XXXXXX")"
trap 'rm -rf -- "$release_dir"' EXIT
gh release download "$release" --repo "$repository" --dir "$release_dir" \
  --pattern trace-marketing-release.json --pattern trace-marketing-bootstrap.py
manifest="$release_dir/trace-marketing-release.json"
bootstrap="$release_dir/trace-marketing-bootstrap.py"
bundle_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle"]["name"])' "$manifest")"
commit_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit_sha"])' "$manifest")"
[[ "$bundle_name" =~ ^trace-marketing-macos-arm64-v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$ ]]
[[ "$commit_sha" =~ ^[0-9a-f]{40}$ ]]
gh release download "$release" --repo "$repository" --dir "$release_dir" --pattern "$bundle_name"
for asset in "$manifest" "$bootstrap" "$release_dir/$bundle_name"; do
  gh attestation verify "$asset" --repo "$repository" \
    --signer-workflow "$repository/.github/workflows/release-mac-worker.yml" \
    --source-ref refs/heads/main --source-digest "$commit_sha" --deny-self-hosted-runners
done
python3 "$bootstrap" --manifest "$manifest" --bundle "$release_dir/$bundle_name" \
  --uv "$(command -v uv)" --gh "$(command -v gh)"
export PATH="$HOME/.local/share/trace-marketing/current/bin:$PATH"
trace-marketing version --json
TRACE_MAC_BOOTSTRAP
```

Run this as the service-owning macOS user after `gh auth status`. It verifies release assets and
workflow provenance before it executes the downloaded bootstrap, then makes one versioned offline
wheelhouse install under `~/.local/share/trace-marketing/releases/<version>`.

## Mac prerequisites

Run as the same macOS user that owns the LaunchAgent:

```bash
codex login
codex login status
gh auth status
appium driver install xcuitest # only when missing
trace-marketing worker doctor
```

The Mac needs Xcode, an available iPhone Simulator, Appium with XCUITest, and Trace debug build
`com.corca.Trace`. `worker doctor` proves local prerequisites, not an enrolled or completed task.

## Enrollment and operation

```bash
trace-marketing worker create-enrollment --url https://workspace.borca.ai --name 'Studio Mac'
trace-marketing worker enroll --url https://workspace.borca.ai --code '...'
trace-marketing worker install-service
trace-marketing worker status
trace-marketing worker run --once
trace-marketing worker set-state --state draining
trace-marketing worker update --dry-run
trace-marketing worker updater-status
```

The administrator creates the enrollment code. The Mac stores its distinct revocable machine
credential under `~/.trace-agent`; the LaunchAgent pins the selected `codex` executable but stores
neither machine nor Codex credentials.

## Threads Cloudflare configuration

Threads is optional and disabled by default. With no `THREADS_*` configuration, config generation
omits its public variables, `/health` reports `threads_ready: false`, and scheduled publication and
engagement do not run. Existing workspace, candidate, review, and Mac worker paths still deploy.

To enable Threads, configure `THREADS_APP_ID`, `THREADS_REDIRECT_URI`,
`THREADS_GRAPH_API_VERSION`, and `THREADS_PUBLIC_ORIGIN` together. Partial configuration fails
closed. The redirect and public origin must use HTTPS, and the Graph version must use `vN.N`.

Store secret values only with Wrangler; do not put them in the generated config or repository:

```bash
cd cloudflare
wrangler secret put THREADS_APP_SECRET
wrangler secret put THREADS_TOKEN_ENCRYPTION_KEY
wrangler secret put THREADS_MEDIA_SIGNING_KEY
wrangler secret put TRACE_EVENT_INGEST_TOKEN
```

`THREADS_TOKEN_ENCRYPTION_KEY` is a versioned 256-bit AES key such as `v1:<base64>`. The media-signing
key is at least 32 random bytes. All three secrets must exist before health reports Threads ready.
`CONTROL_PLANE_TOKEN` continues to protect OAuth start, profile mutation, reply content, every marketing-agent run/campaign creation or assisted action, exact next-experiment review, and unknown-outcome resolution. `TRACE_EVENT_INGEST_TOKEN` is a separate Trace-app-only secret for product-event ingestion; neither it nor `CONTROL_PLANE_TOKEN` may enter a Mac worker task. Set `MARKETING_AGENT_MODEL` to the exact official Codex model that hosted agent runs must pin. Deploy D1 migration `0016_hosted_threads.sql`, main's `0017_worker_task_events.sql`, and marketing-agent migrations `0018`–`0041` in order before enabling the marketing-agent runtime. Migration `0036` is deliberately forward-only so environments that already recorded `0034` still receive the successor admission guards; `0037` freezes hosted research capability snapshots and adds the append-only run receipt ledger; `0038` freezes the eligible next-intent snapshot and adds the append-only run-step ledger; `0039` adds the bounded customer-evidence resume lineage; `0040` adds the durable campaign-delegation outbox; and `0041` adds parent-scoped journey traversal indexes. Complete Meta App Review for the four documented scopes, connect a test profile, keep auto-publish OFF, then run one explicitly authorized non-production post/readback and engagement canary. Source or fake-Graph success is not live Meta proof.

## Managed releases and compatibility

Managed releases live under `~/.local/share/trace-marketing` and switch `current` atomically.
The default `~/.trace-agent` state home, including credentials, inbox/outbox, artifacts, and
legacy `codex-runs`, remains intact. An `executing` legacy marker without `result.json` only
makes the updater defer; it is read-only compatibility input and is never resumed or rewritten.

`com.corca.trace-agent` and `com.corca.trace-ads` are migration-only legacy plist names: inspect
and drain them separately. The current labels are
`com.corca.trace-marketing-worker` and `com.corca.trace-marketing-updater`.

When a release changes the Cloudflare control plane, the release workflow first waits for that exact
revision's deployed health check. After it confirms the stable release and assets are publicly
readable, it records the version in the hosted control plane. An enrolled worker at an older strict
semantic version receives the target on its next heartbeat and starts the already-loaded updater,
normally within 15 seconds. The updater still verifies GitHub attestation, drains work, switches
atomically, and rolls back on failure. The hourly LaunchAgent interval remains the fallback. Workers
installed before this signal support need one normal `trace-marketing worker update --apply` to gain
it. While the installed version remains older, each heartbeat repeats the non-forced wake-up; it
never kills a running updater.

## Proof boundaries

- A checkout or `uv run` proves source behavior, not a managed installation.
- A doctor report proves local prerequisites, not Cloudflare state or image output.
- PNG/manifest checks prove export bindings, not visual quality.
- Human image approval is the final creative gate. With Threads auto-publish ON it creates a frozen
  next-slot publication decision; only authoritative Cloudflare post-ID readback proves publication.

See [system architecture](docs/architecture/system.md),
[code architecture](docs/architecture/code.md), [dynamic workers](docs/contracts/dynamic-mac-workers.md),
and [testing](docs/development/testing.md).
