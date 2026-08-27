# Trace Marketing Agent Runtime

This repository builds Trace marketing images without setting a real iOS wallpaper. It retrieves
a licensed-source background from image search, configures native Trace components through Appium,
and deterministically composites those layers with a clean iPhone system-UI asset through a durable,
idempotent `TraceRun` state machine.

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
  --context-file appium/jobs/composite/mock-contexts/jp-student-exam.json
```

The command parses the persona and promotion-material context, searches approved public image
sources for a background, verifies and normalizes the selected file to PNG, then prepares the native
runtime by opening and booting the selected Simulator and starting Appium when they are installed
but inactive. Appium enters the three current Trace component titles through the Trace setup UI,
saves that configuration, and requests a fresh request-bound native component export. The final
image is a deterministic three-layer composition: searched background, native Trace components,
and the packaged iPhone system UI. It does not call an image-generation model, reuse a prior Trace
component artifact, add Trace branding, or require a separately authored `trace-run` job.

## Team workspace service

The workspace is a local-first team surface layered on top of the existing agent. Keep
`trace-ads` or `trace-agent` for the standalone TUI and plain REPL; install the agent on an
always-on Mac when the team needs a persistent post factory. The installer only installs the CLI;
start the workspace separately when you are ready. The service does not start a Codex process,
publish to Notion or Threads, or create a remote database.

The browser surface is two tabs: 후보 and 캡션·주제 승인. 후보 is the default tab; it creates post
candidates and lists every candidate in the workspace, and 캡션·주제 승인 is the human approval gate
for the first stage of the candidate journey.
Persona, promotion, and reference knowledge lives in the operator's own markdown folder rather than
in a browser form. The context, asset, campaign, queue, and private-chat routes still run and keep
their tests, but they are API-only: no browser tab renders them.

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
paste the complete value into the browser entry form. An authenticated owner can use `팀원 초대` in
the browser to create a regular member and receive a three-part member access ID that does not
contain the shared workspace code. The compatibility alias `rotate-code` performs the same action.
The service still scopes shared context to the workspace and private chat history to the authenticated
member.
Rotating either code version invalidates sessions issued with the old version. The first-run CLI
provisions the owner pair; the local operator can provision another member with a member access
code:

```bash
uv run trace-agent workspace add-member --name "Grace"
```

The first owner access ID is the workspace administrator. After signing in, the owner can use the
browser's `팀원 초대` action to create a regular member and receive a three-part member access ID
once. The member pastes that ID into the same browser entry field; the server verifies only the
member's scrypt-backed code and never exposes the shared workspace code. The CLI command remains
available as a local fallback. The `/api/assets/upload` route validates JPEG, PNG, and WebP bytes,
stores a protected copy below `$TRACE_AGENT_HOME/assets/`, and records its normalized path, media
type, SHA-256, and size. That route and the lower-level `/api/assets` CRUD routes are API-only; the
browser has no upload form since the two-tab restructure.

The service binds to loopback only. `--port 0` chooses an available port for an ephemeral
check and prints the selected URL; use a fixed port for launchd. A foreground `serve` process
owns the listener, so stop it with `Ctrl-C` when it is not managed by launchd.

The private-chat API keeps conversation history scoped to the authenticated member and session, and
the central agent OAuth credential belongs to the always-on host, so team members use their
Workspace/Member access codes rather than separate provider accounts. `/session` lists only that
member's saved private sessions; `/model` and `/permission` controls are also member-scoped. The
two-tab browser surface no longer exposes chat, so these controls are reachable through the
`/api/chat` routes and the standalone TUI.

### Workspace data and configuration

`TRACE_AGENT_HOME` is the service's single local data root. It defaults to `~/.trace-agent`.
Use an absolute path when configuring a dedicated Mac:

| Path | Contents |
| --- | --- |
| `$TRACE_AGENT_HOME/workspace.sqlite3` | Workspace/member records, hashed access-code versions, shared context and asset metadata, post candidates with their review state, and member-scoped private session histories |
| `$TRACE_AGENT_HOME/automation.sqlite3` | Finite/continuous campaigns, queue records, leases, run references, artifact hashes, and review state |
| `$TRACE_AGENT_HOME/service.json` | Owner workspace/member IDs, loopback host/port, tunnel selection, and the last emitted public URL; never plaintext codes |
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
TRACE_AGENT_CONTEXT_DIR       # default: <serve working directory>/context
TRACE_AGENT_CANDIDATE_TIMEOUT_SECONDS  # default: 240
TRACE_AGENT_MEMORY_FILE       # default: $TRACE_AGENT_HOME/memory.jsonl
TRACE_AGENT_SESSIONS_DIR      # default: $TRACE_AGENT_HOME/sessions
TRACE_AGENT_WEB_SEARCH_PROVIDER
TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS
TRACE_AGENT_BROWSER_COMMAND
TRACE_AGENT_APPIUM_SERVER       # default: http://127.0.0.1:4723
TRACE_AGENT_IPHONE_UI           # default: packaged clean iPhone system UI asset
TRACE_AGENT_TRACE_COMPONENTS    # default: packaged Trace component fixture asset
TRACE_AGENT_GENERATION_TIMEOUT_SECONDS # default: 120
```

### Candidate pipeline

A candidate is the post unit of the workspace: a topic, a caption, its hypothesis, the references
and principles behind it, and the free-form Appium prompt used to build its image. Candidates have two
entrances, automatic and manual, and both land in one list before a human approval gate. The 후보
tab opens on a 새 게시물 만들기 block: 후보 자동 생성 is the primary action and 수동 등록 is a
collapsed form for pasting one candidate by hand — 주제/컨셉 comes first and is required, and 국가
is a KR/JP/TW/US dropdown that defaults to KR. Below the block the tab lists every candidate in the
workspace, newest first, with the topic as its main line and the caption's first line beneath it.
The 캡션·주제 승인 tab shows one card per candidate that is still awaiting a decision: the topic is
the card headline, the caption sits directly under it, and the composed image when one exists,
hypothesis, applied principles, references, the recorded AI verdict, and the Appium prompt follow.
One approve/reject pair decides the topic and the caption together — there is no separate per-field
gate.

Every candidate row and approval card carries the same three-step journey line,
`① 캡션·주제 승인 → ② 이미지 승인 → ③ 제출`, so the current position is visible. Stages ① and ② are
implemented; ③ is reached by approving the image, and posting itself stays manual:

| Status | Meaning | Reachable today |
| --- | --- | --- |
| `awaiting_review` | 캡션·주제 검수 대기 | yes, on creation |
| `caption_approved` | 캡션·주제 승인됨 · 이미지 대기 | yes, by approving stage one or rejecting an image |
| `rejected` | 반려됨 | yes, by rejecting stage one |
| `image_awaiting_review` | 이미지 검수 대기 | yes, by generating an image |
| `submitted` | 제출됨 · 게시 준비 완료 | yes, by approving an image |

The 캡션·주제 승인 tab holds both gates. `① 캡션·주제` lists candidates awaiting the first decision.
`② 이미지` lists candidates that passed it: a `caption_approved` card offers `이미지 생성` and
shows `이미지 생성 중… (1~3분)` while the request runs, and an `image_awaiting_review` card shows the
composed image with the topic and caption beside `승인` and `반려` plus a reason field. A
rejection returns the candidate to `caption_approved` with the note, so a new image can be composed.

Opening the workspace database runs two idempotent migrations for rows written before these
fields existed: `accepted` becomes `caption_approved`, and candidates stored before `topic` was
required are backfilled with the placeholder `(주제 미기록)` so the required field holds. Posting
stays manual and outside this runtime.

후보 자동 생성 is wired to the built-in agent. One click assembles the operator's context
documents into a single provider call and stores three Korean candidates as `source=auto`, awaiting
caption approval like any manual candidate. The button disables itself and shows
`생성 중… (1~3분 소요)` while the request runs, then refreshes the list. It needs two things:

- **Run the service from the folder that holds `context/`.** The generator reads
  `<serve workspace>/context` unless `TRACE_AGENT_CONTEXT_DIR` points elsewhere, and it needs
  `core/PIPELINE-SCOPE.md`, `core/PRINCIPLES-GLOBAL.md`, `core/PRINCIPLES-KR.md`,
  `core/ELEMENTS-KR.md`, `core/VOICE-KR.md`, `core/SHOOTING-KR.md`, `core/FACTS.md`, and
  `references/KR/INDEX.md`. A missing folder or file stops the run before any model call and the
  browser shows which one.
- **A logged-in agent credential.** Run `trace-agent auth login` in the terminal first; without it
  the button reports `AI 로그인이 필요합니다`.

This version is deliberately simple: one non-streaming call per click, no tool loop and no web
search, exactly three candidates, Korean only. The model must answer with a strict JSON array; one
malformed answer is retried once with the validation error, and a second failure stores nothing and
reports `AI 응답이 형식을 통과하지 못했습니다`. The generated `appium_prompt` is stored as the
candidate's Appium 프롬프트.

| Route | Purpose |
| --- | --- |
| `GET /api/candidates` | List the authenticated member's workspace candidates, newest first |
| `POST /api/candidates` | Create a manual candidate from `topic`, `country`, `caption`, `hypothesis`, `image_inputs`, and optional `refs_used`/`principles_applied`/`shooting_order`; the server forces `source=manual` and `status=awaiting_review` |
| `POST /api/candidates/generate` | Assemble `context/` into one provider call and store three `source=auto` candidates, or store nothing; `409` for a missing context folder or credential, `502` for a provider or format failure |
| `POST /api/candidates/{candidate_id}/review` | Stage-one decision on topic and caption together: `caption_approved` or `rejected`, with an optional note and an `expected_revision` guard |
| `POST /api/candidates/{candidate_id}/generate-image` | Stage-two composition: search a background, compose the lock-screen image, and move a `caption_approved` candidate to `image_awaiting_review`; `409` for the wrong stage, a stale revision, or a failed run |
| `POST /api/candidates/{candidate_id}/review-image` | Stage-two decision: `submitted`, or back to `caption_approved` with the note; `409` for the wrong stage or a stale revision |
| `GET /api/candidates/{candidate_id}/image` | Serve the composed PNG to the owning workspace; `404` when the candidate has no image |

Every decision carries the candidate's current revision and only applies from the expected stage. A
stale revision or a repeated decision returns `409`, an unknown candidate returns `404`, and
candidates are never visible or reviewable from another workspace. The Appium prompt is stored in
the `shooting_order` field; only its UI label was renamed.

`image_inputs` is the machine half of a candidate and the manual form collects it: 잠금화면 일정 is a
textarea with one item per line (1–8 lines), 기기 시각 takes `HH:MM`, 배경 소재 is a dropdown with
Korean labels, 배경 분위기 is a short free-text mood, and the content language is derived from the
selected country.

#### What the image stage needs

`이미지 생성` composes the image in the web process without Appium or a simulator, from three
layers: a background from the external image search allowlist (Pexels/Unsplash/Pixabay), the
packaged Trace component fixture, and the packaged iPhone UI asset. The searched background's
provider, source URL, and digest are written to `inputs/background-source.json` beside it.

- **An image search route.** The stage uses the same provider selection as `trace-generate-one`:
  install `ddgs`, or set `BRAVE_SEARCH_API_KEY`. `TRACE_AGENT_WEB_SEARCH_PROVIDER` and
  `TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS` override the choice and its timeout. Without a working
  route the browser reports `배경 이미지를 찾지 못했습니다` and the candidate stays `caption_approved`.
- **Nothing else.** The Trace component fixture and the iPhone UI both ship inside the installed
  package, so the stage works from whatever directory the service was started in — including a
  knowledge folder that holds only `context/`. `TRACE_AGENT_TRACE_COMPONENTS` and
  `TRACE_AGENT_IPHONE_UI` override them with an absolute or working-directory-relative path; a
  missing override file stops the run before any search and is reported with the environment
  variable that set it.

Because the Trace component layer is that fixture and not a fresh native export, the candidate's own
schedule items and device time are recorded on the run but are **not** drawn into the image — the
fixture's calendar and clock appear instead. Rendering a candidate's own schedule needs the native
Appium capture environment, which this stage does not use. Artifacts are written under
`$TRACE_AGENT_HOME/candidates/<candidate-id>/r<revision>/`, and the composed image's path and
SHA-256 are recorded on the candidate.

### Continuous campaign and review flow

This flow is API-only after the two-tab restructure: the browser no longer has the 자료 준비,
생성 큐, or 새 자료 만들기 surfaces that used to drive it, and the routes below are reachable only
through the API.

Save a persona as `PersonaProfile` JSON and a promotion as `PromotionMaterial` JSON through
`/api/contexts`. A promotion may include exactly three `trace_items` to control the native Trace
copy. Upload optional visual references, then reference those records when creating a campaign
through `/api/campaigns`. Choose a finite count or leave continuous production enabled. The campaign freezes those inputs and the service creates one
uniquely identified variation at a time. Stopping a campaign prevents future variations without
erasing work already submitted or running. A known image-search, filesystem, or Appium
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

- network access to DDGS image search or `BRAVE_SEARCH_API_KEY` for the approved background-source search
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

For Trace capture, searched-background generation, and visual QA, the agent first inspects the local runtime. It
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

The capture job starts a Trace setup session, enters the request's three component titles through
Appium, then starts a request-bound export session. Trace writes a native transparent
`trace_components.png` plus `trace_components.manifest.json` into its App Group, and the worker
collects both.

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

The compositor crops the searched background photo to the requested canvas, normalizes the clean
iPhone system UI asset to alpha, resizes every layer consistently, and applies the fixed order.

## Contracts

### Component export

`trace.capture-job.v1` configures the Simulator, Appium-entered Trace component titles, and
component export.
The only supported capture target is `trace_components`.
For component-only capture, `background_image` may be omitted; the background remains
required by the separate composition job.
When supplied, `component_canvas` declares the expected native PNG dimensions (the
current iPhone 17 Pro/iOS 26.5 export is `1206×2622`) and the
manifest/artifact validator rejects a self-consistent export with the wrong canvas.
`reference_date` remains scene input for the planned Trace configuration and provenance contract.

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

`trace.marketing-composite-job.v2` requires background and Trace component paths. `iphone_ui` is
optional for lower-level composition commands and is included by `generate-one` from the packaged
clean system-UI asset.

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
    "iphone_ui": "inputs/iphone-ui.png"
  },
  "output_image": "outputs/final-marketing.png"
}
```

Input and output paths are resolved relative to the job file and may not collide or
escape that directory.

## Dynamic Cloudflare marketing loop

This repository includes an optional Cloudflare control plane for data-driven marketing accounts and
a portable pull bridge for workspace-backed execution. The first milestone is a durable pipeline, not content quality. Live publication
is disabled until the selected channel adapter passes a capability and readback probe.

Run the full contract locally without Cloudflare or external side effects:

```bash
trace-marketing simulate --account-id trace-kr --country KR --auto-approve
```

The command creates a shared registry plus a separate private-memory SQLite file per account, walks
the approval-gated state machine, simulates six observation samples, evaluates them, commits private
memory, and prints a `completed` run. Omit `--auto-approve` to stop first at
`awaiting_candidate_approval`; after candidate approval the run stops again at
`awaiting_human_approval` before publication.

Simulation accounts without a `workspace_id` execute research, candidate, capture, publication, and
metrics tasks inside the Cloudflare Workflow. Each task is explicitly labeled as simulated, stored
as a digest-backed R2 artifact, and indexed as succeeded in D1. The Workflow still pauses at both
human approval gates. This hosted path needs no always-on worker process.

The production Worker serves the public review workspace at the stable custom domain:

```text
https://workspace.borca.ai/
```

It has no workspace access ID or login step. Anyone with the URL can create or change an account,
its context, candidates, settings, and feedback, so do not put private material in this surface.
The account selector is a logical D1 silo, not an authorization boundary: every candidate, profile,
review event, and learned rule is scoped by `account_id`, but every visitor can select those accounts.
The workbench shows live counts for caption review, image work, and publication-ready results.

Each account owns a country, locale, timezone, morning/evening posting times, and an automatic daily
generation switch. The default KR account is ready after migration; additional accounts can be
created and edited in the workbench. Choose one account persona before `오늘 후보 4개 생성`;
Cloudflare Workers AI combines it with the packaged Trace principles, facts, voice, country reference
index, current account instruction, and that account/persona's repeated rejection rules. One batch
stores exactly four D1 candidates: two morning-slot and two evening-slot candidates. The Cron Trigger
also claims each enabled account at its next local morning time and runs the same generation path.
Profiles can be added, edited, or hidden from the same screen. Every candidate stores the selected
profile as an immutable snapshot, so later profile edits do not rewrite its generation provenance.

The hosted generation path defaults to `@cf/openai/gpt-oss-20b`; operators can override it with
`WORKSPACE_AI_MODEL`. The model runs only for `오늘 후보 4개 생성` and enabled scheduled generation.
Manual candidate entry, caption/image review, Mac/Appium capture, and the final `submitted` transition
do not call a language model.

Caption approval, native Mac/Appium capture, and image approval use the same review tab. `이미지 생성`
creates a revision-scoped D1 task. Once a non-revoked Mac has been enrolled, exactly one healthy worker
claims an expiring lease with its own revocable machine credential; a Cloudflare Queue token is not
installed on that Mac. It dynamically selects a booted or available iPhone Simulator, starts Appium
when installed but inactive, captures a fresh Trace component, composes the final PNG, and sends a
digest-backed result to the protected worker callback API. A background heartbeat remains active
during capture and renews the accepted lease for up to one hour from its original claim. The Worker
verifies lease ownership and the digest before storing the PNG in R2.
Offline workers leave the card visibly queued for a replacement; verified failures show a stable
code and a retry button. Installations with no registered broker worker retain the existing Queue
path for rollout and rollback compatibility. Image approval
ends at `submitted` (게시 준비 완료); it does not call Threads or another publishing API. Candidate
filters make review, image, ready, and rejected queues visible. Every hosted candidate, including
`submitted` candidates, has 수정 and 삭제 controls. Editing clears its previous approval and R2
image and returns it to `awaiting_review`; deletion removes both its D1 row and R2 object with an
optimistic revision guard. Approval is a one-click 5-point review. Rejection requires a 1–3 rating
and one or more structured tags; `기타` also requires a note. Three matching rejections for the same
account/persona become an account-scoped rule candidate and are injected into its next generation prompt.

The packaged context is also the local generator's fallback. `TRACE_AGENT_CONTEXT_DIR` remains the
explicit override, and an existing `<serve workspace>/context` still takes precedence; starting from
a directory without `context/` no longer leaves the default candidate generator empty.
The repository did not previously contain a production country/account/persona context library:
the JSON files under `appium/jobs/**/mock-contexts/` are capture fixtures, and PR #21 expected the
operator to supply the six markdown files in an untracked local `context/` directory. The packaged
context is now mixed, and `src/trace_capture/assets/context/ORIGIN.md` records which part is which.
`core/` and `references/KR/` hold the team's own collected and verified marketing documents for KR,
JP, and TW, copied byte-identically from the marketing context archive, while `markets/US.md`,
`markets/DE.md`, `markets/FR.md`, `markets/BR.md` and all 16 profiles remain unverified starter
guidance. Read a US, DE, FR, or BR candidate as a hypothesis, not as a researched result. The
context manifest owns country document, asset, reference, and profile paths; adding a packaged
country is a data change to the manifest/documents/profile JSON, not a Worker source edit. Country
documents are injected in full on every generation and the build rejects a country whose context
exceeds 48,000 bytes; reference bodies ship whole but only the records a persona names by id are
inlined, capped at five records and 24,000 bytes. Custom account-scoped profiles live in D1.
Generation fails with `409` when a selected country has no packaged documents rather than silently
falling back to KR.

Hosted context endpoints are intentionally public with the rest of this workspace:

| Endpoint | Behavior |
| --- | --- |
| `GET /api/accounts` | List enabled logical account silos and seed the default KR account |
| `POST /api/accounts` | Create an account with country, locale, timezone, two posting times, and optional automatic generation |
| `PATCH /api/accounts/{account_id}` | Edit account schedule and generation settings with `expected_revision` |
| `DELETE /api/accounts/{account_id}` | Disable an account without rewriting its historical records |
| `GET /api/context-countries` | List countries currently enabled by the packaged manifest; the UI builds its country selector from this response |
| `GET /api/context-profiles` | Seed packaged profiles if needed, then list enabled profiles for the selected account |
| `POST /api/context-profiles` | Add an account-scoped team profile |
| `PATCH /api/context-profiles/{profile_id}` | Edit a profile with `expected_revision` |
| `DELETE /api/context-profiles/{profile_id}` | Soft-hide a profile while preserving candidate snapshots |
| `GET /api/feedback-summary` | Return account/persona rejection counts, top tags, and 3+ occurrence rule candidates |
| `GET /api/workers/status` | Return only sanitized worker aliases and ready/busy/degraded/offline counts for the public status strip |

### Enroll or replace a hosted Mac worker

Prepare the target Mac with Xcode, an available iPhone Simulator, the Trace Debug build
(`com.corca.Trace`), Appium 3, and its XCUITest driver. The local doctor selects and boots the best
available Simulator before checking the installed Trace bundle, then reports each prerequisite:

```bash
trace-marketing worker doctor
```

The normal operator path starts at `https://workspace.borca.ai/`. In the Mac status strip choose
`Mac 연결 관리`, paste the Worker's `CONTROL_PLANE_TOKEN`, and use the protected manager to:

- see every registered Mac, recent heartbeat, Appium doctor summary, and current task;
- stop or resume new work for one Mac, or explicitly revoke only that machine credential; and
- create a short-lived one-time code and copy the exact commands for the target Mac.

This is not a workspace login. The public status strip still contains only sanitized aliases and
counts. The token exists only in JavaScript memory while the manager is open; it is never written to
HTML, URL parameters, cookies, `localStorage`, or `sessionStorage`, and closing or locking the manager
clears both the token and the displayed one-time code. The target Mac receives only the enrollment
code—not the control-plane token.

The same administration remains available by CLI. On an administrator machine, create a code that
expires after ten minutes. The control-plane token is used only for this admin call and is not given
to the target Mac:

```bash
export TRACE_MARKETING_CONTROL_TOKEN=...
trace-marketing worker create-enrollment \
  --url https://workspace.borca.ai \
  --name "Studio Mac"
```

Use the returned `enrollment_code` once on the target Mac, then install the managed per-user service:

```bash
trace-marketing worker enroll \
  --url https://workspace.borca.ai \
  --code '<one-time-code>'
trace-marketing worker install-service
trace-marketing worker status
```

Enrollment writes non-secret routing to
`$TRACE_AGENT_HOME/marketing-worker/config.json` and the one worker-scoped credential to the separate
mode-`0600` `credential.json`. It does not use macOS Keychain, a person's login, a fixed Simulator
UDID, the Cloudflare account ID, or a Cloudflare Queue token. The generated LaunchAgent plist uses an
absolute installed `trace-marketing` path plus that Mac's `PATH`, contains no credential, and keeps
the worker alive across logins/restarts. `TRACE_AGENT_DEVICE_UDID` remains an optional local override;
without it each task resolves the best available iPhone Simulator at execution time.

To replace a Mac without stopping the pipeline, create and enroll the new one first, wait until the
manager shows it as `작업 가능`, stop new work on the old Mac, and revoke the old connection after its
current task clears. The equivalent CLI commands are:

```bash
export TRACE_MARKETING_CONTROL_TOKEN=...
trace-marketing worker list --url https://workspace.borca.ai
trace-marketing worker set-state --state draining \
  --url https://workspace.borca.ai --worker-id '<old-worker-id>'
trace-marketing worker revoke \
  --url https://workspace.borca.ai --worker-id '<old-worker-id>'
```

Revocation clears only that machine's token hash and releases its unfinished lease; other worker
credentials and queued candidates are unchanged. An accepted task is first persisted in the local
SQLite inbox, and its terminal callback stays in a durable outbox until Cloudflare accepts it. The
two-minute claim becomes a renewable fifteen-minute execution window after local acceptance;
heartbeat renewal stops after one hour so a hung worker eventually releases the task.

### Legacy Queue bridge

The external Queue bridge remains for non-hosted Workflow accounts with a local `workspace_id` and
as a rollback path for hosted installations that have not enrolled a D1 worker. Its interactive
form reads these environment variables:

```bash
export CLOUDFLARE_ACCOUNT_ID=...
export TRACE_MARKETING_QUEUE_ID=...
export TRACE_MARKETING_QUEUE_TOKEN=...
export TRACE_MARKETING_CONTROL_PLANE_URL=https://...
export TRACE_MARKETING_WORKER_TOKEN=...
trace-marketing bridge
```

For an external supervisor, `bridge-configure` persists only non-secret routing and
`bridge-service` resolves Queue/callback credentials from the environment or an argv-safe secret
command:

```bash
trace-marketing bridge-configure --executor candidate-pipeline
trace-marketing bridge-service
```

The queue token needs Cloudflare Queues read/write permission because this legacy consumer also
acknowledges messages. Keep both values in the supervisor or external secret manager. The Worker
sends JSON text and the bridge also accepts the older base64 body during rollout.

A temporary claim, acknowledgement, callback, or heartbeat outage leaves local inbox/outbox work
durable and lets an expired broker lease move to another healthy Mac.

The bridge defaults to an artifact-only simulation executor for transport testing. To connect PR
#22's installed candidate journey, register the Cloudflare account with the local Trace
`workspace_id`, start the service from the workspace that owns its `context/` directory, and opt in
explicitly:

```bash
trace-marketing bridge --executor candidate-pipeline
```

This mode routes `hosted_workspace_capture_v1` tasks through the production Appium/XCUITest capture
runner. Other control-plane capture tasks keep the installed candidate-store journey. It does
**not** enable live publication or metrics: those task kinds remain visibly simulated. The operator reviews every generated caption in the existing workspace. When no caption
is left waiting, the bridge durably sends one candidate approval containing all accepted candidates
(or one rejection if all were rejected). After capture, approving every selected image similarly
sends publication approval. No separate API call is part of the normal review flow.

The control-plane approval endpoint remains available for explicit recovery or diagnostics:

```json
{"decision":"approved","phase":"candidates","candidate_ids":["<candidate-id>"]}
{"decision":"approved","phase":"publication"}
```

Both bodies can be posted to `POST /v1/runs/<run-id>/approval`. Omitting `phase` remains supported:
the control plane infers it from the run's current waiting state. Normal bridge-originated review
events instead use the Worker-token-only `/v1/review-events` boundary and a D1 receipt keyed by
`<run-id>:<phase>` so retrying the same event does not advance the Workflow twice. Hosted workspace
captures do not use the packaged fixture: the callback is accepted only when task, run, account,
candidate revision, kind, callback ID, PNG digest, and size all match.

Cloudflare deployment is under `cloudflare/`:

```bash
cd cloudflare
npm install
CF_D1_DATABASE_ID=<database-id> npm run config
npm run db:migrate:remote
npx wrangler secret put CONTROL_PLANE_TOKEN
npx wrangler secret put WORKER_CALLBACK_TOKEN
npm run deploy
npx wrangler queues consumer http add trace-marketing-tasks
```

The commands above are for an initial resource bootstrap or an explicit local recovery. Normal
production delivery is automatic: a merge to `main` that changes `cloudflare/**`, the canonical
workspace UI, or packaged context runs `.github/workflows/deploy-cloudflare.yml`, checks the Worker,
applies pending D1 migrations, deploys the merged revision, and verifies `/health`, the login-free
root workspace, its public session, and `https://workspace.borca.ai/health` in that order. Pull Requests run the same Worker check
without receiving deployment credentials or changing Cloudflare. GitHub Actions stores the deployment API
token as the `CLOUDFLARE_API_TOKEN` repository secret; account ID, D1 ID, and health URL are
repository variables. Existing Worker runtime secrets remain in Cloudflare and are not copied into
the repository or deployment log.

Create the D1 database, R2 bucket, and Queue named in `wrangler.template.jsonc` only for a new
environment. The generated config is ignored because it contains environment-specific resource IDs. See
[the full loop contract](docs/contracts/cloudflare-marketing-loop.md) for states, extension rules,
security boundaries, and the honest two-hour acceptance path.

The `0002_one_active_run_per_account.sql` migration prevents Cron and manual triggers from creating
overlapping non-terminal runs for one account. The merge workflow applies it before deploying this
revision; operators do not run it separately during the normal merge path.
Account registration currently accepts only `adapter_mode: "simulation"`; `"live"` fails closed
until a reviewed publication adapter and readback path are present.

## Sample asset status

The checked sample final image demonstrates the deterministic three-layer composition pipeline.
The context-driven path requires a current Trace debug build and performs a fresh Appium setup and
native export for every run.

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
| `src/trace_capture/marketing/` | Cloudflare task contract, legacy Queue/D1 broker transports, replaceable Mac worker lifecycle, durable inbox/outboxes, and local loop proof |
| `src/trace_capture/cli/` | `trace-ads`, `trace-marketing`, `trace-capture`, `trace-compose`, and `trace-run` boundaries |
| `cloudflare/` | Hosted account/worker registry, Workflow, Durable Object, D1 leases, legacy Queue, and R2 deployment |
| `appium/jobs/composite/` | Runnable sample job, layers, result, and final PNG |

Last reviewed: 2026-08-25
