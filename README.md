# Trace Marketing Pipeline

`ads-booster` is transitioning to an always-on, on-premises Trace Marketing Agent Service. The
service owns canonical Agent Runs; Codex, Cloudflare, Mac/Appium, Threads, research, and creative
systems are replaceable provider or tool adapters. The only installed command remains
`trace-marketing`; no separate custom-agent executable is introduced.

## Check which CLI installation is running

Updating a checkout does not update an installed command. Before following a command below, inspect
the executable selected by the current shell and the commands that installation actually provides:

```bash
command -v trace-marketing
ls -l "$(command -v trace-marketing)"
trace-marketing --help
```

An older uv-tool installation may still resolve through `~/.local/bin/trace-marketing` to
`~/.local/share/uv/tools/trace-appium-capture/bin/trace-marketing`. A help screen containing only
`simulate`, `bridge`, `bridge-configure` and `worker` does not provide the newer `agent`, `service`
or `server` groups. Source-based `uv run` success does not prove those commands are installed.

For a managed Mac installation, inspect `~/.local/share/trace-marketing/current/bin/trace-marketing`
and follow the [verified Mac bootstrap](#bootstrap-a-verified-mac-worker-release) for the intended
release. For Linux, follow the [server installation guide](docs/operations/agent-server/slack-launch-guide.md);
its installed command lives under `~/.local/share/trace-marketing-server/current/.venv/bin/trace-marketing`.
Confirm that installation's help before selecting it in PATH. Preserve unrelated installations and
state directories; do not replace a CLI symlink merely to make a documented command appear.

## Web login and Slack onboarding (candidate change)

The on-prem service now has authorization-code/PKCE browser login, durable asynchronous web
requests, signed Slack mentions/thread replies/DMs and /trace commands, exact approval and input
handling, and a Notion-independent
research.daily_slack_only schedule. The installed service uses one configured tenant and one
default Slack channel, an explicit conversation-channel allowlist, and isolated member DMs.
research.search accepts a plain query and returns attributed,
explicitly unverified public search snippets; immutable product research remains research.web.

The standard Linux installation path is now `install-server.sh` followed by
`trace-marketing server setup`, using your existing on-premises server and Cloudflare Tunnel/domain.
No ZIP transfer is required. The wizard validates Slack bot credentials, discovers team/bot IDs,
writes private settings, and prepares the agent, dedicated tunnel and five-minute updater services.
See the [installation and Slack walkthrough](docs/operations/agent-server/slack-launch-guide.md).

```bash
# After this installer change is merged and main CI succeeds:
curl -fsSL https://raw.githubusercontent.com/corca-ai/ads-booster/main/install-server.sh -o /tmp/trace-install.sh
bash /tmp/trace-install.sh
export PATH="$HOME/.local/bin:$PATH"
codex login --device-auth
trace-marketing server manifest --origin https://your-agent.example.com --bootstrap
# Create/install the Slack app using that manifest, then:
trace-marketing server setup
trace-marketing server doctor
trace-marketing server start
trace-marketing server status
```

Supported servers are Ubuntu 22.04/24.04 on x86_64/aarch64 with systemd. The installer prepares
missing system packages (sudo), checksum-pinned uv, native Codex and cloudflared; uv installs Python
3.14 and locked application dependencies. Existing tools and tunnel services are preserved. GitHub
login and Node.js are unnecessary. If logged in as root, use `bash /tmp/trace-install.sh --user
trace-marketing`, then `sudo -iu trace-marketing` for Codex login and setup. Credentials stay with
that user. Installer enables linger for operation after logout and reboot.
The Python package includes IANA timezone data via `tzdata`, including on minimal Ubuntu hosts
without a system timezone database.

The managed agent listens on `127.0.0.1:8090`; route the marketing Cloudflare hostname to
`http://localhost:8090`. `~/.config/trace-marketing/server.json` stores an integer `port` (default
8090), shared by launch, updater health and `server status`. Setup writes 8090 on a new install;
existing files retain their values. Port changes require a stopped, idle agent and matching tunnel
route; never edit the port during an update transaction. See the [one-time port migration](docs/operations/agent-server/slack-launch-guide.md#기존-8765-설치에서-8090으로-전환) for older fixed-port installations.
The standalone `service run` command also defaults to 8090 but uses its explicit `--port` option,
independently of managed-server configuration.

Rerun the installer after an interrupted download; rerun `server setup` after an interrupted config
write. Setup resumes its own writes, preserves intervening edits and never imports an unrelated
manual configuration. `server doctor` exits nonzero until local prerequisites, Codex login and setup
are ready. `server status` shows health, installed SHA and the last update result. Every five minutes,
the enabled timer fetches main; only a changed SHA requires an anonymous GitHub CI lookup. A failed
API lookup or pending CI never replaces the running version. Activation drains work, backs up state,
and rolls back a failed start. Company OAuth is unnecessary for Slack-only operation.

`server manifest` exports the final Events-enabled manifest after the public health URL is ready.
For contributors, `bash install-server.sh --source /absolute/checkout` installs that checkout's
**committed HEAD**, labels it `source-…`, and subsequently follows verified public main. This is an
explicit candidate installation, not proof of the published default URL. CI exercises isolated Ubuntu
installation and systemd activation with fixture Slack identity/upstream CI; real Slack delivery and
post-merge public-URL installation require deployment acceptance.

## Small tasks, human work and image review

In an authorized Slack thread, follow-ups keep the same work and budget: ask a question,
provide a correction, or send a human-made result. `어디까지 됐어?` reads status,
`잠깐 멈춰줘` waits at the next safe boundary, and `계속` resumes an input wait.
`새 작업 <request>` starts independent work. A stopped external action is never claimed
undone. Existing `/trace` commands and server onboarding continue to work.

The agent can discover installed procedures with `skills.list` and load one exact version with
`skills.read`. Marketing opportunity research, strategy, copywriting and experiment analysis are
reusable skills; their full instructions are loaded only when selected. Skill reads use the same
Run receipts and call budget as other tools. They grant no integration access or execution approval.
Small writing requests can be answered directly. When a creative plan has an available execution
tool, the agent is guided to continue through that tool and inspect its result before handing back.
Slack follow-ups receive the current bounded conversation, including earlier assistant answers,
so selections such as “use the second option” can resolve within the same work after restart.
The latest admitted request is also passed separately from the original goal and reference data,
so a new question or a shorter-answer request can steer the next reply. Normal answers omit
internal Run diagnostics; `상태` still shows them, and the optional work link appears separately.
`marketing.context` guides on-demand wiki/memory/source lookup. When team knowledge is configured,
DMs expose its scoped read tools as well as skill discovery and public search. A missing read
integration does not mean the wiki is empty; this PR does not enable that integration on a server.

Creative skills can prepare mood/reference/font/color, background review/partial-edit,
capture/localization/mockup/QA instructions without a campaign. If Appium or editing is
unavailable, the answer specifies what to do and what to return. A preparation receipt
is not a produced image. Source/derivative kind, preserved areas, locale and human vs
system verification remain separate.

In an existing Slack work thread, `성과 도움말` shows the bounded reporting commands.
`성과 기록 {JSON}` records the account, country, publication reference, UTC observation
window and views/likes/comments; clicks and installs stay unknown when omitted. Use
`성과 목록` for the six latest reports, `성과 비교 ID ID` to compare two snapshots, and
`성과 정정 ID {JSON}` to correct a report without erasing its source. These are attributed
human reports, not automatically collected platform metrics. `성과 학습 ID[,ID] 관찰 | 반례 |
적용범위` prepares a memory candidate for the existing review/approval flow. Correcting a
source excludes its earlier learning from future context selection. Authenticated clients
can read the same Run's current reports at `GET /v1/runs/{run_id}/performance`; private-chat
reports are not projected into the shared Web workspace.

When Web access is configured, shared Slack summaries link to the same work page. The page
shows up to six recent performance reports and six linked images; selecting an image uses
authenticated byte readback. Source, locale, human-report/worker origin, stale lineage and QA
remain visible. A preview is not a quality approval. Private conversations do not receive a
shared-workspace link. `creative.asset.review` can assess registered same-work images directly,
without uploading a generated result back to Slack; its model findings still require human review.

Slack image review is **optional and off by default**. In a separately approved app update,
add `files:read` to the app's bot OAuth scopes and reinstall it, then set
`TRACE_MARKETING_SLACK_IMAGE_REVIEW=1` in the service environment. Token identity and actual
scope are probed before readiness. This PR does not change an existing app, token, login,
server unit or tunnel. Without that setup, attachments remain task references and human
handoff is available. With access, up to four bound PNG/JPEG files are visually reviewed
through official Codex; no image generation/editing or native app proof is claimed. DM
image execution is not enabled.

Bounded raster editing is separately opt-in: `TRACE_MARKETING_IMAGE_EDIT_CONFIG` points to
a JSON file with an absolute `executable` path to the existing user's official Codex CLI,
an explicit `model_id`, and optional `timeout_seconds` (1–1800, default 300). The service
does not install a provider, change its login or enable this configuration automatically.
Provider capability readiness alone does not prove a successful image edit; validate the
selected installation's actual input/output before team use.

When available, `creative.image.edit` extends the top of a registered image or replaces
explicit rectangular regions; `creative.image.localize` additionally binds the target
locale and exact replacement text. An exact production approval is required. Original
pixels outside the requested region are copied back and checked, including the complete
shifted original for top extension. Typography, meaning, seams and phone-size readability
still require visual and human review. Results are raster assets or edited promotional
images, never evidence of native Trace font/language support. Unknown generation outcomes
are retained for reconciliation and are not automatically regenerated. Without readiness,
the existing preparation and human-handoff paths remain available.
Each admitted edit reserves 20 conservative accounting units; these are not a measured
provider price. A rejected preflight costs zero units, while a started generation retains
its reservation until a validated terminal result or explicit reconciliation.

For a currently pending local capture/edit/localization proposal, an authorized reviewer who
has received every `검토` page for that exact proposal can say `이대로 만들어줘` or
`이대로 제작해줘`. This reuses the exact production review context. Changed targets, another
member's review, publication and remote external effects retain their explicit approval path.

For an uncertain edit, authenticated clients can inspect
`GET /v1/runs/{run_id}/image-edits/{operation_id}`. A current reviewer may explicitly stop
tracking it with `POST` to the same path plus `/abandon`, supplying only
`{"invocation_sha256":"<exact digest>","note":"<reason>"}`. This records a human-reported
abandonment, consumes the reserved 20 units and preserves `outcome_unknown: true`; it does
not assert provider failure, refund cost, publish anything or authorize regeneration.
Repeated identical decisions repair only local completion projection. Keep the database and
artifact directory for readback even after abandonment.

Local acceptance on September 8: Codex 0.153.4 advertised image generation, but restricted
ephemeral-thread setup failed on this host (`codex_image_edit_thread_start_rpc_error_32603`).
The optional edit tool therefore reports unavailable here. Automatic editing/localization
quality remains unverified; do not activate it for the team based solely on fixture tests.

Local Mac capture can be enabled separately with `TRACE_MARKETING_CAPTURE_CONFIG` pointing
to a JSON file containing `device` (`kind: "simulator"`, real `udid`, `platform_version`,
`device_name`), optional loopback `appium_server` and `timeout_seconds` (30–3600).
Start the existing `trace-marketing service run` with that environment variable. This is
local Mac composition, not a remote Mac connection from the Linux server. Defaults and
server onboarding are unchanged. Readiness checks only inspect the already-booted configured
Simulator, installed Trace app and Appium status; they never boot a device or start Appium.
Native export/Debug support is verified only by the approved capture result.

For a separate Mac, the service also supports an **optional remote capture worker**. Configure
`TRACE_MARKETING_REMOTE_CAPTURE_CONFIG` with a private JSON file containing `profile` and
`token_sha256` (SHA-256 of a separate random worker token, at least 32 characters). The profile
contains `worker_id`, the service's exact `tenant_id`, simulator `device`, the Mac's installed
`python_executable` absolute path, loopback `appium_server`, and integer `timeout_seconds`.
Use either local capture configuration or remote capture configuration, not both. No worker
enrollment, existing service mutation or automatic activation occurs during installation.

On that Mac, prepare a separate JSON configuration containing the identical `profile`, HTTPS
service `origin`, `token_env` (name of the environment variable containing the worker token),
and an absolute private `state_root`. Enter the token in the Mac terminal or secret manager;
the server configuration stores its hash. Preserve both state directories on restart.

```bash
trace-marketing worker capture-remote-doctor --config /absolute/path/worker.json
trace-marketing worker capture-remote-run --config /absolute/path/worker.json --once
```

Doctor is local and read-only: it does not send a heartbeat, create worker state or start a
device. Run checks the pinned profile, reports actual readiness and processes approved work;
omit `--once` to remain in the foreground. The Mac must already have the configured Simulator,
Trace app, Appium and the same macOS user's official Codex login ready. An unavailable worker
leaves capture out of the usable catalog; human capture handoff remains available.

Worker credentials grant only the configured capture queue and source/result routes. A job
binds its Run, exact approval, source and complete Mac profile. Local and server start records
precede device preparation. Response loss after a recorded result retries only that result;
unknown device execution stays pending for explicit reconciliation. The new commands do not
install a LaunchAgent, alter the existing D1 worker service or start the marketing server.

When ready, `capture.appium` accepts a registered background's ID/revision/digest, country
(`KR`, `JP`, `US`), reference date, synthetic schedule and preserve/change instructions.
It requires exact runtime approval before device preparation. The result remains subject
to human visual review. Its 20 cost units are conservative fixed accounting, not measured
time or currency. An uncertain capture requires reconciliation and is not automatically
repeated. Slack file references become registered assets only through the optional file intake
below or the existing asset upload API. Remote Mac transport is described above; live device
acceptance remains separate from its local contract verification.

The same optional Slack image configuration also exposes `creative.file.inspect` and
`creative.asset.import`. Inspection downloads a signed, same-work PNG/JPEG (up to 10 MiB)
without invoking image reasoning. The agent can then propose that exact file digest with
source/use terms, data permission and preserve/change metadata for the existing approval
review. Import records the approver's confirmation as `human_reported`; neither upload nor
approval proves licensing, visual quality or native product support. Changed file bytes or
use terms require a new exact review. Import feeds a receipt to the same Run and links the
asset for Web readback. It does not approve subsequent capture, editing or publication.
DM file intake remains disabled. Missing `files:read` keeps these tools unavailable and the
agent can request an asset through the existing human handoff.

Web/API users can `POST /v1/runs/:id/continuation` with `event_id`, `action` (`revise`/`pause`)
and `note`; `POST /v1/runs/:id/assets` accepts a base64 PNG/JPEG (512 KiB maximum), asset ID,
kind, source/use terms/data permission, preserve/change and locale/parent metadata. Registered
Slack/worker image GET readback has a separate 10 MiB limit. Uploads
resume the same work by default; `resume:false` retains a wait. Authenticated
`GET /v1/runs/:id/assets/:asset-id` returns a preview and stale state. Byte validation never
implies visual QA. Run details remain available in the existing Web view.
`awaiting_tool` means an asynchronous tool accepted the task and its result is still pending.
It is distinct from completion or an unknown execution result. Follow-up requests are retained
without cancelling an already-started effect. Remote capture uses this capability only when
enabled by the separate configuration above. Its completion queues one update in the existing
Slack thread; delivery rechecks current membership and preserves unknown send outcomes.

`/v1/memories` provides scoped candidate drafts/read/selection. HTTP identity alone cannot
adopt a shared rule. Authorized Slack reviewers use `기억 제안 <내용>`, `기억 목록`,
`기억 검토 <ID>`, then `기억 채택 <ID> <해시>` or `기억 폐기 <ID> <해시>`.
These default to the current work. For explicitly shared product learning, use
`기억 공용 제안`, `기억 공용 검토`, and `기억 공용 채택` in a team channel; private
conversations cannot create or alter shared notes. Review displays the scope and expiry.
Candidates do not enter future context until review; expiry and corrected/deleted notes
leave current retrieval. Private chat cannot change shared memories.

Record human effort in the current Slack work with `작업 기록 제작 12분 설명` (also
`수정`, `검수`, `현지화`); add `언어=ja` before the description for a locale.
`작업 정정 <ID> 현지화 9분 언어=ja 설명` replaces an earlier report in totals while
preserving history. `작업 요약` separates reported minutes/revisions from recorded tool
cost units. It does not infer start times, currency costs or unreported effort. A report
can be corrected by its author or an authenticated reviewer; at most 1000 records per work.
`작업 학습 <ID> 관찰 | 반례 | 적용범위` creates a work-scoped hypothesis for the existing
memory review flow. Correcting its source excludes that learning from current retrieval
and blocks further approval. No automatic promotion or causal claim is made.

The installed reasoning tool `delivery.prepare` can persist a draft on the current Run.
`POST /v1/runs/:id/delivery` also prepares a typed production/publication/Paid/format/code/change
review packet; `GET /v1/runs/:id/delivery/:proposal-id` reads it. In the same Slack work,
`실행안 검토 <ID> [페이지]` shows its full versioned scope in bounded pages;
the first page leads with a brief of the reason, requested change and relevant costs/conditions.
That brief is navigation only; the complete target below remains the approval reference.
review every page before using the exact approval command shown on the last page.
Membership-authorized reviewers can approve its
version/hash, prepare a reservation or cancel the preparation. **These are preparation
records: no post, reservation, ad spend or GitHub mutation is executed.** Existing external
owners retain their own approval and readback contracts. Asset-bearing approvals and
prepared reservations recheck current revisions, parent lineage, digests and actual bytes;
Paid preparation reserves against the exact approved budget in one local transaction.
OAuth/browser login alone grants no Run approval authority: deployments must supply a trusted
reviewer mapping. Queued approvals recheck it at execution; the configured local operator
token retains its existing loopback authority. Slack uses authenticated membership.

`research.daily_slack` v2 requires only research and Slack. Explicit combined delivery uses
`research.daily_slack_and_notion`; `research.daily_slack_only` remains available. Already-created
v1 scheduled Runs retain their frozen procedure; updating does not silently grant new
Notion permissions or recreate them.

## On-premises Agent Service (implemented foundation)

The source implements the service boundary and portable Run/Step/Intent/CapabilitySnapshot/
Invocation/Approval/Receipt/Outcome/Learning contracts, a unified tool descriptor registry, a
replaceable Codex reasoning provider, append-only SQLite recovery, exact effect approval, and a
tenant-scoped HTTP API. Start it with the same macOS user's official Codex CLI login:

```bash
trace-marketing service doctor
export TRACE_MARKETING_SERVICE_TOKEN='replace-with-a-private-token'
trace-marketing service run --model gpt-6-astra --host 127.0.0.1 --port 8090
```

Keep `--model` explicit. These examples use `gpt-6-astra`; choose a model available to the Codex
account logged in on that host.

For an on-premises or cloud server, terminate HTTPS at the ingress/reverse proxy and configure OAuth
2.0 token introspection before binding beyond loopback:

```bash
export TRACE_MARKETING_OAUTH_INTROSPECTION_URL='https://identity.example/oauth/introspect'
export TRACE_MARKETING_OAUTH_CLIENT_ID='trace-marketing-agent'
export TRACE_MARKETING_OAUTH_CLIENT_SECRET='<secret-store-reference>'
export TRACE_MARKETING_OAUTH_AUDIENCE='trace-marketing-agent'
export TRACE_MARKETING_OAUTH_TENANT_CLAIM='workspace_id'
export TRACE_MARKETING_HOSTED_ORIGIN='https://trace.example'
export TRACE_MARKETING_CONTROL_TOKEN='<cloudflare-control-plane-token>'
export TRACE_MARKETING_SLACK_BOT_TOKEN='<xoxb-token>'
export TRACE_MARKETING_SLACK_CHANNEL_ID='<channel-id>'
export TRACE_MARKETING_NOTION_TOKEN='<notion-integration-token>'
export TRACE_MARKETING_NOTION_PARENT_PAGE_ID='<daily-marketing-parent-page-id>'
trace-marketing service run --model gpt-6-astra --host 0.0.0.0 --port 8090
```

The service accepts a token only when introspection returns `active: true`, the configured audience,
a non-empty `sub`, and a non-empty tenant claim. `sub` owns approval decisions and the tenant claim
scopes every Run read and write. Macs enroll separately as remote Appium workers. A static
`TRACE_MARKETING_SERVICE_TOKEN` is accepted only for loopback development binding.

The installed service exposes `research.web` directly and adds `catalog.hosted.install` plus
`workflow.feature_launch` when the hosted origin and control token are configured. The latter
delegates to the existing research → strategy → candidate → Appium → review → Threads → outcome
pipeline; it does not duplicate those effect owners. Slack and Notion become executable tools only
when both values for that integration are present. Inspect the live catalog with `GET /v1/tools` and
the executable procedures with `GET /v1/skills`.

`POST /v1/runs` creates a canonical run, `POST /v1/skills/:skill-id/runs` starts a versioned skill,
`GET /v1/runs/:id` returns its complete step and record
journey, `POST /v1/runs/:id/input` resumes requested evidence, and
`POST /v1/runs/:id/approval` decides the exact pending invocation. The bearer token is bound by
service configuration to one tenant and principal; callers cannot supply either identity in the
request body. Appium is not inspected or required for service startup or reasoning.

Open `http://127.0.0.1:8090/` and enter the same service token to create and inspect Runs. Channel
result links use `http://127.0.0.1:8090/runs/<run-id>` and open the same run-centric UI directly.
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
trace-marketing service run --model '<approved-codex-model>' --host 127.0.0.1 --port 8090
```

In a second terminal, verify the installed service—not the checkout—and then use the browser UI:

```bash
curl -s http://127.0.0.1:8090/health
open http://127.0.0.1:8090/
```

The first safe exercise is an Appium-independent goal such as “일본 Threads에서 검증된 Trace
포맷을 확장하기 위해 다음에 확인할 근거를 정한다.” Confirm that the Run URL survives a service
restart and that a provider outage yields the retryable response above instead of dropping the HTTP
connection. With `TRACE_MARKETING_HOSTED_ORIGIN` and its control token configured, the canonical Run
can delegate the integrated candidate/Appium/Threads path to the hosted compatibility workflow.

The hosted compatibility API exposes `GET /api/marketing-agent/tools` and authenticated
`POST /api/marketing-agent/tools/install`. The agent can install only server-owned catalog entries;
callers cannot upload code or effect policy. Threads installation returns its OAuth setup path and
remains `registered_reference` until a human completes Meta consent, after which the verified OAuth
callback activates it automatically. The installed service provides live Slack and Notion adapter
owners when their credential pairs are configured.
`GET /api/marketing-agent/skills` exposes versioned procedures separately from tools. The initial
skills are `research.daily_slack` and `threads.validated_format_replication`; readiness is derived
from their independently registered tools, so neither can be reported ready while Slack, research,
capture, or Threads is missing.

To run `research.daily_slack` every day from the server, point
`TRACE_MARKETING_DAILY_RESEARCH_INPUT` at an immutable research-request JSON file and optionally set
`TRACE_MARKETING_DAILY_AT` (`08:00`), `TRACE_MARKETING_DAILY_TIMEZONE` (`Asia/Seoul`),
`TRACE_MARKETING_DAILY_TENANT`, and `TRACE_MARKETING_DAILY_PRINCIPAL`. The scheduler uses one stable
Run ID per local date and grants only the exact scheduled Slack/Notion delivery invocations; it can
never preapprove Appium or Threads publication. Existing Cloudflare/D1 hosted runs remain the
compatibility effect owner until projection cutover. Fake adapter tests do not count as live Slack,
Notion, Meta, or platform-review evidence.

## Team knowledge context

The on-premises `MarketingAgentService` can own a server-local team knowledge store. The knowledge
owner keeps immutable source and Markdown revisions under `TRACE_MARKETING_KNOWLEDGE_ROOT`, its
SQLite catalog in that root, and the deletion chain in `TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT`.
The store is separate from the canonical Agent Run database. A Mac worker receives only a selected,
digest-bound `trace.knowledge-context.v1` envelope; it does not open the knowledge database or select
TEAM, SOUL, MEMORY, Wiki, or source revisions.

Knowledge configuration is all-or-none. Set these three absolute paths together:

```bash
export TRACE_MARKETING_KNOWLEDGE_ROOT='/private/path/to/knowledge'
export TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT='/private/path/to/knowledge-control'
export TRACE_MARKETING_KNOWLEDGE_POLICY='/private/path/to/knowledge-control/policy.json'
```

With all three unset, knowledge is disabled. A partial set fails configuration. The root and control
directory must be owned by the service user with mode `0700`; the policy and control identity file
must be mode `0600`. When configured, `trace-marketing service run` builds the knowledge ingress,
curation provider, bounded jobs, index worker, memory-view worker, owner lock, and continuous runtime
alongside the canonical service. Fresh installed-service behavior and deployment require separate
verification.

Authenticated Agent Service and Slack adapters provide the actor, workspace, member, session, and
grants. The service binds these identities to existing knowledge members and conversation sessions,
preserving stored roles and revocations; replay and context preparation recheck that authority.
Shared Slack threads use workspace scope. Private Slack DMs use member and conversation
scope; the service filters them to read-only knowledge tools (`knowledge_search`, `knowledge_get`,
`memory_get`, `memory_explain`, and `source_read`) and does not grant shared-memory writes or
external delivery. Edits, deletes, and corrections enter a pending fence before the affected Run is
prepared again.

The `trace-marketing knowledge` CLI group is registered as a local admin surface. Every command
requires `--root`, `--control-root`, and `--policy`; `init` also requires `--workspace`. The current
commands are `init`, `doctor`, `ingest --envelope <file> [--attachment ORDINAL=/absolute/path]`,
`run [--model MODEL] [--service-database PATH] [--once|--until-idle [--flush-batches]]`,
`search --query TEXT [--limit N]`, `get --id ID [--revision REVISION]`,
`context --request FILE [--brand ID]`, `schedule --request FILE`, `backup --destination PATH`,
`restore --backup PATH`, `retract --source ID`, `purge --request ID`, and
`questions --pending|--answer ID --text TEXT`. Subgroups provide `memory get|explain|correct|consolidate`,
`brand register|list`, and `task open|close`; their selectors are `--kind`, `--date`, `--brand`,
`--entry`, `--request`, `--task`, and `--id` as applicable. `memory consolidate` requires
`--until-idle`; `run` accepts `--flush-batches` only with `--until-idle`.

Routine curation batches default to a 60-second window from the first event. `--flush-batches` makes collected
routine work ready immediately. Cancellation releases unfinished events for a new batch while
preserving completed event receipts. `brand register` replays an identical operation and name under
the same authority; reusing the operation ID with another name conflicts and replay still requires
current write permission. Verify installed commands, live Codex/Slack, hosted validation, remote purge,
and deployment separately from source tests.

Deletion writes an immutable control-root erase-ledger entry before local blocking and purge. Source,
Wiki, memory, derived context, and transfer dependencies are blocked through tombstones and reverse
dependency records. Hosted or Mac transfer replicas remain `purge_pending` until their deletion
receipts arrive; an external acknowledgement gap is not reported as global purge completion.
`purge --request ID` takes the operation ID returned by `retract`; repeating it resolves the same
stored target and purge request. Restore requires a new target root, validates the backup manifest
and file digests, applies the current erase ledger, and rebuilds search before activating the root.

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
trace-marketing agent research --input request.json --home /private/path/to/state --model gpt-6-astra
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
  --model gpt-6-astra
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

Mac compatibility CI still tests shared package changes and a fresh offline installation. An unchanged
package version does not require a new Mac release. Version changes on main trigger the verified
release pipeline; an explicit main workflow dispatch can release or resume with the existing ownership
guards. Publication and verification follow the [managed release procedure](docs/contracts/mac-worker-auto-update.md#managed-release-publication).
PRs never publish. Mac release checks do not gate the on-prem server updater: it requires the
exact main SHA's successful `Verify on-prem agent` check, including tool-adapter compatibility.
The Mac remains a separately enrolled tool; direct on-prem enrollment/lifecycle management is pending.

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

### On-premises Slack agent with automatic main updates

For a company without OAuth, use `TRACE_MARKETING_SLACK_ONLY=1`: signed `/trace` commands,
results, paginated exact invocation review, approvals and daily Slack research work without a web
login; web UI and bearer API routes are closed. Bind to loopback behind the configured Tunnel.
See the [server installation and operator handoff](docs/operations/agent-server/README.md).

The Linux user service runs a managed installation in `~/.local/share/trace-marketing-server/current`.
The supplied updater timer checks main every five minutes, requires the exact commit's successful
`Verify on-prem agent` check and completed passing checks, stages a locked installation, waits for
active work to finish, backs up state, switches releases and verifies passive startup before resuming.
Failed startup restores the previous code and state; interrupted transactions recover on the next
check. Settings, secrets and canonical records remain outside release directories. GitHub read access
and a one-time timer installation are required; these files do not deploy themselves to a server.
Candidate packages and worktree tests are not proof of a live main update, Slack send or Linux reboot.

Slack conversation setup: enable `TRACE_MARKETING_SLACK_BOT_USER_ID` and invite the bot to an
internal channel. Any workspace member can mention it there. Legacy configured channel/member
lists no longer restrict installed Events conversations. Use `TRACE_MARKETING_SLACK_ALLOW_DM=1`
for members' DMs. The signed
`/channels/slack/events` route acknowledges durable admission before reasoning. Mentions start
threads; ordinary replies continue them with scoped persisted context. DM runs use a derived
workspace/member/session tenant and search-only capability policy; they cannot mutate shared
workspace state or invoke delivery tools. `상태`, `검토 1`, `승인 해시`, `거절 해시`, `종료`,
`다시 시작` work within the conversation. File contents and Slack-wide history search are not supported.
Use the [merge-to-Slack walkthrough](docs/operations/agent-server/slack-launch-guide.md), creating
from the bootstrap manifest first and activating the full Events manifest after server startup.

### Create ads-booster issues from Slack

The optional `github.issue.create` tool creates issues only in `corca-ai/ads-booster`.
In an allowed shared Slack channel, mention the agent with the issue details. It proposes the
repository, title and body for review; use `검토 1` (and subsequent pages), then send the exact
`승인 <hash>` reply as a configured approver. `/trace` uses its existing review/approve commands.
After creation and GitHub readback, the reply includes the actual issue URL. Private DMs do not
have repository write authority. Labels, assignees, PRs and other repositories are not supported.

After the update reaches your server, run as the service user:

```bash
trace-marketing server github-setup
```

Enter a GitHub fine-grained personal access token in the hidden terminal prompt. Select resource
owner `corca-ai`, only repository `ads-booster`, and repository **Issues: Read and write** permission.
See [GitHub's create-issue permission contract](https://docs.github.com/en/rest/issues/issues#create-an-issue).
If the organization requires token approval, wait for it before testing. Never paste the token into
Slack or a chat. The command checks repository access without creating an issue, then stores the
credential in `~/.config/trace-marketing/github.token` with mode 0600; this check alone does not prove
write permission. Existing Slack settings and credentials are preserved.

Once no agent work/update is in progress, restart only the agent:

```bash
systemctl --user restart trace-marketing.service
trace-marketing server status
```

The service reads that private file on startup. For a custom path, set
`TRACE_MARKETING_GITHUB_TOKEN_FILE` in the service environment; it must name a private regular file.
The default token file is outside the release directory and survives main updates. Rotate it with
`server github-setup` and restart the idle service. Remove the credential file and restart to disable
the tool. GitHub authentication for automatic main updates does not grant this write capability.

A timeout, malformed creation response or failed readback leaves the run awaiting reconciliation;
creation is never blindly retried. Check the repository's recent issues before making another request.
Explicit GitHub rejection (for example 401/403) returns a sanitized failure instead of an issue URL.

### Slack working status and stop button

Mention/DM requests now show one working message with an **실행 중단** button. The same message
updates its current stage and elapsed time every five seconds, then becomes the answer, approval
request, failure or stopped result. The old acceptance-only message is no longer sent. This is
execution status, not streaming model tokens or an estimated completion percentage.

For an existing Slack app, after deploying this update enable **Interactivity & Shortcuts** and set:

```text
https://marketing-agent.borca.ai/channels/slack/interactions
```

Use your own public hostname for another installation. Both exported manifests include this setting;
`trace-marketing server manifest --origin https://marketing-agent.borca.ai` prints the updated manifest.
Existing installations must apply the interactivity setting once; automatic server updates cannot
change the Slack app configuration. The existing `chat:write` scope also permits message updates.

The request author (or a configured approver in a shared channel) can stop that execution. The signed
button callback is acknowledged without waiting for reasoning. It cancels the owned Codex subprocess
and prevents subsequent planning/tool dispatch. Already-dispatched external requests finish or enter
reconciliation; stopping does not undo a GitHub issue that was already created. The final message
keeps verified issue links and reports uncertain effects explicitly. Cancellation survives restart,
and a delayed old button cannot stop a newer request. `종료` still closes conversation auto-replies;
the button stops the current execution without closing the conversation.

### Generate an image from Slack with the server's Codex login

In an allowed shared channel, mention the bot with a visual brief, for example
`@Trace Marketing Agent 파란 배경의 미니멀한 Trace 앱 광고 이미지 한 장 만들어줘`.
Review the proposed `creative.image.generate` prompt and approve its exact hash. The installed
server runs one dedicated official Codex image-generation turn with its existing ChatGPT login and
configured model; no image API key or Mac/Appium worker is required. The account/model must support
Codex's `image_generation` feature. Authentication alone does not prove image-generation entitlement.

A validated PNG draft is attached to the originating Slack thread for human visual review. Generated
files are private, digest-addressed artifacts under the service state's `images/` directory, outside
release directories. Results include image/prompt/invocation digests and dimensions. This first
tool generates one new PNG from text and is not exposed in private DMs. Existing optional
image-review/edit tools retain their own configuration; this tool accepts no reference-image input. It does not publish the draft to a marketing channel.

For an existing Slack app add the Bot Token Scope **files:write** under **OAuth & Permissions**, then
**Reinstall to Workspace** and approve the added permission. Both manifests include the scope. If Slack
issues a replacement bot token, enter it through server setup's hidden prompt and restart the idle
agent; never paste it in chat. Keep the previously configured interaction callback for the stop button.

Working status includes generation and image validation. Stopping cancels the owned Codex process;
if generation was already admitted the run can require reconciliation because usage/results may be
uncertain. It never automatically reruns an uncertain generation. Slack attachment failures preserve
the local draft, report the unconfirmed upload and do not upload again automatically. A generated
image requires human review; a PNG/digest check is not visual approval.

Installed Slack mention access is workspace-wide: invite the bot to any internal public or private
channel, then any member can mention it and continue in that thread. Existing configured
member/channel lists no longer limit Events API conversations. Each newly seen Slack user gets a
separate durable identity without approval rights; configured approvers retain their rights.
Revoked/disabled identities and Slack Connect channels remain rejected. `/trace` slash commands
retain their configured channel/member restrictions. No additional Slack scope or setup reset is
required for mention access after updating the server.
