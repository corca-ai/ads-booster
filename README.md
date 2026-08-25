# Trace Marketing Agent Runtime

This repository builds Trace lock-screen marketing images without setting a real
iOS wallpaper. It keeps the background photo, native Trace components, and
AI-generated iPhone system UI as independent layers, then runs capture and composition
through a durable, idempotent `TraceRun` state machine.

The standalone agent can perform read-only web and image searches when asked, but it does not
automatically research trends, invent personas, write campaign copy, publish to Notion or Threads,
or learn from campaign feedback. The local workspace can turn saved persona and promotion JSON
plus uploaded reference images into finite or continuous generation campaigns. Results still stop
at human review; there is no external publication step.

## Install as a native CLI

On macOS or Linux, install the user-local CLI with one command:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://raw.githubusercontent.com/corca-ai/ads-booster/main/install.sh | bash
source ~/.zshrc  # use ~/.bashrc for bash
trace-ads --help
```

The installer uses `uv tool install` without `sudo`. If `uv` is missing, it installs
uv into `~/.local/bin`, lets uv manage the supported Python 3.14 runtime, installs the
package into uv's isolated tool environment, and links `trace-ads`, `trace-agent`,
`trace-capture`, `trace-compose`, and `trace-run` into `~/.local/bin`. Re-running the
same command upgrades the installed checkout with `--force`.

For a local checkout, run:

```bash
bash install.sh --source .
```

Use `--dry-run` to inspect the plan. `--ref <git-ref>` selects a GitHub ref, while
`--bin-dir <absolute-path>` changes the user bin directory. The installer updates only
the current user's zsh or bash startup file; `--no-shell-update` disables that change.
It does not install Xcode, Appium, the XCUITest driver, or the separate `Trace_iOS`
debug build required by the native capture path. Prepare those prerequisites manually
only when you need Trace automation; the CLI and offline composition do not require them.

## Standalone agent shell

`trace-ads` is a foreground process with its own REPL, session history, model loop,
approval policy, and tool registry. It does not start or call a Codex process. The existing
`trace-run` engine is exposed as one governed Trace capability inside the shell.
`trace-agent` remains available as a compatibility alias.

```bash
uv sync
source .venv/bin/activate
# OpenAI ChatGPT / Codex OAuth 로그인
trace-ads auth login
trace-ads --model gpt-5.5
```

실제 터미널에서 `trace-ads`를 실행하면 대화 영역, 도구 상태, 승인 패널이 있는
Codex풍 TUI가 열립니다. `Ctrl-C` 두 번으로 종료, `Ctrl+L`은
대화 초기화, `Ctrl+K`는 입력창 포커스입니다. 마우스 드래그를 통한 터미널 텍스트
선택 및 복사를 지원합니다. 파이프나 CI처럼 TTY가 없는 환경에서는 자동으로 기존 plain
REPL을 사용하며, 어느 환경에서나 `trace-ads --plain`으로 명시할 수 있습니다.
가상환경을 활성화하지 않는 경우에는 `uv run trace-ads`를 사용합니다.

로그인은 TUI 안에서 OpenAI ChatGPT / Codex OAuth 2.0 (PKCE)로 처리할 수 있습니다.

```text
/auth login                    OpenAI ChatGPT / Codex 브라우저 OAuth 로그인
/auth status                   OpenAI OAuth 로그인 상태
/auth logout                   저장된 OAuth credential 삭제
/model [model-id]              현재 모델 확인 또는 변경 (예: gpt-5.5)
/permission [ask|yolo]         승인 모드 확인 또는 변경 (기본값: yolo)
/new                          현재 세션을 보존하고 새 세션 시작
/clear                         현재 세션을 삭제하고 새 세션 시작
/session [session-id]          이전 세션 목록 표시 또는 지정 세션 복귀
/help                          명령 목록 표시
```

입력창에 `/`를 입력하면 사용할 수 있는 핵심 명령이 미리보기로 표시되고, 문자를
더 입력하면 해당 prefix에 맞춰 목록이 좁혀집니다. `↑/↓`로 항목을 선택하고
`Tab` 또는 `Enter`로 입력창에 채울 수 있습니다.

TUI의 permission 기본 모드는 `yolo`이며 파일 쓰기, shell, browser mutation, TraceRun 같은
변경 작업을 자동으로 허용합니다. `/permission ask`로 전환하면 각 변경 작업마다 승인 패널이
열리고, `Approve` 또는 `Deny`를 선택해야 합니다. 현재 모드는 `/permission`으로 확인하고
`/permission yolo`로 다시 자동 허용 모드로 바꿀 수 있습니다.

`/new`는 현재 세션을 `~/.trace-agent/sessions`에 보존한 뒤 빈 세션으로 전환합니다.
`/clear`는 현재 세션의 저장 기록까지 삭제한 뒤 빈 세션으로 전환합니다. `/session`은
이전에 보존한 세션을 TUI 선택 목록으로 보여주며, 방향키와 Enter로 복귀할 수 있습니다.
plain REPL에서는 목록에 표시된 ID를 `/session [session-id]`로 입력합니다. 각 답변이
완료될 때 현재 세션 history가 자동 저장되므로 프로세스를 다시 실행한 뒤에도 복귀할 수
있습니다.

`/model`을 입력하면 현재 provider의 사용 가능한 모델 목록을 불러옵니다. 목록에서
위/아래 방향키와 Enter로 모델을 선택할 수 있으며, `/model [model-id]`로 직접 지정할
수도 있습니다. (예: `gpt-5.5`, `gpt-5.4`).
선택한 모델이 추론 강도를 제공하면 이어서 `low`, `medium`, `high`,
`xhigh` 같은 effort 목록이 표시되고, 선택값은 현재 TUI 세션의 provider 요청에
즉시 반영됩니다.
자연어로 현재 모델을 물어보면 다음 provider 요청에 실제로 전달할 `requested_model`을
기준으로 답합니다. provider 내부에서 별도로 라우팅하는 숨은 모델명까지 확인한다는 뜻은
아닙니다.
기존 `trace-ads auth login`은 TTY에 들어갈 수 없는 환경이나 명시적인 device-code
fallback이 필요할 때 사용할 수 있습니다.

### Context-driven one-shot image generation

The first context-driven capture path is available directly from the standalone agent:

```bash
trace-agent generate-one \
  --context-file appium/jobs/composite/mock-contexts/jp-student-exam.json \
  --image-model gpt-5.6-luna
```

The command parses the persona and promotion-material context, prepares the native runtime by
opening and booting the selected Simulator and starting Appium when they are installed but inactive,
then asks the current ChatGPT/Codex OAuth compatibility Responses endpoint to generate the external
background. Appium captures the current iPhone UI and requests a fresh Trace component export for
the same run. A final high-fidelity Image Model edit receives those three verified image layers and
returns the marketing image. It does not reuse a previous Trace component artifact, start a Codex
process, or require a separate manually authored `trace-run` job. The default `gpt-5.6-luna` value matches the current
ChatGPT/Codex account-compatible model catalog; `--image-model` can override it when the
authenticated account advertises another image-capable Responses model.

## Team workspace service

The workspace is a local-first team surface layered on top of the existing agent. Keep
`trace-ads` or `trace-agent` for the standalone TUI and plain REPL; install the agent on an
always-on Mac when the team needs a persistent workspace, shared context, private member chats,
and automatic marketing generation. The installer only installs the CLI; start the workspace
separately when you are ready. The standalone TUI and authenticated Web private chat use the same
`AgentSession`, tool registry, provider OAuth, and slash-command contract. Web chat is a browser
surface for the same agent controls, including `/session`, `/model`, `/permission`, `/auth`, `/new`,
`/clear`, and `/help`; `/permission ask` shows approval controls in the browser. The service does
not start a Codex process, publish to Notion or Threads, or create a remote database.

### Start the local service

Run the service in the foreground during a local check:

```bash
export TRACE_AGENT_HOME="$HOME/.trace-agent"
uv run trace-agent serve --host 127.0.0.1 --port 8765 --workspace-name "Launch archive"
```

The default service keeps the ASGI origin on loopback and requests a bounded cloudflared quick
tunnel. Use `--tunnel none` for local-only operation. Start the workspace explicitly with:

```bash
trace-agent workspace start --workspace-name "Launch archive"
```

The installer does not start launchd or create a URL. `--workspace-service` is an explicit installer
opt-in for users who want setup and startup in one command; otherwise the installer skips all
workspace side effects. The workspace start command requires cloudflared on `PATH`.

On the first `workspace start`, the service creates one workspace and one owner member using the
supplied `--workspace-name` without printing
authentication codes. `service.json` and the SQLite database persist only code hashes. To inspect
the initialized workspace without exposing a code, use:

```bash
uv run trace-agent workspace access
```

`workspace show` remains an optional diagnostic command; it is not required for normal team access.

`workspace access` explicitly rotates the owner workspace/member codes and prints one browser login
ID once. Its four `%`-separated parts are Workspace ID, Member ID, Workspace code, and Member code;
paste the complete value into the browser entry form. The compatibility alias `rotate-code` performs
the same action. The service still parses and verifies those four values separately, then scopes
shared context to the workspace and private chat history to the authenticated member.
Rotating either code version invalidates sessions issued with the old version. The first-run CLI
provisions the owner pair; the local operator can provision another member with a one-time invite
code:

```bash
uv run trace-agent workspace add-member --name "Grace"
```

The command is intentionally local-only because the current Web surface has no separate
administrator identity. It prints the new member ID and invite code once; only the scrypt hash is
stored. Authenticated members can upload JPEG, PNG, and WebP references from 자료 준비. The
`/api/assets/upload` route validates the image bytes, stores a protected copy below
`$TRACE_AGENT_HOME/assets/`, and records its normalized path, media type, SHA-256, and size.
The lower-level `/api/assets` CRUD routes remain available for metadata integrations.

The service binds to loopback only. `--port 0` chooses an available port for an ephemeral
check and prints the selected URL; use a fixed port for launchd. A foreground `serve` process
owns the listener, so stop it with `Ctrl-C` when it is not managed by launchd.

The Web private chat keeps conversation history scoped to the authenticated member and session.
The central agent OAuth credential belongs to the always-on host, so team members use their
Workspace/Member access codes rather than separate provider accounts. `/session` lists only that
member's saved private sessions; `/model` and `/permission` controls are also member-scoped for the
running service. The browser command catalog is loaded from the same TUI command definitions, so
new commands do not need a second Web-only list.

### Workspace data and configuration

`TRACE_AGENT_HOME` is the service's single local data root. It defaults to `~/.trace-agent`.
Use an absolute path when configuring a dedicated Mac:

| Path | Contents |
| --- | --- |
| `$TRACE_AGENT_HOME/workspace.sqlite3` | Workspace/member records, hashed access-code versions, shared context and asset metadata, and member-scoped private session histories |
| `$TRACE_AGENT_HOME/automation.sqlite3` | Finite/continuous campaigns, queue records, leases, run references, artifact hashes, and review state |
| `$TRACE_AGENT_HOME/service.json` | Workspace/member IDs, loopback host/port, tunnel selection, and the last emitted public URL; never plaintext codes |
| `$TRACE_AGENT_HOME/auth.json` | Agent OAuth credentials, written with mode `0600` |
| `$TRACE_AGENT_HOME/memory.jsonl` | Context-compaction summaries for the standalone agent |
| `$TRACE_AGENT_HOME/sessions/` | Standalone TUI/REPL session histories |
| `$TRACE_AGENT_HOME/logs/` | Protected service and optional cloudflared logs |

The one-shot generation command keeps its default run artifacts relative to the checkout that
invokes it: `.trace-agent/generated/`, `.trace-agent/state/`, and `.trace-agent/capture/`.
The workspace queue stores queue metadata under `TRACE_AGENT_HOME`; the configured worker owns
the generated artifact root and records only verified run/artifact references in the queue.

The relevant environment overrides are:

```text
TRACE_AGENT_HOME              # default: ~/.trace-agent
TRACE_AGENT_MODEL             # default: gpt-5.5
TRACE_AGENT_MEMORY_FILE       # default: $TRACE_AGENT_HOME/memory.jsonl
TRACE_AGENT_SESSIONS_DIR      # default: $TRACE_AGENT_HOME/sessions
TRACE_AGENT_WEB_SEARCH_PROVIDER
TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS
TRACE_AGENT_BROWSER_COMMAND
TRACE_AGENT_APPIUM_SERVER       # default: http://127.0.0.1:4723
TRACE_AGENT_IPHONE_UI           # default: appium/jobs/composite/inputs/iphone-ui-ai.png
TRACE_AGENT_IMAGE_MODEL         # default: gpt-5.6-luna
TRACE_AGENT_GENERATION_TIMEOUT_SECONDS # default: 120
```

### Continuous campaign and review flow

In 자료 준비, save a persona as `PersonaProfile` JSON and a promotion as `PromotionMaterial`
JSON. A promotion may include exactly three `trace_items` to control the native Trace copy. Upload
optional visual references, then select those records in 새 자료 만들기. Choose a finite count or
leave continuous production enabled. The campaign freezes those inputs and the service creates one
uniquely identified variation at a time. Stopping a campaign prevents future variations without
erasing work already submitted or running. A known Image Model, credential, filesystem, or Appium
failure records the queue item as failed and automatically stops that campaign instead of producing
an unbounded failure loop.

Each variation becomes a typed `MarketingContextBundle`. Reference images are digest-checked again,
sent as Responses image inputs, and used with high-fidelity image editing. The scheduler claims due
queue records through a single active worker lease. The worker invokes `GenerateOneRunner`, verifies
run identity, output presence, and the artifact SHA-256, then moves a verified result to `review`.
The advanced one-shot JSON form and lower-level `/api/queue` route remain available for explicit
integrations.

Review is explicit: a member accepts or rejects the review record, and optimistic revisions
reject stale decisions. An expired running attempt becomes `failed/unknown_side_effect` and is
not blindly retried. `trace-agent serve` starts the web/API process and its persistent scheduler/
worker; it does not claim that a queued item has completed until the worker records that state.
The MVP has no automatic external publication step.

### launchd service

The native CLI installer supplies `trace-agent` on `PATH`. After activating the local venv or
installing the user-local tool, install the per-user macOS service:

```bash
trace-agent workspace start --port 8765 --workspace-name "Launch archive"
trace-agent service status
launchctl print "gui/$(id -u)/com.corca.trace-agent"
```

`workspace start` uses the same launchd service boundary. It writes
`~/Library/LaunchAgents/com.corca.trace-agent.plist`, creates
protected stdout/stderr files below `$TRACE_AGENT_HOME/logs/`, loads the job by default, waits for the
previous launchd job to finish unloading before replacement, and then waits briefly for the local
service and the emitted public URL to become ready. On an existing state root,
`--workspace-name` updates the stored workspace name when you intentionally rerun setup.
The default launchd command requests cloudflared; use `--tunnel none` for local-only service
operation. Use `--no-load` to generate the plist without starting it or `--plist <path>` to
choose an explicit plist path. To stop the loaded per-user job, run:

```bash
launchctl bootout "gui/$(id -u)/com.corca.trace-agent"
```

`service status` reports whether the plist exists and whether the saved local URL answers
`/health`. When the local service is healthy, it reports the public URL emitted by the configured
cloudflared process. It does not require a second public DNS probe from the same Mac, because a
local resolver failure can hide a tunnel that external team members can still reach.

### Optional public URL

The default cloudflared mode uses the `cloudflared` executable on `PATH`. The adapter starts a
bounded quick tunnel and accepts only a live `https://*.trycloudflare.com` URL emitted by that
process. If cloudflared installation is disabled, the executable is missing, the tunnel times out,
or it exits without a URL, the service stays available at its loopback URL and prints
`Public URL: unavailable (...)`. The URL is shown after the local service is healthy and the
tunnel process has emitted it; external reachability still depends on the network used by each
team member. Configure a stable domain separately if the deployment needs one.

### Generation prerequisites

The workspace web/API shell can start without native capture dependencies. A queued generation
or `generate-one` run also needs:

- ChatGPT/Codex OAuth with an image-capable model (`trace-agent auth login`)
- Xcode and an available iOS Simulator
- a Debug Trace build installed as `com.corca.Trace`
- the request-bound Trace component-export trigger from the sibling `Trace_iOS` checkout
- Appium 3 with the XCUITest driver, normally at `http://127.0.0.1:4723`

The installer does not install Xcode, Appium, the XCUITest driver, or the Trace Debug build.
Prepare those prerequisites manually before accepting a generated result.

### Compatibility smoke checks

The workspace commands are additive. These checks keep the existing aliases and one-shot path
visible while exercising the new service boundary:

```bash
uv run trace-ads --help
uv run trace-agent --help
uv run trace-agent serve --help
TRACE_AGENT_HOME="$(mktemp -d)" uv run trace-agent auth status
uv run trace-agent generate-one --help
uv run trace-agent workspace --help
uv run trace-agent service --help
```

## Context, compaction, and prompt cache

대화 history는 canonical transcript로 보존하고, provider에 보내는 model projection만
줄입니다. context pressure는 다음 순서로 처리합니다.

1. 큰 `function_call_output`을 request-local로 prune합니다.
2. soft limit에 도달하면 marketing goal, country/persona/caption 결정, artifact reference,
   미검증 gap을 보존하는 compact summary를 만들고 최근 turn tail을 유지합니다.
3. summary는 JSONL memory file에 저장하며 원본 history는 삭제하지 않습니다.
4. provider가 context overflow를 반환하면 같은 turn을 한 번만 compact 후 retry합니다.

기본값은 128k context, 70% soft threshold, 85% hard threshold, 최근 16k token tail,
tool output 6k 문자입니다. 다음 환경변수로 조정할 수 있습니다.

```text
TRACE_AGENT_CONTEXT_WINDOW_TOKENS
TRACE_AGENT_CONTEXT_SOFT_RATIO
TRACE_AGENT_CONTEXT_HARD_RATIO
TRACE_AGENT_CONTEXT_RECENT_TAIL_TOKENS
TRACE_AGENT_CONTEXT_MAX_TOOL_OUTPUT_CHARS
TRACE_AGENT_MEMORY_FILE       # default: ~/.trace-agent/memory.jsonl
TRACE_AGENT_WEB_SEARCH_PROVIDER       # auto, ddgs, or brave; default: auto
TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS # default: 30
BRAVE_SEARCH_API_KEY                   # optional; used by provider=brave or auto
TRACE_AGENT_SESSIONS_DIR      # default: ~/.trace-agent/sessions
```

prompt cache는 correctness 전제가 아닙니다. stable instructions와 고정된 tool descriptor
순서로 prefix digest를 만들고, provider 응답이 `usage.input_tokens_details.cached_tokens`
를 제공할 때만 실제 cached token 수를 표시합니다. provider가 cache usage를 주지 않으면
`cache=unknown`으로 남기며 cache hit를 추정하지 않습니다.

The default login command opens the ChatGPT/Codex subscription OAuth compatibility flow
with PKCE and a loopback callback at `http://localhost:1455/auth/callback`. If the local
browser callback cannot be used, paste the redirect URL when prompted. Device-code login
is an explicit fallback with `trace-ads auth login --device-code`; it may be
disabled by account security settings. Tokens are stored in the agent-owned
`$TRACE_AGENT_HOME/auth.json` path, or `~/.trace-agent/auth.json` by default, with mode
`0600`. The credential is never printed by status commands. This is distinct from the
usage-billed OpenAI Platform API-key route and may change if the compatibility backend
changes.

The built-in tools are:

- `file_read`, `file_list`, and approval-gated `file_write` inside the workspace
- approval-gated `shell_exec` using the workspace as the command boundary
- `browser` through the external `agent-browser` CLI; navigation and snapshots are
  read-only, while click, typing, and screenshots require approval
- `web_search` through a read-only provider; `auto` uses keyless DDGS and selects
  Brave when `BRAVE_SEARCH_API_KEY` is configured, returning normalized source URLs
- `image_search` through the same provider setting, returning image URLs, thumbnails,
  source pages, and available dimensions
- approval-gated `trace_run` for the existing Appium/staging/composition workflow

For Trace capture, image generation, and visual QA, the agent first inspects the local runtime. It
starts an installed but inactive Simulator or Appium dependency, verifies readiness, and continues
without asking the user. It does not install missing software or start these services for unrelated
work. A missing Trace Debug build remains a typed prerequisite failure.

Use `--workspace <directory>` to change the file and command boundary and
`TRACE_AGENT_MODEL` to select the Codex-compatible model identifier.

## Runtime boundary

```text
upstream marketing context / trace.run-job.v1
        |
        v
     trace-run --> TraceRun journal (append-only JSONL)
        |
        +--> trace.capture --> Appium + Trace native component export
        |
        +--> stage verified component artifact
        |
        +--> trace-compose --> final marketing PNG
```

Layer order is fixed:

1. Background photo
2. Trace component PNG
3. iPhone system UI PNG

The worker never asks Simulator to display a custom wallpaper. A physical iPhone is
not required.

## Prerequisites

- macOS with Xcode and an available iOS Simulator
- A debug Trace build installed as `com.corca.Trace`
- The Trace component-export launch trigger and request-bound manifest export included
  in the sibling `Trace_iOS` checkout
- Appium 3 with the XCUITest driver
- `uv`

The currently checked environment uses Appium 3.3.0, XCUITest 11.0.0, and iOS
26.5. Confirm live values instead of copying the sample UDID blindly.

```bash
appium --version
appium driver list --installed
xcrun simctl list devices available
xcrun simctl listapps <SIMULATOR_UDID> | rg 'com\.corca\.Trace'
```

## Setup

```bash
uv sync
```

`trace-agent generate-one` and the workspace worker start the installed Simulator and Appium
server when they are inactive. Run `appium --port 4723` manually only for lower-level capture
commands such as `trace-capture` or `trace-run`.

## Run the complete local workflow

With a native Trace debug build installed, this command uses the hardened Appium
capture path and then composes the final image:

```bash
uv run trace-run \
  --job appium/jobs/composite/trace-run-example.json \
  --state-root .trace-runs \
  --capture-output-root appium/jobs/composite/captures \
  --appium-server http://127.0.0.1:4723 \
  --timeout-seconds 120
```

For an offline smoke test, replace Appium with the checked temporary component
fixture. The fixture path must differ from the declared staging destination:

```bash
uv run trace-run \
  --job appium/jobs/composite/trace-run-example.json \
  --component-artifact appium/jobs/composite/inputs/trace-components-fixture.png \
  --state-root .trace-runs-smoke
```

Re-running the same `run_id`, `idempotency_key`, and input returns the durable terminal
result without invoking capture or composition again. An `idempotency_key` is reserved
across the entire state root, so the same key cannot be claimed by a different
`run_id` or input. Reusing the identity with changed input fails closed.

The journal records contiguous sequence numbers, UTC timestamps, the idempotency key,
input digest, requested capability, artifact reference, and terminal state. Transitions
are flushed and `fsync`ed before each capability side effect.

## Run each capability manually

### 1. Export Trace components

The capture job injects exact fixture items and passes
`-traceMarketingExportComponents` to the Trace debug build. Trace writes a native
transparent `trace_components.png` plus `trace_components.manifest.json` into its App
Group, and the worker collects both.

```bash
uv run trace-capture \
  --job appium/jobs/composite/component-export.json \
  --output-root appium/jobs/composite/work \
  --appium-server http://127.0.0.1:4723 \
  --timeout-seconds 120
```

`trace-capture` accepts only explicit numeric HTTP loopback endpoints. It rejects remote
hosts, credentials, query strings, fragments, and TLS endpoints before WebDriver starts.
`--cancel-file <path>` provides a process-external cancellation marker.
The Appium session command timeout is extended beyond the shared capture deadline so
delayed native publication does not expire the WebDriver session before cleanup.

Expected artifact:

```text
appium/jobs/composite/work/component-export/trace-components.png
appium/jobs/composite/work/component-export/trace-components.manifest.json
```

If the native manifest is missing or does not match the request digest, nonce, Trace
bundle, Simulator UDID, role, canvas, and artifact hash, capture fails with
`export_unverified` or `export_invalid`. The offline `--component-artifact` override is
for local fixture smoke tests only.

If the Trace debug export itself fails, it publishes
`trace_components.error.json`; the collector returns `export_failed` immediately instead
of waiting for the entire capture deadline.

The native manifest intentionally does not claim a WebDriver session binding. It records
the per-capture export nonce together with the request digest, Trace bundle, and
Simulator UDID; the `session_id` in capture provenance is the Appium-side identifier.

### 2. Compose the marketing image

```bash
uv run trace-compose \
  --job appium/jobs/composite/example.json
```

Expected artifacts:

```text
appium/jobs/composite/outputs/final-marketing.png
appium/jobs/composite/outputs/jp-night-city-calendar-iphone-ui.png
appium/jobs/composite/outputs/composite-result.json
```

The compositor crops the background photo to the requested canvas, normalizes the
AI UI layer to real alpha transparency, resizes every layer consistently, and applies
the fixed layer order.

## Contracts

### Component export

`trace.capture-job.v1` configures the Simulator, fixture items, and component export.
The only supported capture target is `trace_components`.
For component-only capture, `background_image` may be omitted; the background remains
required by the separate composition job.
When supplied, `component_canvas` declares the expected native PNG dimensions (the
current iPhone 17 Pro/iOS 26.5 export is `1206×2622`) and the
manifest/artifact validator rejects a self-consistent export with the wrong canvas.
`reference_date` is also scene input and is passed to the Trace fixture instead of being
fixed inside the Appium adapter.

Successful scene results contain provenance:

- deterministic request SHA-256
- capture source (`native_appium` or `offline_fixture`)
- per-capture native export nonce and request/device binding
- collected artifact SHA-256 and byte size
- expected Trace bundle ID and target Simulator UDID
- Appium session ID (separate from native export binding)
- PNG dimensions and source modification time

The collector removes the stale App Group export before launching Trace, requires the
new file to be fresh, and accepts only a readable RGBA PNG with at least 20% fully
transparent pixels and at least 1% visible pixels. Cleanup errors are recorded without
replacing the primary capture failure.

`trace-run` carries this capture provenance into its result and journal. An offline
fixture can therefore be used for deterministic smoke tests without being represented
as a native Appium export.

### Trace run

`trace.run-job.v1` embeds one capture job and one composition job with matching marketing
context. It adds a stable `run_id` and `idempotency_key`. Runtime states are closed:

```text
queued -> running -> awaiting_tool -> completed | failed | aborted | unknown_side_effect
```

Terminal and aborted runs cannot invoke later capabilities. Corrupt, reordered, or
identity-inconsistent journals are rejected during replay. If the process restarts
after an `awaiting_tool` event was durably written, the runner records
`unknown_side_effect` and does not invoke that capability again: the external effect
must be reconciled by an operator before the run can be resolved. This state and all
replay/error output remain scoped to the requested run directory.

### Marketing composition

`trace.marketing-composite-job.v2` requires three distinct input paths:

```json
{
  "schema_version": "trace.marketing-composite-job.v2",
  "job_id": "jp-night-city-calendar",
  "context": {
    "country": "JP",
    "persona_id": "jp-office-worker",
    "promotion_material_id": "night-city-monthly-calendar"
  },
  "canvas": {"width": 1290, "height": 2796},
  "layers": {
    "background": "inputs/background-night-city.png",
    "trace_components": "work/component-export/trace-components.png",
    "iphone_ui": "inputs/iphone-ui-ai.png"
  },
  "output_image": "outputs/final-marketing.png"
}
```

Input and output paths are resolved relative to the job file and may not collide or
escape that directory.

## Sample asset status

The checked sample final image demonstrates the corrected three-layer pipeline. Its
component layer is a temporary transparent fixture derived from the supplied visual
reference. Replace it by running the native Appium component export after the updated
Trace debug build is built and installed.

## Exit codes

The Trace capture CLIs use:

- `0`: completed
- `1`: runtime or layer failure
- `2`: unreadable or invalid job contract

## Development checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
```

## Key files

| Path | Purpose |
| --- | --- |
| `src/trace_capture/agent/` | REPL and standalone agent loop |
| `src/trace_capture/auth/` | ChatGPT/Codex OAuth, refresh, and credential store |
| `src/trace_capture/automation/` | Durable campaigns, variation producer, queue, scheduler, and GenerateOne worker adapter |
| `src/trace_capture/providers/` | Codex-compatible Responses transport and model contracts |
| `src/trace_capture/search/` | Text/image search contracts and external provider adapters |
| `src/trace_capture/service/` | Foreground workspace server, launchd plist, and service status |
| `src/trace_capture/tools/` | Workspace, shell, browser, and Trace capability tools |
| `src/trace_capture/tunnel/` | Optional cloudflared public-URL boundary |
| `src/trace_capture/web/` | Authenticated workspace API and static browser shell |
| `src/trace_capture/workspace/` | Local SQLite workspace, context, member, and private-session stores |
| `src/trace_capture/capture/` | XCUITest/Appium capture and native artifact validation |
| `src/trace_capture/composition/` | Layer normalization and deterministic PNG composition |
| `src/trace_capture/contracts/` | Versioned capture, composition, and run contracts |
| `src/trace_capture/runtime/` | TraceRun state machine, journal, locks, and replay |
| `src/trace_capture/cli/` | `trace-ads`, `trace-capture`, `trace-compose`, and `trace-run` boundaries |
| `appium/jobs/composite/` | Runnable sample job, layers, result, and final PNG |

Last reviewed: 2026-08-25
