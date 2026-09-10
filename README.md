# Trace Marketing Agent

`ads-booster` provides an always-on, on-premises Trace Marketing Agent Service. It owns
canonical Agent Runs, team knowledge, research, reviewable creative work, and Slack/Web access.
The installed command is `trace-marketing`. The service runs directly on its host through systemd;
Cloudflare Tunnel can provide HTTPS ingress. Workers, D1/R2 campaign storage, Mac/Appium execution,
and Threads publishing are no longer part of this repository's runtime.

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
`simulate`, `bridge`, `bridge-configure` and `worker` identifies a retired installation. The current
command provides `agent`, `service`, `server` and `knowledge` groups. Source-based `uv run` success does not prove those commands are installed.

For Linux, use the [server installation guide](docs/operations/agent-server/slack-launch-guide.md).
The managed executable lives under `~/.local/share/trace-marketing-server/current/.venv/bin/trace-marketing`.
Preserve unrelated installations and state when choosing the CLI in PATH. The distribution name
`trace-appium-capture` remains unchanged for package compatibility.

## Web login and Slack onboarding

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

For supplied funnel counts, `marketing.analyze` calculates stage conversion and cost per desired
customer outcome, with explicit denominators and comparison limits. It needs no credentials and
is available in admitted Slack channels and private conversations. Counts must be nested unique
people from mature cohorts; spend is a decimal string such as `"300.00"`. Unknown spend and zero
denominators remain undefined. The tool does not collect analytics or authorize budget changes.
Growth and customer-interview skills connect these observations to experiments and finished copy.
See the [marketing colleague evaluation](docs/research/marketing-colleague.md) for research,
observed weaknesses and the limits of the synthetic Slack rehearsal.

The agent can discover installed procedures with `skills.list` and load one exact version with
`skills.read`. Marketing opportunity research, strategy, copywriting and experiment analysis are
reusable skills; their full instructions are loaded only when selected. Skill reads use the same
Run receipts and call budget as other tools. They grant no integration access or execution approval.
Small writing requests can be answered directly. When a creative plan has an available execution
tool, the agent is guided to continue through that tool and inspect its result before handing back.

With scoped team knowledge available, discovery prefers `skill_list` / `skill_get`, which include
effective learned revisions and built-ins. Both list routes accept `query`, `limit` (1–100,
default 50), and `offset`; `next_offset` continues the same query and filters. Search uses Unicode
keywords and exact skill IDs, not semantic similarity. Read the returned exact revision/version.
The prepared index ranks relevant metadata before spending at most 2,400 token-budget units and
half the remaining context capacity, leaving room for team evidence. Missing capabilities are
reported as unavailable in the current snapshot; they do not prevent reading useful guidance.

Slack에서 `@Trace 스킬 만들기: 고객 인터뷰를 광고 카피로 바꾸는 절차`처럼 요청하면,
에이전트가 `marketing.skill_learning` 절차에 따라 기존 스킬을 검색하고 공용 스킬을 저장한 뒤
저장 receipt와 정확한 revision을 다시 읽어 확인합니다. 저장한 스킬은 같은 워크스페이스의
다른 채널에서도 검색·사용할 수 있습니다. 수정은 `@Trace 스킬 수정: 스킬 ID와 변경 내용`으로
요청합니다. 일반 피드백이나 반복 작업만으로 스킬을 자동 생성·수정하지 않습니다.

저장은 현재 사용자의 명시적 요청에서만 가능합니다. 첫 줄의 `스킬 만들기:` / `스킬 수정:` /
`스킬 삭제:` 또는 지원되는 직접 요청형 문장을 확인하며, 애매한 표현은 다시 요청하도록
안내합니다. 스킬은 공용 절차이므로 채널의 비공개 사실·인증정보를 넣지 않습니다.
원본 채널 대화의 읽기 권한은 확대되지 않으며, 요청 원문이 수정·삭제되거나 차단되면
그 근거에 연결된 스킬은 재사용에서 제외됩니다. 개인 DM에서는 공용 스킬을 수정할 수 없습니다.
스킬 저장은 실행 코드 설치, 새 도구 등록, 외부 게시 승인이 아닙니다.
[비교 자료와 확장 경계](docs/research/adaptive-skills.md)를 참고하세요.
Slack follow-ups receive the current bounded conversation, including earlier assistant answers,
so selections such as “use the second option” can resolve within the same work after restart.
The latest admitted request is also passed separately from the original goal and reference data,
so a new question or a shorter-answer request can steer the next reply. Normal answers omit
internal Run diagnostics; `상태` still shows them, and the optional work link appears separately.
`marketing.context` guides on-demand wiki/memory/source lookup. When team knowledge is configured,
DMs expose its scoped read tools as well as skill discovery and public search. A missing read
integration does not mean the wiki is empty; this PR does not enable that integration on a server.

Creative skills can prepare mood/reference/font/color, background review/partial-edit,
localization/mockup/QA instructions without a campaign. If editing is
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

For a currently pending edit/localization proposal, an authorized reviewer who
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

The same optional Slack image configuration also exposes `creative.file.inspect` and
`creative.asset.import`. Inspection downloads a signed, same-work PNG/JPEG (up to 10 MiB)
without invoking image reasoning. The agent can then propose that exact file digest with
source/use terms, data permission and preserve/change metadata for the existing approval
review. Import records the approver's confirmation as `human_reported`; neither upload nor
approval proves licensing, visual quality or native product support. Changed file bytes or
use terms require a new exact review. Import feeds a receipt to the same Run and links the
asset for Web readback. It does not approve subsequent editing or external delivery.
DM file intake remains disabled. Missing `files:read` keeps these tools unavailable and the
agent can request an asset through the existing human handoff.

Web/API users can `POST /v1/runs/:id/continuation` with `event_id`, `action` (`revise`/`pause`)
and `note`; `POST /v1/runs/:id/assets` accepts a base64 PNG/JPEG (512 KiB maximum), asset ID,
kind, source/use terms/data permission, preserve/change and locale/parent metadata.
Registered image GET readback has a separate 10 MiB limit. Uploads
resume the same work by default; `resume:false` retains a wait. Authenticated
`GET /v1/runs/:id/assets/:asset-id` returns a preview and stale state. Byte validation never
implies visual QA. Run details remain available in the existing Web view.
`awaiting_tool` means an asynchronous tool accepted the task and its result is still pending.
It is distinct from completion or an unknown execution result. Follow-up requests are retained
without cancelling an already-started effect. Image-edit completion queues one update in the existing
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
records: no post, reservation, ad spend or GitHub mutation is executed.** Publication targets
and manually reported platform names do not provide a Threads API or publishing integration.
Configured external tools retain their own approval and readback contracts. Asset-bearing approvals and
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
tenant-scoped HTTP API. Start it with the service user's official Codex CLI login:

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
export TRACE_MARKETING_SLACK_BOT_TOKEN='<xoxb-token>'
export TRACE_MARKETING_SLACK_CHANNEL_ID='<channel-id>'
export TRACE_MARKETING_NOTION_TOKEN='<notion-integration-token>'
export TRACE_MARKETING_NOTION_PARENT_PAGE_ID='<daily-marketing-parent-page-id>'
trace-marketing service run --model gpt-6-astra --host 0.0.0.0 --port 8090
```

The service accepts a token only when introspection returns `active: true`, the configured audience,
a non-empty `sub`, and a non-empty tenant claim. `sub` owns approval decisions and the tenant claim
scopes every Run read and write. A static
`TRACE_MARKETING_SERVICE_TOKEN` is accepted only for loopback development binding.

The installed service exposes `research.web`, `research.search`, creative preparation and direct
Codex image generation. Slack, Notion and GitHub tools become executable when their integration
configuration is present. Inspect readiness with `GET /v1/tools` and procedures with `GET /v1/skills`.

`POST /v1/runs` creates a canonical run, `POST /v1/skills/:skill-id/runs` starts a versioned skill,
`GET /v1/runs/:id` returns its complete step and record
journey, `POST /v1/runs/:id/input` resumes requested evidence, and
`POST /v1/runs/:id/approval` decides the exact pending invocation. The bearer token is bound by
service configuration to one tenant and principal; callers cannot supply either identity in the
request body. Tool readiness is checked independently from service startup and reasoning.

Open `http://127.0.0.1:8090/` and enter the same service token to create and inspect Runs. Channel
result links use `http://127.0.0.1:8090/runs/<run-id>` and open the same run-centric UI directly.
If the official Codex turn is temporarily unavailable, run creation returns HTTP `503` with
`{"error":"reasoning_provider_unavailable","retryable":true}`. The admitted Run remains durable;
submit the identical create request or refresh and retry after provider readiness is restored.

To run `research.daily_slack` every day from the server, point
`TRACE_MARKETING_DAILY_RESEARCH_INPUT` at an immutable research-request JSON file and optionally set
`TRACE_MARKETING_DAILY_AT` (`08:00`), `TRACE_MARKETING_DAILY_TIMEZONE` (`Asia/Seoul`),
`TRACE_MARKETING_DAILY_TENANT`, and `TRACE_MARKETING_DAILY_PRINCIPAL`. The scheduler uses one stable
Run ID per local date and grants only the exact scheduled Slack/Notion delivery invocations; it can
never preapprove image production or unrelated external effects. Fake adapter tests do not count
as live Slack or Notion evidence.

## Team knowledge context

The on-premises `MarketingAgentService` can own a server-local team knowledge store. The knowledge
owner keeps immutable source and Markdown revisions under `TRACE_MARKETING_KNOWLEDGE_ROOT`, its
SQLite catalog in that root, and the deletion chain in `TRACE_MARKETING_KNOWLEDGE_CONTROL_ROOT`.
The store is separate from the canonical Agent Run database. The service selects TEAM, SOUL,
MEMORY, Wiki and source revisions under current identity and access grants.

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
Shared Slack conversations use workspace plus channel ID scope: threads in one channel share
knowledge, while other channels have separate memories, Wiki and brand settings. Private Slack DMs
use member and conversation scope, without automatically reading channel or legacy workspace data; the service filters them to read-only knowledge tools (`knowledge_search`, `knowledge_get`,
`memory_get`, `memory_explain`, and `source_read`) and does not grant shared-memory writes or
external delivery. Edits, deletes, and corrections enter a pending fence before the affected Run is
prepared again.

### Shared feedback and procedural learning

When knowledge is enabled, authenticated members admitted to shared Slack threads contribute to
one workspace learning stream. A clear correction can update CORE immediately at the next safe
foreground boundary. Terminal tool receipts count toward a bounded review round; nine shared turns
or receipts leave the round collecting, and the tenth of either kind seals one logical round. A
reviewed complete observed receipt can support a reusable procedure; failed, unknown, or invalidated
receipts remain evidence without promotion. The wake-up counter does not merge authority:
existing member, session, scope, grant, and policy partitions still produce separate curation work.

Private DMs do not add shared-learning turns or receipts and cannot write shared memory or skills.
The same knowledge repository stores agent-created procedural skills. The complete built-in catalog
and its overrides stay protected from background learning. An authenticated member may change a
protected procedure only through an explicit current foreground request; a changed built-in release
causes the stored override to fall back to the current built-in until it is reviewed.

Normal learning is silent. An unresolved conflict for the same applicability remains pending and
creates one question in the original Slack thread. That question is a learning input, not a
`ToolApproval`. Existing reviewed SQLite work and performance notes remain read-only evidence; the
automatic path does not dual-write them or change their review gate. No additional provider,
daemon, store, or verifier is introduced for this path.

The enabled runtime exposes `skill_list`, `skill_get`, and `skill_apply` through the existing
knowledge tool host. Prepared context carries only the effective skill index and its selected
revision receipt. Each selected skill keeps nested `source_refs` and `source_revisions`; these are
skill provenance and are distinct from generic retrieval references. The procedure body is loaded
by `skill_get`. Binding-free Runs hide these tools and this context. Private DMs may read shared
skill metadata and bodies under their current grants, but cannot write them.

Completed shared message plans and terminal receipts enter learning only through their canonical
stored source or Run receipt. Message edits and deletes invalidate dependent pending learning; an
edited source can enter the urgent correction path. After a foreground correction applies, its exact
target is fenced by source revision so a later review cannot apply it twice while other source
evidence remains available. A completed `no_effect` receipt counts as terminal work for the learning
cadence, while effect success is recorded separately. Any reusable procedure requires reviewed
typed input/output provenance bound to the invocation, receipt, source, and Run.

The reduced verification evidence covers the bounded repair, installed basic CLI/API smoke, and one
external installed minimal reuse canary. The expanded verification matrix remains unexecuted.

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
current write permission. Verify installed commands, live Codex/Slack and deployment separately from source tests.

Deletion writes an immutable control-root erase-ledger entry before local blocking and purge. Source,
Wiki, memory, derived context, and transfer dependencies are blocked through tombstones and reverse
dependency records. Preexisting transfer replicas remain `purge_pending` until separately reconciled.
The service has no configured remote purge transport; removing an adapter neither deletes remote
data nor acknowledges its deletion.
`purge --request ID` takes the operation ID returned by `retract`; repeating it resolves the same
stored target and purge request. Restore requires a new target root, validates the backup manifest
and file digests, applies the current erase ledger, and rebuilds search before activating the root.

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

When team knowledge is configured, project conditions and corrections in shared Slack conversations
can be retained automatically as source-backed reference memory. Mention the same project in a new
thread to retrieve its current conditions. The background curation worker preserves the subject's
scope and correction history; temporary or QA facts are not general preferences for unrelated work.
A storage claim still requires a successful stored-memory read or receipt. Private chat does not
promote information into shared memory, and the explicit work-memory review commands retain their
existing workflow. See the [local Docker rehearsal](dev/local-agent/README.md) for isolated testing.

Channel isolation also applies to reviewed work memory and work/performance observations.
`기억 공용` shares within the current channel. Existing workspace-scoped records are preserved,
but are excluded from channel defaults; they are not automatically copied or reassigned. The
workspace OAuth API retains its explicit workspace scope. The local admin CLI defaults to
workspace scope; for channel administration, use a separate private policy JSON copy with
`"channel_id": "C0123456789"` and pass its path as `--policy`. Keep the main service policy
unchanged. The policy copy uses the same workspace and control identity, but creates channel-specific
grants, sessions and document IDs. Brand registration returns the brand ID to add to that policy
copy's `brand_voice_brand_ids` before editing SOUL. Request-body scope overrides do not grant access.

Personal marketing preferences use a `USER` document owned by the workspace, channel and
authenticated Slack member. For example, “I prefer short copy with images first” becomes that
member's default in that channel; another member has a separate profile. The preference survives
new threads, while a different channel and DM do not inherit it. Common project facts remain in
channel CORE memory. Current instructions and established team/brand rules take precedence over
personal defaults; a one-time request does not change the saved default.

`memory_get` with `kind: user` resolves the authenticated requester's profile without accepting
another user's identity. The existing SQLite store owns its entries, permissions and revision
links. Versioned Markdown remains immutable, and the current readable view is generated at
`teams/<workspace>/channels/<channel_scope_key>/users/<encoded_member_id>/USER.md`.
Use the agent's memory operations to make corrections; editing the generated file alone does not
change canonical memory. Source messages remain provenance, but a message supporting personal
preferences is excluded from common reference search, including when it also supplied a separately
stored common fact. Existing common memory is not automatically reclassified as personal.

### Package releases

[GitHub Releases](https://github.com/corca-ai/ads-booster/releases) provides versioned wheels,
source distributions, the source commit and SHA-256 checksums. A reviewed version bump is published
automatically after verification of that exact main commit. Maintainers follow the
[release procedure](docs/conventions/github.md#on-prem-package-releases).
The Python package keeps the compatibility name `trace-appium-capture`; its CLI is `trace-marketing`.
The server installer/updater continues to follow verified main. A GitHub release is a downloadable
package snapshot; check the server's health release SHA to confirm an installed update.
